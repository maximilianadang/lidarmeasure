import unittest
import numpy as np
from histogram_data import Summary, capture_histograms


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
        summary = Summary(profile, config)
        summary.add(times, delays, channels)
        te, re, counts = summary.te, summary.re, summary.counts
        np.testing.assert_allclose(te, [0, .1, .2, .3, .35])
        np.testing.assert_array_equal(counts.sum(axis=1), [1, 1, 0, 1])
        self.assertEqual(counts.sum(), 3)

    def test_no_photons_retains_empty_windows(self):
        p = dict(duration_ms=1000, window_ms=100, expected_bin_width_ps=80)
        c = dict(channel=1, reference_distance_m=18.3, reference_delay_ns=58.4,
                 delay_window_ns=[0, 100])
        summary = Summary(p, c)
        summary.add(np.array([]), np.array([]), np.array([]))
        te, counts = summary.te, summary.counts
        self.assertEqual(len(te), 11)
        self.assertEqual(counts.shape[0], 10)
        self.assertEqual(counts.sum(), 0)


if __name__ == '__main__':
    unittest.main()


class SavedCaptureTests(unittest.TestCase):
    def test_exact_interval_with_condensed_overview_and_both_formats(self):
        import json
        import tempfile
        from pathlib import Path
        profile = dict(duration_ms=350, window_ms=100, bin_width_ps=80, preview_max_columns=2)
        config = dict(channel=1, reference_distance_m=18.3, reference_delay_ns=58.4,
                      delay_window_ns=[58.4, 58.64])
        events = dict(elapsed_s=np.array([.1, .15, .2, .25, .35]),
                      delay_ps=np.full(5, 58400), channels=np.ones(5))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            np.savez(path/'decoded-events.npz', **events)
            legacy = capture_histograms(path, profile, config, .15, .25)
            (path/'blocks').mkdir()
            np.savez(path/'blocks'/'00000000.npz', **events)
            (path/'stream-progress.json').write_text(json.dumps(dict(elapsed_s=.35, blocks=1)))
            streamed = capture_histograms(path, profile, config, .15, .25)
            for result in (legacy, streamed):
                te, re, counts, histogram, total, window, interval = result
                np.testing.assert_allclose(te, [0, .2, .35])
                self.assertEqual(counts.sum(), 4)
                self.assertEqual(histogram.sum(), 2)
                self.assertEqual(total, 4)
                self.assertEqual(interval, (.15, .25))
            np.testing.assert_array_equal(legacy[2], streamed[2])
            for start, end in [(.4, .5), (0, float('inf')), (.2, .1)]:
                with self.assertRaises(ValueError):
                    capture_histograms(path, profile, config, start, end)
