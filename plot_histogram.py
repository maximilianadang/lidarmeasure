"""Plot saved captures using native Python; no snAPI or hardware access."""
import argparse
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt

from range_transform import C, range_data


def plot_capture(directory):
    directory = Path(directory)
    settings = json.loads((directory / 'lidar-settings.json').read_text())
    config = settings['plot']
    summary = json.loads((directory / 'summary.json').read_text())
    with np.load(directory / 'histogram.npz') as data:
        delay, ranges, counts, weighted = range_data(data['time_ps'], data['counts'][config['channel']], config)
    np.savetxt(directory / 'range-counts-over-r4.csv',
               np.column_stack((delay, ranges, counts, weighted)), delimiter=',',
               header='delay_ns,range_m,photon_counts,photon_counts_per_m4', comments='')
    calibration = dict(config, effective_timing_offset_ns=config['reference_delay_ns'] -
                       2 * config['reference_distance_m'] / C * 1e9,
                       formula='R = reference_distance_m + c * (delay_ns - reference_delay_ns) * 1e-9 / 2',
                       ordinate='raw photon counts / range_m**4', background_subtracted=False,
                       independent_calibration_validation=False,
                       range_ambiguity_m=C / (2 * settings['expected_sync_rate_hz']))
    (directory / 'range-plot-calibration.json').write_text(json.dumps(calibration, indent=2) + '\n')
    for name, x, y, xlabel, ylabel in (
        ('histogram.png', delay, counts, 'Measured delay relative to SYNC (ns)', 'Photon counts per bin'),
        ('range-counts-over-r4.png', ranges, weighted, 'Range (m), using fixed reference calibration',
         'Photon counts / R⁴ (counts m⁻⁴ per bin)')):
        fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
        ax.step(x, y, where='mid', color='#167c9c', linewidth=1.1)
        ax.set(xlabel=xlabel, ylabel=ylabel,
               title=f'CH{config["channel"]} lidar histogram — {summary["duration_ms"] / 1000:g} s acquisition\n'
                     f'{summary["bin_width_ps"]:g} ps bins; reference {config["reference_distance_m"]:g} m at {config["reference_delay_ns"]:g} ns')
        ax.set_xlim(x[0], x[-1]); ax.set_ylim(bottom=0); ax.grid(alpha=.2)
        fig.savefig(directory / name, dpi=180)
        plt.close(fig)
    print('PLOTS', directory, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_directory', type=Path)
    args = parser.parse_args()
    plot_capture(args.capture_directory)
