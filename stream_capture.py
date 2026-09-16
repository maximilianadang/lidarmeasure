"""Bounded block acquisition with recoverable, atomic event files."""
import json
import os
import shutil
import signal
import time

import numpy as np
from stream_processing import T2Delays, Processor, NativeProcessor


def atomic_json(path, value, sync=True):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        if sync: os.fsync(handle.fileno())
    temporary.replace(path)


class OverrunMonitor:
    """Read only new vendor-log bytes; buffer loss invalidates the recording immediately."""
    def __init__(self, out): self.directory, self.offsets = out / 'snapi', {}

    def check(self):
        for path in self.directory.rglob('*.log'):
            with path.open(errors='replace') as log:
                log.seek(self.offsets.get(path, 0))
                text = log.read()
                self.offsets[path] = log.tell()
            if any(term in text.lower() for term in ('buffer overrun', 'buffer full', 'cnts_dropped')):
                raise RuntimeError('snAPI buffer overflow: events were dropped; recording stopped, saved data will be plotted as incomplete')


def stream_events(sn, measurement, settings, profile, out, rates, check_health=None):
    background = profile.get('kind') == 'background'
    t2 = profile.get('mode', 'T3') == 'T2'
    resolution = float(profile['bin_width_ps']) if t2 else float(sn.deviceConfig['Resolution'])
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
    processor_type = NativeProcessor if os.environ.get('LIDAR_RUNTIME_DIR') else Processor
    processor = processor_type(out, settings, profile, rates, float(sn.deviceConfig['BaseResolution']) if profile.get('raw_t2') else resolution, progress.copy())
    processing_keys = ('blocks', 'committed_records', 'records', 'batches', 'elapsed_s', 'channel_counts',
                       'unreferenced_events', 'motion_ready', 'buffered_records', 'processing_timings', 'summary_deferred',
                       'raw_batches_saved', 'processing_pending_batches', 'processing_error')
    def update_processing(result):
        if 'processing_error' in result and 'processing_error' not in progress:
            print(f'PROCESSING ERROR: {result["processing_error"]}; raw recording continues', flush=True)
        progress.update({key: result[key] for key in processing_keys if key in result})
    atomic_json(out / 'stream-progress.json', progress)
    stopping = False
    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True
    previous = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()
    last_read = started
    last_tick = -1
    last_flush = started
    overruns = OverrunMonitor(out)
    stop_reason = None
    try:
        open_ended = bool(settings.get('motion')) and check_health is not None
        requested = 'until motion completes' if open_ended else f'{profile["duration_ms"] / 1000:g} s requested'
        print(f'STARTING acquisition: {requested}; waiting for hardware and buffered events', flush=True)
        if not measurement.startBlock(acqTime=0 if open_ended else profile['duration_ms'], size=capacity, savePTU=profile['save_ptu']):
            raise RuntimeError('Continuous block acquisition failed')
        while True:
            overruns.check()
            if isinstance(processor, NativeProcessor): update_processing(processor.status())
            motion_done = check_health() if check_health is not None and not stopping and stop_reason is None else False
            if (stopping or motion_done) and stop_reason is None:
                stop_reason = 'user' if stopping else 'motion_complete'
                progress.update(motion_ready=False, status='stopping')
                atomic_json(out / 'stream-progress.json', progress, sync=False)
                measurement.stopMeasure()
            finished = measurement.isFinished()
            read_started = time.monotonic()
            block = measurement.getBlock()
            packed, channels = (block, np.empty(0, dtype=np.uint8)) if profile.get('raw_t2') else block
            # snAPI owns these views until the next getBlock; save them before reuse.
            progress['max_get_block_s'] = max(progress.get('max_get_block_s', 0), time.monotonic()-read_started)
            now = time.monotonic()
            progress['max_read_interval_s'] = max(progress['max_read_interval_s'], now-last_read)
            last_read = now
            if len(packed) >= capacity: raise RuntimeError('Block reached capacity; possible lost events')
            if len(packed):
                progress['input_records'] += len(packed)
                progress['max_block_records'] = max(progress['max_block_records'], len(packed))
                if not background and not t2:
                    delay = measurement.dTime_T3(packed).astype(np.float64) * resolution
                    packed = np.array([measurement.nSync_T3(packed).astype(np.float64) / rates[0] + delay * 1e-12, delay])
                processing_started = time.monotonic()
                result = processor.process(packed, channels, finished)
                progress['max_processing_handoff_s'] = max(progress.get('max_processing_handoff_s', 0), time.monotonic()-processing_started)
                update_processing(result)
            if not isinstance(processor, NativeProcessor) and not len(packed) and progress.get('buffered_records') and now-last_flush >= profile.get('chunk_seconds', 5):
                update_processing(processor.flush())
                last_flush = now
            # Read once more after isFinished becomes true; this iteration is that final drain.
            progress['wall_elapsed_s'] = now-started
            if finished:
                if stop_reason is None: progress['elapsed_s'] = profile['duration_ms'] / 1000
                else:
                    progress['elapsed_s'] = max(progress['elapsed_s'], now-started)
                duration = progress['elapsed_s']
                progress.update(status='processing', motion_ready=False)
                atomic_json(out / 'stream-progress.json', progress, sync=False)
                print('ACQUISITION STOPPED; finishing processing of saved batches', flush=True)
                update_processing(processor.flush(duration))
                progress['elapsed_s'] = max(duration, progress['elapsed_s'])
                progress['motion_ready'] = False
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
                print(f'STREAM {progress.get("raw_batches_saved", 0)} raw batches saved; {progress.get("processing_pending_batches", 0)} awaiting processing; {progress["committed_records"]} detector events saved; {progress.get("buffered_records", 0)} buffered; latest detector timestamp {progress["elapsed_s"]:.3f} s; {now-started:.1f} s wall time including startup', flush=True)
                last_tick = tick
            time.sleep(max(0, interval - (time.monotonic()-now)))
    except BaseException as error:
        try: measurement.stopMeasure()
        except Exception as stop_error: progress['stop_error'] = str(stop_error)
        try:
            update_processing(processor.flush())
        except Exception as disk_error: progress['chunk_write_error'] = str(disk_error)
        progress['uncommitted_records'] = progress.get('buffered_records', 0)
        progress.update(status='failed', error=f'{type(error).__name__}: {error}')
        atomic_json(out / 'stream-progress.json', progress)
        raise
    finally:
        processor.close()
        for sig, handler in previous.items(): signal.signal(sig, handler)
    return dict(bin_width_ps=resolution, records=progress['records'], channel_counts=progress['channel_counts'],
                sync_rate_for_timestamps_hz=float(rates[0]), sync_rate_source=progress['sync_rate_source'],
                stop_requested=stop_reason is not None, motion_complete=stop_reason == 'motion_complete', stream=progress)
