"""Bounded block acquisition with recoverable, atomic event files."""
import json
import os
import shutil
import signal
import time

import numpy as np


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def stream_events(sn, measurement, settings, profile, out, rates, check_health=None):
    resolution = float(sn.deviceConfig['Resolution'])
    if resolution != profile['expected_bin_width_ps']:
        raise RuntimeError(f'Unexpected resolution: {resolution} ps')
    capacity = profile['max_records']
    interval = profile['poll_ms'] / 1000
    if sum(rates[1:]) * interval * 2 >= capacity:
        raise RuntimeError('Insufficient block capacity for the measured rate and poll interval')
    reserve = profile['min_free_disk_gb'] * 1e9
    if shutil.disk_usage(out).free < reserve:
        raise RuntimeError('Free disk space is below min_free_disk_gb')
    blocks = out / 'blocks'
    blocks.mkdir()
    progress = dict(format_version=1, status='acquiring', blocks=0, records=0,
                    channel_counts=[0] * 5, elapsed_s=0.0, bin_width_ps=resolution,
                    sync_rate_for_timestamps_hz=float(rates[0]),
                    sync_rate_source='pre-capture SYNC rate (fixed throughout run)',
                    max_block_records=0, max_read_interval_s=0.0)
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
        if not measurement.startBlock(acqTime=profile['duration_ms'], size=capacity, savePTU=profile['save_ptu']):
            raise RuntimeError('Continuous block acquisition failed')
        while True:
            if stopping and stop_reason is None:
                stop_reason = 'user'
                measurement.stopMeasure()
            finished = measurement.isFinished()
            packed, channels = (np.array(x, copy=True) for x in measurement.getBlock())
            now = time.monotonic()
            progress['max_read_interval_s'] = max(progress['max_read_interval_s'], now-last_read)
            last_read = now
            if len(packed) >= capacity:
                raise RuntimeError('Block reached capacity; possible lost events')
            if len(packed):
                delay = measurement.dTime_T3(packed).astype(np.float64) * resolution
                elapsed = measurement.nSync_T3(packed).astype(np.float64) / rates[0] + delay * 1e-12
                path = blocks / f'{progress["blocks"]:08d}.npz'
                temporary = path.with_suffix('.tmp')
                with temporary.open('wb') as handle:
                    np.savez(handle, elapsed_s=elapsed, delay_ps=delay, channels=channels)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(path)
                progress['blocks'] += 1
                progress['records'] += len(packed)
                progress['max_block_records'] = max(progress['max_block_records'], len(packed))
                progress['elapsed_s'] = max(progress['elapsed_s'], float(elapsed.max()))
                counts = np.bincount(channels, minlength=5)
                for i in range(5):
                    progress['channel_counts'][i] += int(counts[i])
            if check_health is not None:
                check_health()
            # Read once more after isFinished becomes true; this iteration is that final drain.
            progress['wall_elapsed_s'] = now-started
            if finished:
                if stop_reason is None:
                    progress['elapsed_s'] = profile['duration_ms'] / 1000
                else:
                    progress['elapsed_s'] = max(progress['elapsed_s'], now-started)
                progress['status'] = 'stopped' if stop_reason else 'acquired'
                progress['stop_reason'] = stop_reason or 'duration'
                atomic_json(out / 'stream-progress.json', progress)
                break
            atomic_json(out / 'stream-progress.json', progress)
            if shutil.disk_usage(out).free < reserve:
                raise RuntimeError('Disk reserve reached; committed blocks are preserved')
            tick = int((now-started) // 60)
            if tick != last_tick:
                print(f'STREAM {progress["records"]} events; {now-started:.1f} s; {blocks}', flush=True)
                last_tick = tick
            time.sleep(interval)
    except BaseException as error:
        progress.update(status='failed', error=f'{type(error).__name__}: {error}')
        atomic_json(out / 'stream-progress.json', progress)
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return dict(bin_width_ps=resolution, records=progress['records'], channel_counts=progress['channel_counts'],
                sync_rate_for_timestamps_hz=float(rates[0]), sync_rate_source=progress['sync_rate_source'],
                stop_requested=stop_reason == 'user', stream=progress)
