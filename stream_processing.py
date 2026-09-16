"""Native event processing from an on-disk acquisition spool; RAM never queues batches."""
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from chunk_writer import ChunkWriter
from histogram_data import Summary
from live_preview import publish
from native_process import native_env


class T2Delays:
    def __init__(self):
        self.last_sync = None
        self.unreferenced = 0

    def decode(self, times, channels):
        if not len(times): return np.array([], dtype=float), np.array([], dtype=float), channels[:0]
        sync = times[channels == 0]
        if self.last_sync is not None: sync = np.concatenate((np.array([self.last_sync], dtype=times.dtype), sync))
        selected = (channels >= 1) & (channels <= 4)
        photons, photon_channels = times[selected], channels[selected]
        # Place each SYNC at the first equal timestamp, then propagate it forward.
        # This preserves simultaneous-event semantics regardless of channel ordering.
        references = np.full(len(times), self.last_sync if self.last_sync is not None else 0, dtype=times.dtype)
        if len(sync): references[np.searchsorted(times, sync, side='left').clip(0, max(0, len(times)-1))] = sync
        np.maximum.accumulate(references, out=references)
        valid = np.ones(len(photons), dtype=bool) if self.last_sync is not None else (photons >= sync[0] if len(sync) else np.zeros(len(photons), dtype=bool))
        self.unreferenced += int((~valid).sum())
        delays = photons[valid] - references[selected][valid]
        if len(sync): self.last_sync = sync[-1]
        return photons[valid].astype(np.float64)*1e-12, delays.astype(np.float64), photon_channels[valid]



class Processor:
    def __init__(self, out, settings, profile, rates, resolution, progress):
        self.out, self.settings, self.profile = out, settings, profile
        self.rates, self.resolution, self.progress = rates, resolution, progress
        self.decoder = T2Delays()
        self.overflow = 0
        self.writer = ChunkWriter(out, profile, progress)
        self.progress['summary_deferred'] = sum(rates[1:]) > 1000000
        self.summary = Summary(profile, settings['plot']) if 'plot' in settings and not self.progress['summary_deferred'] else None
        self.timings = {}
        self.last_preview = 0
        self.pool = ThreadPoolExecutor(max_workers=3)

    def process(self, packed, channels, finished):
        p, profile = self.progress, self.profile
        background = profile.get('kind') == 'background'
        started = time.perf_counter()
        if profile.get('raw_t2'):
            # MultiHarp T2 V2 format: 25-bit tags, 6-bit channel, special flag.
            special, channel = (packed >> 31).astype(bool), (packed >> 25) & 63
            tags = (packed & 0x1ffffff).astype(np.uint64)
            wraps = np.where(special & (channel == 63), np.maximum(tags, 1), 0)
            correction = np.cumsum(wraps, dtype=np.uint64) * (1 << 25) + self.overflow
            if len(correction): self.overflow = int(correction[-1])
            keep = (~special & (channel < 4)) | (special & (channel == 0))
            channels = np.where(special[keep], 0, channel[keep]+1).astype(np.uint8)
            packed = ((tags[keep]+correction[keep]) * int(self.resolution)).astype(np.uint64)
        if background:
            selected = (channels >= 1) & (channels <= 4)
            elapsed, delay, channels = packed[selected].astype(np.float64)*1e-12, None, channels[selected]
        elif profile.get('mode', 'T3') == 'T2':
            elapsed, delay, channels = self.decoder.decode(packed, channels)
            p['unreferenced_events'] = self.decoder.unreferenced
        else:
            # The caller supplies decoded T3 elapsed/delay rows.
            elapsed, delay = packed
        self.timing('decode', started)
        if len(elapsed) and len(channels) > 1000000 * max(float(elapsed.max())-p['elapsed_s'], 1e-9):
            self.summary = None
            p['summary_deferred'] = True
        def measured(name, operation):
            start = time.perf_counter()
            operation()
            self.timing(name, start)
        jobs = [self.pool.submit(measured, 'write', lambda: self.writer.add(elapsed, delay, channels))]
        if self.summary is not None:
            jobs.append(self.pool.submit(measured, 'histogram', lambda: self.summary.add(elapsed, delay, channels)))
        def preview():
            try: publish(self.out, delay, channels, profile, self.settings['plot']['channel'], p['batches'], elapsed)
            except OSError as error: print(f'PREVIEW could not update: {error}', file=sys.stderr, flush=True)
        if 'plot' in self.settings and (finished or time.monotonic()-self.last_preview >= self.settings.get('preview', {}).get('refresh_ms', 500)/1000):
            self.last_preview = time.monotonic()
            jobs.append(self.pool.submit(measured, 'preview', preview))
        errors = []
        for job in jobs:
            try: job.result()
            except Exception as error: errors.append(error)
        if errors: raise errors[0]
        p['motion_ready'] = not finished and bool(len(elapsed)) and (background or bool(np.any(delay < 1e12 / self.rates[0])))
        p['batches'] += 1
        p['records'] += len(channels)
        p['elapsed_s'] = max(p['elapsed_s'], float(elapsed.max()) if len(elapsed) else 0)
        counts = np.bincount(channels, minlength=5)
        for i in range(5): p['channel_counts'][i] += int(counts[i])
        return self.status()

    def timing(self, name, started):
        duration = time.perf_counter() - started
        entry = self.timings.setdefault(name, {'total_s': 0, 'max_s': 0})
        entry['total_s'] += duration
        entry['max_s'] = max(entry['max_s'], duration)

    def status(self):
        return dict(self.progress, buffered_records=self.writer.used, processing_timings=self.timings)

    def close(self): self.pool.shutdown(wait=True)

    def flush(self, duration=None):
        self.writer.flush()
        if duration is not None and self.summary is not None and duration <= self.summary.te[-1]:
            self.summary.save(self.out, duration)
        return self.status()


class NativeProcessor:
    """Save first, process independently. Only finalization waits for the worker."""
    def __init__(self, out, settings, profile, rates, resolution, progress):
        self.out, self.submitted = out, 0
        self.spool = out / 'raw-batches'
        self.spool.mkdir()
        self.result = dict(progress, motion_ready=False, buffered_records=0,
                           processing_timings={}, summary_deferred=False)
        config = dict(out=str(out), settings=settings, profile=profile, rates=rates,
                      resolution=resolution, progress=progress)
        (self.spool / 'config.json').write_text(json.dumps(config))
        self.log = (out / 'logs/processing.log').open('w')
        self.worker = subprocess.Popen(['/usr/bin/python3', '-I', '-u', str(Path(__file__).resolve()), str(self.spool)],
                                       stderr=self.log, stdout=self.log, env=native_env())

    def status(self):
        path = self.spool / 'progress.json'
        if path.exists(): self.result = json.loads(path.read_text())
        result = dict(self.result, raw_batches_saved=self.submitted,
                      processing_pending_batches=self.submitted-self.result['batches'])
        if self.worker.poll() not in (None, 0):
            result['processing_error'] = 'Processing worker failed; raw batches preserved; see logs/processing.log'
        return result

    def process(self, packed, channels, finished):
        # snAPI's borrowed arrays are fully written before the next getBlock call.
        path = self.spool / f'{self.submitted:08d}.npz'
        with path.with_suffix('.tmp').open('wb') as handle:
            np.savez(handle, packed=packed, channels=channels)
            handle.flush()
            os.fsync(handle.fileno())
        path.with_suffix('.tmp').replace(path)
        self.submitted += 1
        return self.status()

    def flush(self, duration=None):
        # Called only once acquisition has stopped; decoding cannot delay further reads.
        path = self.spool / 'finish.json'
        path.with_suffix('.tmp').write_text(json.dumps(dict(batches=self.submitted, duration=duration)))
        path.with_suffix('.tmp').replace(path)
        self.worker.wait()
        result = self.status()
        if self.worker.returncode: raise RuntimeError(result['processing_error'])
        if result['batches'] != self.submitted: raise RuntimeError('Processing did not consume all saved batches')
        return result

    def close(self):
        if self.worker.poll() is None:
            self.worker.terminate()
            try: self.worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait()
        self.log.close()
        # On failure, retain the checkpoint and pending raw batches for recovery.
        if self.worker.returncode == 0 and (self.spool / 'finish.json').exists(): shutil.rmtree(self.spool)


def main():
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    spool = Path(sys.argv[1])
    config = json.loads((spool / 'config.json').read_text())
    config['out'] = Path(config['out'])
    checkpoint = spool / 'checkpoint.json'
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else None
    if saved: config['progress'] = saved['progress']
    processor = Processor(**config)
    if saved:
        processor.overflow = saved['overflow']
        processor.decoder.last_sync = saved['last_sync']
        processor.decoder.unreferenced = saved['progress']['unreferenced_events']
        processor.summary = None  # Rebuild summaries from all decoded blocks after recovery.
        processor.progress['summary_deferred'] = True
    index = committed = processor.progress['batches']
    last_commit = time.monotonic()
    def commit(duration=None):
        nonlocal committed, last_commit
        result = processor.flush(duration)
        sync_directory(config['out'] / 'blocks')
        write_status(spool, dict(progress=result, overflow=processor.overflow,
                                last_sync=None if processor.decoder.last_sync is None else int(processor.decoder.last_sync)), 'checkpoint', sync=True)
        # The checkpoint and decoded chunks are durable before raw inputs are removed.
        for i in range(committed, index): (spool / f'{i:08d}.npz').unlink(missing_ok=True)
        committed, last_commit = index, time.monotonic()
    try:
        while True:
            path = spool / f'{index:08d}.npz'
            if path.exists():
                with np.load(path) as data:
                    processor.process(data['packed'], data['channels'], False)
                index += 1
            elif (spool / 'finish.json').exists():
                finish = json.loads((spool / 'finish.json').read_text())
                if index < finish['batches']:
                    if path.exists(): continue  # The producer committed it after our first check.
                    raise RuntimeError(f'Missing saved raw batch {index}')
                commit(finish['duration'])
                break
            else:
                time.sleep(.02)
                continue
            if time.monotonic()-last_commit >= config['profile'].get('chunk_seconds', 5): commit()
            write_status(spool, processor.status())
    finally:
        processor.close()
        processor.writer.flush()
        write_status(spool, processor.status())


def write_status(spool, result, name='progress', sync=False):
    path = spool / f'{name}.json'
    with path.with_suffix('.tmp').open('w') as handle:
        json.dump(result, handle)
        handle.flush()
        if sync: os.fsync(handle.fileno())
    path.with_suffix('.tmp').replace(path)
    if sync: sync_directory(spool)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


if __name__ == '__main__': main()
