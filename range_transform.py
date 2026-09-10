"""Shared fixed calibration and raw counts/R⁴ weighting (no hardware or plotting)."""
import numpy as np

C = 299792458.0


def range_data(time_ps, counts, config):
    """Use a fixed calibration, never re-anchor the peak of a new capture."""
    delay_ns = np.asarray(time_ps) / 1000
    ranges = to_range(time_ps, config)
    lo, hi = config['delay_window_ns']
    mask = (delay_ns >= lo) & (delay_ns < hi) & (ranges > 0)
    if not np.any(mask):
        raise ValueError('No positive-range bins in the configured plotting window')
    raw = np.asarray(counts)[mask]
    return delay_ns[mask], ranges[mask], raw, weight_counts(raw, ranges[mask])


def to_range(time_ps, config):
    return config['reference_distance_m'] + (np.asarray(time_ps) / 1000 - config['reference_delay_ns']) * 1e-9 * C / 2


def weight_counts(counts, ranges):
    return np.asarray(counts) / np.asarray(ranges) ** 4
