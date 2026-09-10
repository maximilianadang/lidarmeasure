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
from plot_histogram import C


def histogram_windows(elapsed_s, delay_ps, channels, profile, config):
    duration = profile['duration_ms'] / 1000
    # Include a shortened last window explicitly; never reset the acquisition clock.
    time_edges = np.arange((profile["duration_ms"] + profile["window_ms"] - 1) // profile["window_ms"] + 1, dtype=np.int64) * profile["window_ms"] / 1000
    time_edges[-1] = duration
    width = profile['expected_bin_width_ps']
    lo, hi = config['delay_window_ns']
    delay_edges = np.arange(np.floor(lo * 1000 / width), np.ceil(hi * 1000 / width) + 1) * width
    mask = (channels == config['channel']) & (elapsed_s >= 0) & (elapsed_s < duration) & (delay_ps >= delay_edges[0]) & (delay_ps < delay_edges[-1])
    counts, _, _ = np.histogram2d(elapsed_s[mask], delay_ps[mask], bins=(time_edges, delay_edges))
    range_edges = config['reference_distance_m'] + (delay_edges / 1000 - config['reference_delay_ns']) * 1e-9 * C / 2
    # Match the static plot's bin-coordinate convention; exclude nonpositive bins.
    keep = range_edges[:-1] > 0
    if not keep.any():
        raise ValueError('No positive range bins in configured window')
    first = int(np.flatnonzero(keep)[0])
    ranges = range_edges[first:-1]
    counts = counts[:, first:].astype(np.uint64)
    return time_edges, range_edges[first:], counts, counts / ranges[np.newaxis, :] ** 4


def plot_capture(directory):
    directory = Path(directory)
    settings = json.loads((directory / 'lidar-settings.json').read_text())
    profile, config = settings['profiles']['waterfall'], settings['plot']
    with np.load(directory / 'decoded-events.npz') as events:
        time_edges, range_edges, counts, weighted = histogram_windows(
            events['elapsed_s'], events['delay_ps'], events['channels'], profile, config)
        selected_total = int(np.count_nonzero(events['channels'] == config['channel']))
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
           title=f'CH{config["channel"]} continuous lidar waterfall\n{profile["duration_ms"] / 1000:g} s acquisition; {profile["window_ms"]:g} ms windows')
    fig.savefig(directory / 'waterfall.png', dpi=180)
    plt.close(fig)
    metadata = dict(reference_calibration=config, windows=len(time_edges)-1,
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
    plot_capture(parser.parse_args().capture_directory)
