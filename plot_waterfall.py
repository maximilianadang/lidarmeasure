"""Build a waterfall from a saved continuous T3 recording without hardware."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.colors import LogNorm
from range_transform import to_range, weight_counts


def histogram_windows(elapsed_s, delay_ps, channels, profile, config):
    duration = profile['duration_ms'] / 1000
    # Include a shortened last window explicitly; never reset the acquisition clock.
    time_edges = np.arange((profile["duration_ms"] + profile["window_ms"] - 1) // profile["window_ms"] + 1, dtype=np.int64) * profile["window_ms"] / 1000
    time_edges[-1] = duration
    width = profile['expected_bin_width_ps']
    lo, hi = config['delay_window_ns']
    delay_edges = np.arange(np.ceil(lo * 1000 / width), np.ceil(hi * 1000 / width) + 1) * width
    mask = (channels == config['channel']) & (elapsed_s >= 0) & (elapsed_s < duration) & (delay_ps >= delay_edges[0]) & (delay_ps < delay_edges[-1])
    counts, _, _ = np.histogram2d(elapsed_s[mask], delay_ps[mask], bins=(time_edges, delay_edges))
    range_edges = to_range(delay_edges, config)
    # Match the static plot's bin-coordinate convention; exclude nonpositive bins.
    keep = range_edges[:-1] > 0
    if not keep.any():
        raise ValueError('No positive range bins in configured window')
    first = int(np.flatnonzero(keep)[0])
    ranges = range_edges[first:-1]
    counts = counts[:, first:].astype(np.uint64)
    return time_edges, range_edges[first:], counts, weight_counts(counts, ranges)


def stream_histogram(directory, profile, config, start_s=0, end_s=None):
    progress = json.loads((directory / 'stream-progress.json').read_text())
    end_s = progress['elapsed_s'] if end_s is None else min(end_s, progress['elapsed_s'])
    if not 0 <= start_s < end_s:
        raise ValueError('Requested interval has no recorded exposure')
    # Limit overview columns while retaining exact events for later detailed plots.
    duration_ms = int(np.ceil((end_s-start_s)*1000))
    requested = profile['window_ms']
    window = requested * max(1, int(np.ceil(duration_ms / requested / profile['preview_max_columns'])))
    view = dict(profile, duration_ms=duration_ms, window_ms=window)
    te, re, counts, _ = histogram_windows(np.array([]), np.array([]), np.array([]), view, config)
    te += start_s
    te[-1] = end_s
    selected_total = 0
    # Enumerate committed paths lazily; never load the entire recording.
    for index in range(progress['blocks']):
        path = directory / 'blocks' / f'{index:08d}.npz'
        with np.load(path) as block:
            times, delays, channels = block['elapsed_s'], block['delay_ps'], block['channels']
            selected = (channels == config['channel']) & (times >= start_s) & (times < end_s)
            selected_total += int(selected.sum())
            times, ranges = times[selected], to_range(delays[selected], config)
            ti = np.searchsorted(te, times, side='right')-1
            ri = np.searchsorted(re, ranges, side='right')-1
            valid = (ti >= 0) & (ti < counts.shape[0]) & (ri >= 0) & (ri < counts.shape[1])
            np.add.at(counts, (ti[valid], ri[valid]), 1)
    return te, re, counts, weight_counts(counts, re[:-1]), selected_total, window


def plot_capture(directory, start_s=0, end_s=None):
    directory = Path(directory)
    settings = json.loads((directory / 'lidar-settings.json').read_text())
    profile, config = settings['profiles']['waterfall'], settings['plot']
    if (directory / 'stream-progress.json').exists():
        time_edges, range_edges, counts, weighted, selected_total, effective_window = stream_histogram(
            directory, profile, config, start_s, end_s)
    else:
        with np.load(directory / 'decoded-events.npz') as events:
            time_edges, range_edges, counts, weighted = histogram_windows(
                events['elapsed_s'], events['delay_ps'], events['channels'], profile, config)
            selected_total = int(np.count_nonzero(events['channels'] == config['channel']))
        effective_window = profile['window_ms']
    np.savez(directory / 'waterfall.npz', time_edges_s=time_edges, range_edges_m=range_edges,
             counts=counts, counts_over_r4=weighted)
    positive = weighted[weighted > 0]
    norm = LogNorm(vmin=float(positive.min()), vmax=max(float(positive.max()), float(positive.min()) * 1.01)) if len(positive) else None
    fig, ax = plt.subplots(figsize=(11, 6), constrained_layout=True)
    mesh = ax.pcolormesh(time_edges, range_edges, np.ma.masked_less_equal(weighted.T, 0),
                         shading='flat', cmap='inferno', norm=norm)
    ax.set_facecolor('#080510')
    fig.colorbar(mesh, ax=ax, label='Photon counts / R⁴ per time/range bin (counts m⁻⁴; log color)' if norm else 'Photon counts / R⁴')
    ax.set(xlabel='Elapsed acquisition time (s)', ylabel='Range (m), fixed reference calibration',
           title=f'CH{config["channel"]} continuous lidar waterfall\n{time_edges[-1]-time_edges[0]:g} s displayed; {effective_window:g} ms windows')
    lower = config.get('range_axis_min_m', 0)
    upper = config.get('range_axis_max_m') or float(range_edges[-1])
    ax.set_ylim(lower, upper)
    if lower < range_edges[0]:
        ax.axhspan(lower, range_edges[0], color='0.92', hatch='//', label='Outside selected range branch')
        ax.legend(loc='upper right')
    fig.savefig(directory / 'waterfall.png', dpi=180)
    plt.close(fig)
    metadata = dict(effective_window_ms=effective_window, requested_window_ms=profile['window_ms'], reference_calibration=config, windows=len(time_edges)-1,
                    counts_plotted=int(counts.sum()), selected_channel_records=selected_total,
                    records_outside_plot=int(selected_total-counts.sum()),
                    time_axis='SYNC counter / measured SYNC rate + fine delay; acquisition origin retained',
                    window_durations_s=np.diff(time_edges).tolist(), color='raw counts / range_m**4, common log scale',
                    background_subtracted=False, zero_counts='masked as dark background',
                    partial_final_window='raw counts, not normalized by exposure duration')
    (directory / 'waterfall-summary.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print('PLOT', directory / 'waterfall.png', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_directory', type=Path)
    parser.add_argument('--start-seconds', type=float, default=0)
    parser.add_argument('--end-seconds', type=float)
    args = parser.parse_args()
    plot_capture(args.capture_directory, args.start_seconds, args.end_seconds)
