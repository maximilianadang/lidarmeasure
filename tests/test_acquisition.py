import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import acquisition
from capture_config import load_settings


class LifecycleTests(unittest.TestCase):
    def test_close_even_when_stop_fails(self):
        sn = Mock()
        sn.histogram.stopMeasure.side_effect = RuntimeError('stop failed')
        fake_module = SimpleNamespace(snAPI=Mock(return_value=sn))
        with patch.dict('sys.modules', {'snAPI': SimpleNamespace(), 'snAPI.Main': fake_module}), patch('acquisition.configure'):
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                with acquisition.device_session({}, {}, Path('/tmp')):
                    pass
        sn.closeDevice.assert_called_once()

    def test_configuration_failure_closes_device(self):
        sn = Mock()
        with patch.dict('sys.modules', {'snAPI': SimpleNamespace(), 'snAPI.Main': SimpleNamespace(snAPI=Mock(return_value=sn))}), patch('acquisition.configure', side_effect=ValueError('invalid config')):
            with self.assertRaises(ValueError):
                with acquisition.device_session({}, {}, Path('/tmp')):
                    pass
        sn.closeDevice.assert_called_once()

    def test_plot_failure_preserves_data_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = dict(settings_source='test.json')
            profile = dict(duration_ms=1, mode='T3')
            sn = Mock(deviceConfig={})
            sn.getCountRates.return_value.tolist.return_value = [10000000]
            def snapshot(settings, out):
                for name in ('lidar-settings.json', 'device.ini', 'system.ini', 'system-source.ini'):
                    (out/name).write_text('{}')
            from contextlib import contextmanager
            @contextmanager
            def session(*args):
                yield sn, Mock()
            def capture(sn, measurement, settings, profile, out, rates):
                (out/'histogram.npz').write_bytes(b'raw data')
                return {}
            with patch('acquisition.ROOT', root), patch('acquisition.load_settings', return_value=(settings, profile)), patch('acquisition.snapshot_settings', snapshot), patch('acquisition.device_session', session), patch('acquisition.histogram', capture), patch('acquisition.check_rates'), patch('acquisition.check_acquisition', return_value={}), patch('acquisition.generate_plots', side_effect=RuntimeError('plot failed')), patch.dict('os.environ', LIDAR_RUNTIME_DIR='/tmp'), patch('acquisition.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'plot failed'):
                    acquisition.run('capture')
            out = next((root/'output'/'runs').iterdir())
            self.assertEqual(json.loads((out/'summary.json').read_text())['status'], 'failed')
            self.assertTrue((out/'histogram.npz').exists())
            self.assertFalse((root/'output'/'latest-measurement.txt').exists())

    def test_invalid_duration_rejected_before_hardware(self):
        settings = json.loads((acquisition.ROOT/'lidar-settings.json').read_text())
        settings['profiles']['capture']['duration_ms'] = 0
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            path.write_text(json.dumps(settings))
            with self.assertRaisesRegex(ValueError, 'duration_ms'):
                load_settings('capture', ['--settings', str(path)])

class AcquisitionDataTests(unittest.TestCase):
    def test_histogram_exports_and_selected_channel_summary(self):
        import numpy as np
        sn = Mock()
        measurement = Mock()
        measurement.getData.return_value = (np.array([[0, 0], [1, 2], [8, 3]]), np.array([0, 80]))
        with tempfile.TemporaryDirectory() as tmp:
            result = acquisition.histogram(sn, measurement, {'plot': {'channel': 2}},
                       dict(duration_ms=1, save_ptu=False, export_bins=2, expected_bin_width_ps=80, mode='T3'), Path(tmp), [])
            self.assertEqual(result['peak_channel'], 2)
            self.assertEqual(result['peak_counts'], 8)
            self.assertEqual(result['channel_counts'], [0, 3, 11])
            self.assertTrue((Path(tmp)/'histogram.npz').exists())

    def test_hardware_flags_use_selected_device(self):
        sn = Mock(deviceConfig={'Index': 3}, measDescription={'WarningsFlag': 0, 'StopReason': 'TimeOver'})
        library = Mock()
        library.MH_GetFlags.return_value = 0
        with patch('acquisition.ct.CDLL', return_value=library), patch.dict('os.environ', LIDAR_RUNTIME_DIR='/tmp'):
            acquisition.check_acquisition(sn, 'T3')
        self.assertEqual(library.MH_GetFlags.call_args.args[0].value, 3)
