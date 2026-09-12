import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from capture_config import load_settings
from stream_capture import stream_events
from plot_waterfall import plot_capture


class BackgroundTests(unittest.TestCase):
    def test_no_sync_keeps_events_and_writes_time_histogram(self):
        settings, profile = load_settings('measurement', ['--settings', 'background-settings.json'])
        self.assertEqual(profile['kind'], 'background')
        profile.update(duration_ms=1000, window_ms=100, chunk_records=2)
        measurement = Mock()
        measurement.isFinished.side_effect = [False, True]
        measurement.getBlock.side_effect = [(np.array([100000000000, 200000000000]), np.array([1, 1])),
                                           (np.array([900000000000]), np.array([1]))]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch('stream_capture.time.sleep'), patch('stream_capture.shutil.disk_usage', return_value=Mock(free=10**12)):
                result = stream_events(Mock(), measurement, settings, profile, out, [0, 3])
            self.assertEqual(result['records'], 3)
            with np.load(out/'blocks/00000000.npz') as data:
                self.assertNotIn('delay_ps', data.files)
                np.testing.assert_allclose(data['elapsed_s'], [.1, .2])
            (out/'lidar-settings.json').write_text(json.dumps(settings))
            plot_capture(out)
            with np.load(out/'background.npz') as data: self.assertEqual(data['counts'].sum(), 3)
            snapshot = json.loads((out/'preview.json').read_text())
            self.assertEqual(snapshot['event_times_s'], [.9])
            self.assertNotIn('counts', snapshot)
            self.assertNotIn('bin_width_ps', snapshot)
            self.assertTrue((out/'background.csv').exists())

    def test_background_rejects_t3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json'
            path.write_text(json.dumps({'extends':str(Path('background-settings.json').resolve()),
                                        'profiles':{'measurement':{'mode':'T3'}}}))
            with self.assertRaisesRegex(ValueError,'streaming T2'):
                load_settings('measurement',['--settings',str(path)])
