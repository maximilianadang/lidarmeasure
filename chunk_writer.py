"""Bounded detector-event chunks, committed by size or elapsed wall time."""
import os
import time
import numpy as np


class ChunkWriter:
    def __init__(self, out, profile, progress):
        self.out, self.progress = out, progress
        self.capacity = profile.get('chunk_records', 1000000)
        self.seconds = profile.get('chunk_seconds', 5)
        self.data = dict(elapsed_s=np.empty(self.capacity), delay_ps=np.empty(self.capacity),
                         channels=np.empty(self.capacity, dtype=np.uint8))
        if profile.get('kind') == 'background': del self.data['delay_ps']
        self.used, self.last_write = 0, time.monotonic()

    def add(self, times, delays, channels):
        offset = 0
        while offset < len(times):
            n = min(len(times)-offset, self.capacity-self.used)
            values_by_key = dict(elapsed_s=times, delay_ps=delays, channels=channels)
            for key, buffer in self.data.items():
                values = values_by_key[key]
                buffer[self.used:self.used+n] = values[offset:offset+n]
            self.used += n
            offset += n
            if self.used == self.capacity: self.flush()
        self.flush_due()

    def flush_due(self):
        if time.monotonic()-self.last_write >= self.seconds: self.flush()

    def flush(self):
        if not self.used: return
        path = self.out / 'blocks' / f'{self.progress["blocks"]:08d}.npz'
        with path.with_suffix('.tmp').open('wb') as output:
            np.savez(output, **{key: value[:self.used] for key, value in self.data.items()})
            output.flush()
            os.fsync(output.fileno())
        path.with_suffix('.tmp').replace(path)
        self.progress['blocks'] += 1
        self.progress['committed_records'] += self.used
        self.used, self.last_write = 0, time.monotonic()
