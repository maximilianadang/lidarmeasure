import unittest
import numpy as np
from range_transform import range_data, C


class RangePlotTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(reference_distance_m=18.3, reference_delay_ns=58.4,
                           delay_window_ns=[0, 100])

    def test_fixed_calibration_preserves_target_motion(self):
        delays = np.array([58.4, 58.4 + 2 / C * 1e9])
        _, ranges, counts, weighted = range_data(delays * 1000, [1, 20], self.config)
        np.testing.assert_allclose(ranges, [18.3, 19.3])
        np.testing.assert_allclose(weighted, counts / ranges ** 4)

    def test_nonpositive_ranges_and_other_periods_are_excluded(self):
        self.config.update(reference_distance_m=1, reference_delay_ns=50)
        delay, ranges, _, weighted = range_data(np.array([0, 50, 100]) * 1000, [5, 10, 15], self.config)
        np.testing.assert_allclose(delay, [50])
        np.testing.assert_allclose(ranges, [1])
        np.testing.assert_allclose(weighted, [10])


if __name__ == '__main__':
    unittest.main()
