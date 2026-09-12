import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from live_preview import publish


class PreviewTests(unittest.TestCase):
    def test_replaces_snapshot_without_accumulating_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for batch in range(100):
                publish(out, np.array([0, 80, 160, 320]), np.array([1, 1, 2, 1]),
                        dict(bin_width_ps=80, num_bins=4), 1, batch)
            snapshot = json.loads((out/'preview.json').read_text())
            self.assertEqual(snapshot['batch'], 99)
            self.assertEqual(snapshot['counts'], [1, 1, 0, 0])
            self.assertEqual([p.name for p in out.iterdir()], ['preview.json'])

    def test_background_keeps_discrete_timestamps_with_bounded_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            publish(out, None, np.ones(5), dict(kind='background',preview_max_events=3),
                    1, 7, np.array([.01,.02,.03,.04,.05]))
            snapshot=json.loads((out/'preview.json').read_text())
            self.assertEqual(snapshot['event_times_s'],[.03,.04,.05])
            self.assertEqual(snapshot['total_events'],5)
            self.assertEqual(snapshot['omitted_events'],2)
            self.assertNotIn('counts',snapshot)
