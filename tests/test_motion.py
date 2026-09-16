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
    def test_config_limits_reach_dry_run_arguments_and_allow_overrides(self):
        from capture_config import load_settings
        settings, _ = load_settings('measurement', ['--dry-run'], moving=True)
        motion = settings['motion']
        from astromount_config import motion_defaults
        configured = motion_defaults()
        local = json.loads((Path(__file__).resolve().parents[1] / 'motion-settings.json').read_text())['motion']
        self.assertFalse(set(configured) & set(local), 'LiDAR must not duplicate controller defaults')
        from astromount_sequence import MOTION_FIELDS
        for key in MOTION_FIELDS:
            self.assertEqual(motion[key], configured[key])
            index = motion['sweep_argv'].index('--' + key.replace('_', '-'))
            self.assertEqual(float(motion['sweep_argv'][index+1]), configured[key])
        settings, _ = load_settings('measurement', ['--deadband', '.02'], moving=True)
        self.assertEqual(settings['motion']['deadband'], .02)

    def test_readiness_requires_valid_running_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);p=out/'stream-progress.json'
            self.assertFalse(acquisition_active(out))
            for ready,status,expected in [(False,'acquiring',False),(True,'acquiring',True),(True,'acquired',False)]:
                p.write_text(json.dumps(dict(motion_ready=ready,status=status)))
                self.assertEqual(acquisition_active(out),expected)
            import os
            p.write_text(json.dumps(dict(motion_ready=True,status='acquiring')))
            os.utime(p,(1,1))
            self.assertTrue(acquisition_active(out))

    def test_waits_for_first_batch_then_runs_one_shared_sweep(self):
        calls = []; stop = threading.Event()
        class Mount:
            def __init__(self,*a,**kw): pass
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def joint_sample(self): return 0,0,'N'
            def stop(self): calls.append('stop')
        class Controller:
            def __init__(self,mount,*a,**kw): self.mount=mount
            def read(self):
                self.mount.joint_sample()
                if (out/'motion-complete').exists(): stop.set()
        reference=Mock();reference.offsets.return_value=(0,0)
        frame=Mock();frame.forward.return_value=(0,0);frame.inverse.return_value=(0,0)
        from capture_config import load_settings
        from astromount_sequence import MOTION_FIELDS
        defaults = load_settings('measurement', [], moving=True)[0]['motion']
        config=dict(port='fake',interval_s=.01,motion=dict(defaults,delta=True,
                    targets=[dict(azimuth=1,elevation=2,duration_s=10),dict(azimuth=-1,elevation=-2,duration_s=20)],
                    positive_directions=['west','south']))
        def execute(control, frame, points, **kw):
            calls.append('sweep')
            (out/'stream-progress.json').write_text(json.dumps(dict(motion_ready=False,status='acquired')))
            self.assertIs(kw['cancel'], stop)
            self.assertFalse(stop.is_set())
        sweep = Mock(side_effect=execute)
        settings_factory=Mock(return_value=Mock())
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            def enable():
                time.sleep(.1)
                self.assertNotIn('sweep',calls)
                (out/'stream-progress.json').write_text(json.dumps(dict(motion_ready=True,status='acquiring')))
            helper=threading.Thread(target=enable);helper.start()
            with patch.dict('sys.modules',{'astromount':SimpleNamespace(Mount=Mount),
                         'astromount_control':SimpleNamespace(Controller=Controller,Settings=settings_factory),
                         'astromount_trajectory':SimpleNamespace(sweep=sweep)}), \
                 patch('mount_motion.asdict', return_value={}), patch('astromount_logging.sweep_log') as logging:
                run_motion(out,config,reference,frame,stop)
            helper.join()
            settings_factory.assert_called_once_with(**{key: config['motion'][key] for key in MOTION_FIELDS})
            self.assertEqual(calls,['sweep','stop'])
            self.assertEqual(sweep.call_args.args[2], [(1,2,10),(-1,-2,20)])
            self.assertTrue(sweep.call_args.kwargs['delta'])
            self.assertEqual(logging.call_args.kwargs['path'], out/'logs/sweep.jsonl')
            self.assertTrue((out/'motion-complete').exists())
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
