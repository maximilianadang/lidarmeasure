import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mount_motion import acquisition_active, run_motion, validate_motion


class MotionTests(unittest.TestCase):
    def test_readiness_requires_fresh_valid_running_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);p=out/'stream-progress.json'
            self.assertFalse(acquisition_active(out,2))
            for ready,status,expected in [(False,'acquiring',False),(True,'acquiring',True),(True,'acquired',False)]:
                p.write_text(json.dumps(dict(motion_ready=ready,status=status)))
                self.assertEqual(acquisition_active(out,2),expected)
            with patch('mount_motion.time.time',return_value=time.time()+10):self.assertFalse(acquisition_active(out,2))

    def test_waits_for_batch_and_cancels_when_acquisition_stops(self):
        calls=[];stop=threading.Event()
        class Mount:
            def __init__(self,*a,**kw):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def joint_sample(self):return 0,0,'N'
            def stop(self):calls.append('stop')
        class Controller:
            def __init__(self,mount,*a,**kw):self.mount=mount
            def _target(self,*a):pass
            def read(self):self.mount.joint_sample()
            def run_pointing(self,frame,*,azimuth,elevation,cancel):
                calls.append('move')
                self.read()
                (out/'stream-progress.json').write_text(json.dumps(dict(motion_ready=False,status='acquired')))
                assert cancel.wait(1)
                stop.set()
                raise RuntimeError('Cancelled')
        reference=Mock();reference.offsets.return_value=(0,0)
        frame=Mock();frame.forward.return_value=(0,0);frame.inverse.return_value=(0,0)
        config=dict(port='fake',interval_s=.01,motion=dict(timeout_s=30,
                    targets=[dict(azimuth=0,elevation=0,duration_s=10)],positive_directions=['west','south']))
        timed=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            def enable():
                time.sleep(.1)
                self.assertNotIn('move',calls)
                (out/'stream-progress.json').write_text(json.dumps(dict(motion_ready=True,status='acquiring')))
            helper=threading.Thread(target=enable);helper.start()
            with patch.dict('sys.modules',{'astromount':SimpleNamespace(Mount=Mount),
                         'astromount_control':SimpleNamespace(Controller=Controller,Settings=lambda **k:k,duration_settings=timed)}):
                run_motion(out,config,reference,frame,stop)
            helper.join()
            self.assertEqual(calls,['move','stop'])
            timed.assert_called_once()
            self.assertEqual(timed.call_args.args[-1],10)
            row=json.loads((out/'mount-coordinates.jsonl').read_text().splitlines()[0])
            self.assertIn('started_unix_s',row);self.assertIn('azimuth_deg',row)

    def test_exactly_one_timing_choice(self):
        for motion in ({'targets': [{'duration_s': 30}]}, {'targets': [{}], 'speed_deg_s': .1}):
            validate_motion(motion)
        for motion in ({'targets': [{}]}, {'targets': [{'duration_s': 30}], 'speed_deg_s': .1},
                       {'targets': [{'duration_s': 30}, {}]}, {'targets': [{'duration_s': None}]},
                       {'targets': [{}], 'speed_deg_s': 0}):
            with self.subTest(motion=motion), self.assertRaisesRegex(ValueError, 'duration_s|speed_deg_s'):
                validate_motion(motion)
