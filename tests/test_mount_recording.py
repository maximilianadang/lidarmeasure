import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
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
