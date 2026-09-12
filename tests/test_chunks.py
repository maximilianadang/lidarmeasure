import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from chunk_writer import ChunkWriter
from histogram_data import Summary, capture_histograms


class ChunkTests(unittest.TestCase):
    def test_split_batches_timer_and_final_flush_preserve_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out/'blocks').mkdir()
            progress = dict(blocks=0, committed_records=0)
            with patch('chunk_writer.time.monotonic', return_value=0) as clock:
                writer = ChunkWriter(out, dict(chunk_records=3, chunk_seconds=5), progress)
                writer.add(np.arange(7), np.arange(7)*80, np.ones(7))
                self.assertEqual(progress['committed_records'], 6)
                self.assertEqual(writer.used, 1)
                clock.return_value = 5
                writer.flush_due()
                writer.add(np.array([7]), np.array([560]), np.array([1]))
                writer.flush()
            self.assertEqual(progress['committed_records'], 8)
            arrays = []
            for file in sorted((out/'blocks').glob('*.npz')):
                with np.load(file) as data: arrays.extend(data['elapsed_s'])
            np.testing.assert_array_equal(arrays, np.arange(8))

    def test_cached_summary_needs_no_events_but_custom_interval_does(self):
        p = dict(duration_ms=1000, window_ms=100, bin_width_ps=80, preview_max_columns=5)
        c = dict(channel=1, reference_distance_m=1, reference_delay_ns=0, delay_window_ns=[0,1])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); (out/'blocks').mkdir()
            summary=Summary(p,c)
            times=np.array([.1,.4,.9]); delays=np.array([0,80,160]); channels=np.ones(3)
            summary.add(times,delays,channels);summary.save(out,1)
            (out/'stream-progress.json').write_text(json.dumps(dict(blocks=1,elapsed_s=1)))
            self.assertEqual(capture_histograms(out,p,c)[2].sum(),3)
            with self.assertRaises(FileNotFoundError): capture_histograms(out,p,c,.3,.5)
            np.savez(out/'blocks/00000000.npz',elapsed_s=times,delay_ps=delays,channels=channels)
            custom=capture_histograms(out,p,c,.3,.5)
            self.assertEqual(custom[3].sum(),1)
            self.assertEqual(custom[2].sum(),3)
