"""Build a waterfall from a saved continuous recording without hardware."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from range_transform import weight_counts

from histogram_data import capture_histograms


def plot_capture(directory, start_s=None, end_s=None, raw_counts=True, color_max=None):
    if color_max is not None and (not np.isfinite(color_max) or color_max <= 0):
        raise ValueError('color-max must be a finite positive number')
    directory = Path(directory)
    settings = json.loads((directory / 'lidar-settings.json').read_text())
    profiles = settings['profiles']
    profile, config = profiles.get('measurement', profiles.get('waterfall')), settings['plot']
    time_edges, range_edges, counts, histogram, selected_total, effective_window, interval = capture_histograms(
        directory, profile, config, start_s, end_s)
    if profile.get('kind') == 'background':
        if not raw_counts: raise ValueError('Background counts have no range or R⁴ transform')
        counts = counts[:, 0]
        np.savez(directory / 'background.npz', time_edges_s=time_edges, counts=counts)
        np.savetxt(directory / 'background.csv', np.column_stack((time_edges[:-1], time_edges[1:], counts)),
                   delimiter=',', header='start_s,end_s,photon_counts', comments='')
        fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
        ax.stairs(counts, time_edges)
        ax.set(xlabel='Acquisition time (s)', ylabel='Photon counts per window',
               title=f'CH{config["channel"]} background counts — {effective_window:g} ms windows', ylim=(0, None))
        fig.savefig(directory / 'background.png', dpi=180)
        plt.close(fig)
        print('PLOT', (directory / 'background.png').resolve(), flush=True)
        return
    start_s, end_s = interval
    np.savetxt(directory / 'histogram.csv',
               np.column_stack((range_edges[:-1], range_edges[1:], histogram,
                                np.full(len(histogram), start_s), np.full(len(histogram), end_s))),
               delimiter=',', header='range_start_m,range_end_m,photon_counts,interval_start_s,interval_end_s',
               fmt=['%.12g', '%.12g', '%d', '%.12g', '%.12g'], comments='')
    weighted = weight_counts(counts, range_edges[:-1])
    np.savez(directory / 'waterfall.npz', time_edges_s=time_edges, range_edges_m=range_edges,
             counts=counts, counts_over_r4=weighted,
             histogram_counts=histogram, histogram_interval_s=[start_s, end_s],
             integrated_counts=counts.sum(axis=0))
    values = counts if raw_counts else weighted
    stem = "waterfall" if raw_counts else "waterfall-counts-over-r4"
    color_label = "Photon counts per time/range bin" if raw_counts else "Photon counts / R⁴ per time/range bin (counts m⁻⁴)"
    fig, (ax, hist_ax) = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True,
                                      gridspec_kw={"width_ratios": [1.5, 1]})
    mesh = ax.pcolormesh(time_edges, range_edges, np.ma.masked_less_equal(values.T, 0),
                        shading='flat', cmap='inferno',
                        vmin=0 if color_max is not None else None, vmax=color_max)
    ax.set_facecolor('#080510')
    fig.colorbar(mesh, ax=ax, label=color_label)
    ax.set(xlabel='Elapsed acquisition time (s)', ylabel='Range (m), fixed reference calibration',
           title=f'CH{config["channel"]} continuous lidar waterfall\n{time_edges[-1]-time_edges[0]:g} s displayed; {effective_window:g} ms windows')
    lower = config.get('range_axis_min_m', 0)
    upper = config.get('waterfall_range_max_m', 20)
    ax.set_ylim(lower, upper)
    if lower < range_edges[0]:
        ax.axhspan(lower, range_edges[0], color='0.92', hatch='//', label='Outside selected range branch')
        ax.legend(loc='upper right')
    ax.axvspan(start_s, end_s, facecolor='cyan', alpha=0.15)
    ax.axvline(start_s, color='cyan', linewidth=1)
    ax.axvline(end_s, color='cyan', linewidth=1)
    hist_values = histogram if raw_counts else weight_counts(histogram, range_edges[:-1])
    hist_ax.stairs(hist_values, range_edges, color='#167c9c')
    hist_ax.set(xlabel='Range (m), fixed reference calibration',
                ylabel='Photon counts per range bin' if raw_counts else 'Photon counts / R⁴ (counts m⁻⁴)',
                title=f'CH{config["channel"]} histogram: {start_s:g}–{end_s:g} s\n'
                      f'{end_s-start_s:g} s integration', xlim=(lower, upper), ylim=(0, None))
    hist_ax.grid(alpha=.2)
    fig.savefig(directory / f'{stem}.png', dpi=180)
    plt.close(fig)
    metadata = dict(color_max_requested=color_max, color_limits=list(mesh.get_clim()),
                    effective_window_ms=effective_window, requested_window_ms=profile['window_ms'], reference_calibration=config, windows=len(time_edges)-1,
                    counts_plotted=int(counts.sum()), selected_channel_records=selected_total,
                    records_outside_plot=int(selected_total-counts.sum()),
                    time_axis='Elapsed acquisition seconds; hardware timestamp origin retained',
                    window_durations_s=np.diff(time_edges).tolist(), color='raw counts, linear scale' if raw_counts else 'raw counts / range_m**4, linear scale',
                    histogram_interval_s=[start_s, end_s], histogram_counts=int(histogram.sum()),
                    background_subtracted=False, zero_counts='masked as dark background',
                    partial_final_window='raw counts, not normalized by exposure duration')
    (directory / f'{stem}-summary.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print('PLOT', directory / f'{stem}.png', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_directory', type=Path)
    parser.add_argument('--start-seconds', type=float, help='Histogram interval start; waterfall always shows full capture')
    parser.add_argument('--end-seconds', type=float, help='Histogram interval end (exclusive)')
    colors = parser.add_mutually_exclusive_group()
    colors.add_argument('--raw-counts', dest='raw_counts', action='store_true', default=True,
                        help='Plot raw photon counts (default)')
    colors.add_argument('--counts-over-r4', dest='raw_counts', action='store_false',
                        help='Divide photon counts by range to the fourth power')
    parser.add_argument('--color-max', type=float, help='Colorbar maximum; larger values saturate (default: automatic)')
    args = parser.parse_args()
    plot_capture(args.capture_directory, args.start_seconds, args.end_seconds, args.raw_counts, args.color_max)
