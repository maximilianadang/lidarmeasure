import sys
import tempfile
import unittest
from pathlib import Path
from native_process import background


class NativeProcessTests(unittest.TestCase):
    def test_graceful_shutdown_and_recorded_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp)/'closed'
            command = [sys.executable, '-u', '-c',
                       'import sys; from pathlib import Path; print("READY"); sys.stdin.readline(); Path(sys.argv[1]).touch()', str(marker)]
            with background(command, Path(tmp)/'log', 'READY', graceful=True) as ready:
                self.assertEqual(ready, 'READY')
                self.assertFalse(marker.exists())
            self.assertTrue(marker.exists())

    def test_startup_failure_and_preserving_original_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp)/'log'
            with self.assertRaisesRegex(RuntimeError, 'failed to start'):
                with background([sys.executable, '-c', 'raise RuntimeError("bad startup")'], log, 'READY'):
                    self.fail('Must not enter failed helper')
            self.assertIn('bad startup', log.read_text())
            with self.assertRaisesRegex(ValueError, 'original'):
                with background([sys.executable, '-u', '-c', 'import time; print("READY"); time.sleep(30)'], log, 'READY'):
                    raise ValueError('original')
