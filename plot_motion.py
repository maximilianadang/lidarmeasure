"""Plot saved lidar and mount data on their recorded Unix time origin."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from histogram_data import capture_histograms


def plot_motion(directory, start_s=None, end_s=None, color_max=None):
    directory = Path(directory)
    settings = json.loads((directory / 'lidar-settings.json').read_text())
    profile, config = settings['profiles']['measurement'], settings['plot']
    if profile.get('kind') == 'background':
        print('MOTION plot skipped: background events have no range')
        return
    if color_max is not None and (not np.isfinite(color_max) or color_max <= 0):
        raise ValueError('color-max must be finite and positive')
    clock = json.loads((directory / 'clock.json').read_text())
    rows = [json.loads(line) for line in (directory / 'mount-coordinates.jsonl').read_text().splitlines()]
    if len(rows) < 2: raise ValueError('Motion plotting needs at least two mount samples')
    # Subtract integer epoch seconds first to avoid adding Unix magnitude to photon times.
    mt = np.array([(r['started_unix_s'] - clock['measurement_start_unix_seconds'] +
                    r['finished_unix_s'] - clock['measurement_start_unix_seconds']) / 2 for r in rows])
    mt -= clock['measurement_start_subsecond_ps'] / 1e12
    if np.any(np.diff(mt) <= 0): raise ValueError('Mount Unix timestamps are not strictly increasing')
    t, r, counts, histogram, _, _, interval = capture_histograms(directory, profile, config, start_s, end_s)
    centers, ranges = (t[:-1] + t[1:]) / 2, (r[:-1] + r[1:]) / 2
    angles = np.array([[row['azimuth_deg'], row['elevation_deg']] for row in rows])
    az, el = [np.interp(centers, mt, values, left=np.nan, right=np.nan) for values in angles.T]
    selected = np.isfinite(el)
    lower, upper = config.get('range_axis_min_m', 0), config.get('waterfall_range_max_m', 20)
    visible = (ranges >= lower) & (ranges <= upper) if upper is not None else ranges >= lower
    fig = plt.figure(figsize=(18, 7), layout='constrained')
    gs = fig.add_gridspec(2, 3, width_ratios=[2, 1, 1])
    waterfall, hist, polar, motion = (fig.add_subplot(gs[:, 0]), fig.add_subplot(gs[0, 1:]),
                                    fig.add_subplot(gs[1, 1], projection='polar'), fig.add_subplot(gs[1, 2]))
    mesh = waterfall.pcolormesh(t, r, counts.T, cmap='inferno', vmin=0, vmax=color_max, rasterized=True)
    fig.colorbar(mesh, ax=waterfall, label='Photon counts per time/range bin')
    waterfall.set(xlabel='Acquisition time (s)', ylabel='Range (m)', ylim=(lower, upper), title='Full recording')
    hist.stairs(histogram, r)
    hist.set(xlabel='Range (m)', ylabel='Photon counts', xlim=(lower, upper), title=f'Histogram: {interval[0]:g}–{interval[1]:g} s')
    if selected.any() and visible.any():
        theta, radius = np.meshgrid(np.deg2rad(el[selected]), ranges[visible], indexing='ij')
        polar.scatter(theta.ravel(), radius.ravel(), c=counts[np.ix_(selected, visible)].ravel(),
                      cmap='inferno', norm=mesh.norm, s=5, rasterized=True)
        polar.set_thetamin(min(-10, float(el[selected].min()) - 1))
        polar.set_thetamax(max(10, float(el[selected].max()) + 1))
    else: polar.text(.5, .5, 'No aligned samples in recording', transform=polar.transAxes, ha='center')
    polar.set_ylim(0, upper if upper is not None else r[-1])
    polar.set_title('Full capture: elevation / range (m)\nAzimuth varies; repeated directions overlap', fontsize=9)
    motion.plot(mt, angles[:, 0], label='Azimuth')
    motion.plot(mt, angles[:, 1], label='Elevation')
    motion.set(xlabel='Acquisition time (s)', ylabel='Angle (degrees)', xlim=(t[0], t[-1]))
    motion.legend()
    fig.savefig(directory / 'motion.png', dpi=180)
    plt.close(fig)
    np.savetxt(directory / 'motion-alignment.csv', np.column_stack((centers, az, el)), delimiter=',',
               header='acquisition_time_s,azimuth_deg,elevation_deg', comments='')
    print('PLOT', directory / 'motion.png', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_directory', type=Path)
    parser.add_argument('--start-seconds', type=float)
    parser.add_argument('--end-seconds', type=float)
    parser.add_argument('--color-max', type=float)
    args = parser.parse_args()
    plot_motion(args.capture_directory, args.start_seconds, args.end_seconds, args.color_max)
