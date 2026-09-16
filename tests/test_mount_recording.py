import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from mount_recording import record_mount, sample


class MountRecordingTests(unittest.TestCase):
    def test_read_only_sample_uses_reference_and_timestamps(self):
        mount, reference, frame = Mock(), Mock(), Mock()
        mount.joint_sample.return_value = (10, 20, 'N')
        reference.offsets.return_value = (1, 2)
        frame.forward.return_value = (3, 4)
        row = sample(mount, reference, frame, (0, 0))
        reference.offsets.assert_called_once_with(10, 20, (0, 0))
        frame.forward.assert_called_once_with(1, 2)
        self.assertEqual([c[0] for c in mount.mock_calls], ['joint_sample'])
        self.assertEqual((row['azimuth_deg'], row['elevation_deg']), (3, 4))
        self.assertGreaterEqual(row['finished_monotonic_s'], row['started_monotonic_s'])

    def test_disabled_does_not_require_mount_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with record_mount({'mount': {'enabled': False}}, Path(tmp)):
                pass
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_helper_startup_failure_allows_recording_and_preserves_reason(self):
        from contextlib import contextmanager
        import json
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); repo = out/'repo'; repo.mkdir()
            (repo/'baseline.json').write_text('{}')
            (repo/'polarity.json').write_text('{"positive_directions": ["west", "south"]}')
            settings = dict(mount=dict(enabled=True, repo=str(repo), baseline='baseline.json', interval_s=.1),
                            motion=dict(polarity='polarity.json'))
            @contextmanager
            def failed_helper(*a, **kw):
                raise RuntimeError('Pointing lies outside the configured joint workspace')
                yield
            with patch('mount_recording.background', failed_helper):
                with record_mount(settings, out) as health:
                    self.assertIsNone(health)
                status = json.loads((out/'mount-status.json').read_text())
                self.assertEqual(status['status'], 'unavailable')
                self.assertIn('joint workspace', status['error'])
                with self.assertRaisesRegex(ValueError, 'acquisition failure'):
                    with record_mount(settings, out): raise ValueError('acquisition failure')

    def test_acquisition_error_waits_for_started_motion_before_cleanup(self):
        from contextlib import contextmanager
        import json
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); repo = out / 'repo'; repo.mkdir()
            (repo / 'baseline.json').write_text('{}')
            (repo / 'polarity.json').write_text('{"positive_directions": ["west", "south"]}')
            (out / 'motion-started').touch()
            process = Mock(); process.poll.return_value = None
            @contextmanager
            def helper(*a, **kw):
                try: yield process
                finally: self.assertTrue((out / 'motion-complete').exists())
            settings = dict(mount=dict(enabled=True, repo=str(repo), baseline='baseline.json', interval_s=.1),
                            motion=dict(polarity='polarity.json'))
            def finish(_): (out / 'motion-complete').touch()
            with patch('mount_recording.background', helper), patch('mount_recording.time.sleep', side_effect=finish) as wait:
                with self.assertRaisesRegex(RuntimeError, 'lidar disconnected'):
                    with record_mount(settings, out): raise RuntimeError('lidar disconnected')
                wait.assert_called_once()
