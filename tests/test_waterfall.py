import unittest
import numpy as np
from plot_waterfall import histogram_windows


class WaterfallTests(unittest.TestCase):
    def test_boundaries_empty_windows_and_count_conservation(self):
        profile = dict(duration_ms=350, window_ms=100, expected_bin_width_ps=80)
        config = dict(channel=1, reference_distance_m=18.3, reference_delay_ns=58.4,
                      delay_window_ns=[58.4, 58.64])
        # Include a window boundary, empty window, partial final window, other channel,
        # an event at the exclusive acquisition end, and an out-of-range photon.
        times = np.array([0, .1, .3, .35, .15, .12])
        delays = np.array([58400, 58480, 58560, 58400, 58400, 60000])
        channels = np.array([1, 1, 1, 1, 2, 1])
        te, re, counts, weighted = histogram_windows(times, delays, channels, profile, config)
        np.testing.assert_allclose(te, [0, .1, .2, .3, .35])
        np.testing.assert_array_equal(counts.sum(axis=1), [1, 1, 0, 1])
        self.assertEqual(counts.sum(), 3)
        np.testing.assert_allclose(weighted, counts / re[:-1][None, :] ** 4)

    def test_no_photons_retains_empty_windows(self):
        p = dict(duration_ms=1000, window_ms=100, expected_bin_width_ps=80)
        c = dict(channel=1, reference_distance_m=18.3, reference_delay_ns=58.4,
                 delay_window_ns=[0, 100])
        te, re, counts, _ = histogram_windows(np.array([]), np.array([]), np.array([]), p, c)
        self.assertEqual(len(te), 11)
        self.assertEqual(counts.shape[0], 10)
        self.assertEqual(counts.sum(), 0)


if __name__ == '__main__':
    unittest.main()
