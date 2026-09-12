"""Shared event binning and saved summaries; no plotting dependencies."""
import json
import numpy as np
from range_transform import to_range


def histogram_grid(profile, config):
    """Create time/range edges, including any shortened final time window."""
    duration, window = profile['duration_ms'], profile['window_ms']
    time_edges = np.arange(int(np.ceil(duration / window)) + 1) * window / 1000
    time_edges[-1] = duration / 1000
    width = profile.get('bin_width_ps', profile.get('expected_bin_width_ps'))
    lo, hi = np.ceil(np.asarray(config['delay_window_ns']) * 1000 / width)
    range_edges = to_range(np.arange(lo, hi + 1) * width, config)
    positive = np.flatnonzero(range_edges[:-1] > 0)
    if not len(positive): raise ValueError('No positive range bins in configured window')
    return time_edges, range_edges[positive[0]:]


def bin_events(times, delays, channels, config, time_edges, range_edges, counts, histogram, interval):
    """Accumulate both panels from the same events, using exclusive end bounds."""
    selected = (channels == config['channel']) & (times >= 0) & (times < time_edges[-1])
    times, ranges = times[selected], to_range(delays[selected], config)
    ti = np.searchsorted(time_edges, times, side='right') - 1
    ri = np.searchsorted(range_edges, ranges, side='right') - 1
    valid = (ri >= 0) & (ri < counts.shape[1])
    np.add.at(counts, (ti[valid], ri[valid]), 1)
    within = valid & (times >= interval[0]) & (times < interval[1])
    np.add.at(histogram, ri[within], 1)
    return int(selected.sum())


def capture_histograms(directory, profile, config, start_s=None, end_s=None):
    """Read each event file once; keep memory bounded for multi-hour captures."""
    cached = directory / 'histogram-summary.npz'
    if start_s is None and end_s is None and cached.exists():
        with np.load(cached) as data:
            if str(data['signature']) == json.dumps([profile, config], sort_keys=True):
                return (data['time_edges'], data['range_edges'], data['counts'], data['histogram'],
                        int(data['selected_total']), float(data['window']), tuple(data['interval'].tolist()))
    progress_path = directory / 'stream-progress.json'
    if progress_path.exists():
        progress = json.loads(progress_path.read_text())
        duration = progress['elapsed_s']
        paths = (directory / 'blocks' / f'{i:08d}.npz' for i in range(progress['blocks']))
    else:
        duration = profile['duration_ms'] / 1000
        paths = [directory / 'decoded-events.npz']
    selection = dict(config.get('histogram_interval', {}))
    if start_s is not None: selection['start_s'] = start_s
    if end_s is not None: selection['duration_s'] = end_s - selection.get('start_s', 0)
    summary = Summary(dict(profile, duration_ms=duration * 1000), dict(config, histogram_interval=selection))
    for path in paths:
        with np.load(path) as events: summary.add(events['elapsed_s'], events.get('delay_ps'), events['channels'])
    return summary.te, summary.re, summary.counts, summary.histogram, summary.total, summary.window, summary.interval


class Summary:
    """Bounded overview plus the exact configured interval, accumulated once."""
    def __init__(self, profile, config):
        self.profile, self.config = profile, config
        duration = profile['duration_ms'] / 1000
        requested = profile['window_ms']
        self.window = requested * max(1, int(np.ceil(duration * 1000 / requested / profile.get('preview_max_columns', 2000))))
        self.background = profile.get('kind') == 'background'
        if self.background:
            self.te = np.arange(int(np.ceil(duration*1000/self.window))+1)*self.window/1000
            self.te[-1], self.re = duration, np.array([])
        else: self.te, self.re = histogram_grid(dict(profile, window_ms=self.window), config)
        selection = {} if self.background else config.get('histogram_interval', {})
        start = selection.get('start_s', 0)
        end = start + selection.get('duration_s', 1)
        if not np.isfinite([duration, start, end]).all(): raise ValueError('Histogram interval must be finite')
        self.interval = (start, min(duration, end))
        if not 0 <= self.interval[0] < self.interval[1]: raise ValueError('Invalid configured histogram interval')
        self.counts = np.zeros((len(self.te)-1, 1 if self.background else len(self.re)-1), dtype=np.uint64)
        self.histogram = np.zeros(max(0, len(self.re)-1), dtype=np.uint64)
        self.total = 0

    def add(self, times, delays, channels):
        if self.background:
            selected = times[(channels == self.config['channel']) & (times >= 0) & (times < self.te[-1])]
            self.counts[:, 0] += np.histogram(selected, self.te)[0].astype(np.uint64)
            self.total += len(selected)
            return
        self.total += bin_events(times, delays, channels, self.config, self.te, self.re,
                                 self.counts, self.histogram, self.interval)

    def save(self, directory, duration):
        if duration <= self.interval[0]:
            return  # No exposure for the configured interval; raw events remain available.
        n = min(len(self.counts), int(np.searchsorted(self.te, duration, side='left')))
        edges = self.te[:n+1].copy()
        edges[-1] = duration
        path = directory / 'histogram-summary.npz'
        with path.with_suffix('.tmp').open('wb') as output:
            np.savez(output, time_edges=edges, range_edges=self.re, counts=self.counts[:n],
                     histogram=self.histogram, selected_total=self.total, window=self.window,
                     interval=[self.interval[0], min(self.interval[1], duration)],
                     signature=json.dumps([self.profile, self.config], sort_keys=True))
        path.with_suffix('.tmp').replace(path)
