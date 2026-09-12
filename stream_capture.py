"""Bounded block acquisition with recoverable, atomic event files."""
import json
import os
import shutil
import signal
import time

import numpy as np
from live_preview import publish
from histogram_data import Summary
from chunk_writer import ChunkWriter


def atomic_json(path, value, sync=True):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        if sync: os.fsync(handle.fileno())
    temporary.replace(path)


class T2Delays:
    """Associate detector events with their preceding SYNC across block boundaries."""
    def __init__(self):
        self.last_sync = None
        self.unreferenced = 0

    def decode(self, times, channels):
        sync = times[channels == 0]
        if self.last_sync is not None: sync = np.concatenate((np.array([self.last_sync], dtype=times.dtype), sync))
        selected = (channels >= 1) & (channels <= 4)
        photons, photon_channels = times[selected], channels[selected]
        index = np.searchsorted(sync, photons, side='right') - 1
        valid = index >= 0
        self.unreferenced += int((~valid).sum())
        if len(sync): self.last_sync = sync[-1]
        return photons[valid].astype(np.float64) * 1e-12, (photons[valid] - sync[index[valid]]).astype(np.float64), photon_channels[valid]


def stream_events(sn, measurement, settings, profile, out, rates, check_health=None):
    background = profile.get('kind') == 'background'
    t2 = profile.get('mode', 'T3') == 'T2'
    resolution = float(profile['bin_width_ps']) if t2 else float(sn.deviceConfig['Resolution'])
    decoder = T2Delays() if t2 else None
    if not t2 and resolution != profile['expected_bin_width_ps']:
        raise RuntimeError(f'Unexpected resolution: {resolution} ps')
    capacity = profile['max_records']
    interval = profile['poll_ms'] / 1000
    if sum(rates if t2 else rates[1:]) * interval * 2 >= capacity:
        raise RuntimeError('Insufficient block capacity for the measured rate and poll interval')
    reserve = profile['min_free_disk_gb'] * 1e9
    if shutil.disk_usage(out).free < reserve: raise RuntimeError('Free disk space is below min_free_disk_gb')
    (out / 'blocks').mkdir()
    progress = dict(format_version=1, status='acquiring', blocks=0, records=0, committed_records=0, batches=0, input_records=0, unreferenced_events=0,
                    channel_counts=[0] * 5, elapsed_s=0.0, bin_width_ps=resolution,
                    sync_rate_for_timestamps_hz=float(rates[0]),
                    sync_rate_source='T2 absolute picosecond timestamps' if t2 else 'pre-capture SYNC rate (fixed throughout run)',
                    max_block_records=0, max_read_interval_s=0.0)
    writer = ChunkWriter(out, profile, progress)
    summary = Summary(profile, settings['plot']) if 'plot' in settings else None
    atomic_json(out / 'stream-progress.json', progress)
    stopping = False
    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True
    previous = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()
    last_read = started
    last_tick = -1
    stop_reason = None
    try:
        requested = 'until motion completes' if settings.get('motion') else f'{profile["duration_ms"] / 1000:g} s requested'
        print(f'STARTING acquisition: {requested}; waiting for hardware and buffered events', flush=True)
        if not measurement.startBlock(acqTime=0 if settings.get('motion') else profile['duration_ms'], size=capacity, savePTU=profile['save_ptu']):
            raise RuntimeError('Continuous block acquisition failed')
        while True:
            motion_done = check_health() if check_health is not None else False
            if (stopping or motion_done) and stop_reason is None:
                stop_reason = 'user' if stopping else 'motion_complete'
                progress.update(motion_ready=False, status='stopping')
                atomic_json(out / 'stream-progress.json', progress, sync=False)
                measurement.stopMeasure()
            finished = measurement.isFinished()
            packed, channels = (np.array(x, copy=True) for x in measurement.getBlock())
            now = time.monotonic()
            progress['max_read_interval_s'] = max(progress['max_read_interval_s'], now-last_read)
            last_read = now
            if len(packed) >= capacity: raise RuntimeError('Block reached capacity; possible lost events')
            if len(packed):
                progress['input_records'] += len(packed)
                progress['max_block_records'] = max(progress['max_block_records'], len(packed))
                if background:
                    selected = (channels >= 1) & (channels <= 4)
                    elapsed, delay, channels = packed[selected].astype(np.float64)*1e-12, None, channels[selected]
                elif t2:
                    elapsed, delay, channels = decoder.decode(packed, channels)
                    progress['unreferenced_events'] = decoder.unreferenced
                else:
                    delay = measurement.dTime_T3(packed).astype(np.float64) * resolution
                    elapsed = measurement.nSync_T3(packed).astype(np.float64) / rates[0] + delay * 1e-12
                writer.add(elapsed, delay, channels)
                if summary is not None: summary.add(elapsed, delay, channels)
                if 'plot' in settings:
                    try:
                        publish(out, delay, channels, profile, settings['plot']['channel'], progress['batches'], elapsed)
                    except OSError as error: print(f'PREVIEW could not update: {error}', flush=True)
                progress['motion_ready'] = not finished and bool(len(elapsed)) and (background or bool(np.any(delay < 1e12 / rates[0])))
                progress['batches'] += 1
                progress['records'] += len(channels)
                progress['elapsed_s'] = max(progress['elapsed_s'], float(elapsed.max()) if len(elapsed) else 0)
                counts = np.bincount(channels, minlength=5)
                for i in range(5): progress['channel_counts'][i] += int(counts[i])
            writer.flush_due()
            # Read once more after isFinished becomes true; this iteration is that final drain.
            progress['wall_elapsed_s'] = now-started
            if finished:
                writer.flush()
                if stop_reason is None: progress['elapsed_s'] = profile['duration_ms'] / 1000
                else:
                    progress['elapsed_s'] = max(progress['elapsed_s'], now-started)
                if summary is not None and progress['elapsed_s'] <= summary.te[-1]: summary.save(out, progress['elapsed_s'])
                progress['status'] = 'stopped' if stop_reason else 'acquired'
                progress['stop_reason'] = stop_reason or 'duration'
                atomic_json(out / 'stream-progress.json', progress)
                print(f'ACQUISITION FINISHED ({progress["stop_reason"]}): {progress["records"]} detector events saved', flush=True)
                break
            # Progress replacement is cheap; reserve fsync for committed event chunks.
            atomic_json(out / 'stream-progress.json', progress, sync=False)
            if shutil.disk_usage(out).free < reserve:
                raise RuntimeError('Disk reserve reached; committed blocks are preserved')
            tick = int(now-started)
            if tick != last_tick:
                print(f'STREAM {progress["committed_records"]} detector events saved; {writer.used} buffered; latest detector timestamp {progress["elapsed_s"]:.3f} s; {now-started:.1f} s wall time including startup', flush=True)
                last_tick = tick
            time.sleep(interval)
    except BaseException as error:
        try:
            writer.flush()
        except OSError as disk_error: progress['chunk_write_error'] = str(disk_error)
        progress['uncommitted_records'] = writer.used
        progress.update(status='failed', error=f'{type(error).__name__}: {error}')
        atomic_json(out / 'stream-progress.json', progress)
        raise
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)
    return dict(bin_width_ps=resolution, records=progress['records'], channel_counts=progress['channel_counts'],
                sync_rate_for_timestamps_hz=float(rates[0]), sync_rate_source=progress['sync_rate_source'],
                stop_requested=stop_reason is not None, motion_complete=stop_reason == 'motion_complete', stream=progress)
