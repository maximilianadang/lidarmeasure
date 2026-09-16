"""Mount-only motion orchestration; acquisition remains owned by acquisition.py."""
import json
import math
from dataclasses import asdict


def validate_motion(motion):
    targets = motion.get('targets', [])
    if not targets: raise ValueError('Motion requires at least one az/el target')
    for target in targets:
        if ('speed_deg_s' in motion) == ('duration_s' in target):
            raise ValueError('Specify either motion.speed_deg_s or duration_s on every target, never both. '
                             'Minimal fix: remove motion.speed_deg_s and add "duration_s": 30 to each target; '
                             'or remove target durations and set "speed_deg_s": 0.1 in motion.')
        value = target.get('duration_s', motion.get('speed_deg_s'))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Motion duration/speed must be finite and positive; use "duration_s": 30 or "speed_deg_s": 0.1')
        if 'speed_deg_s' in motion and value > 3: raise ValueError('speed_deg_s must be at most 3; use "speed_deg_s": 0.1')


def acquisition_active(out):
    path = out / 'stream-progress.json'
    try:
        progress = json.loads(path.read_text())
        return progress.get('motion_ready', False) and progress['status'] == 'acquiring'
    except FileNotFoundError: return False


def run_motion(out, config, reference, frame, stop):
    from astromount import Mount
    from astromount_control import Controller, Settings
    from astromount_sequence import MOTION_FIELDS
    from astromount_trajectory import sweep
    from astromount_logging import sweep_log
    from mount_recording import read_sample
    motion = config['motion']
    validate_motion(motion)
    values = {key: motion[key] for key in MOTION_FIELDS}
    if 'speed_deg_s' in motion: values['max_speed'] = motion['speed_deg_s']
    settings = Settings(**values)
    targets = motion['targets']
    points = [{k: v for k, v in target.items() if k != 'duration_s'} for target in targets]
    if not motion.get('delta'):
        for point in points: frame.inverse(**point)
    with (out / 'mount-coordinates.jsonl').open('w') as output:
        class LoggedMount(Mount):
            previous = (0, 0)
            def joint_sample(self):
                row = read_sample(super().joint_sample, reference, frame, self.previous)
                self.previous = row['joint_offsets_deg']
                print(json.dumps(row, allow_nan=False), file=output, flush=True)
                return row['hour_angle_deg'], row['declination_deg'], row['status']
        with LoggedMount(config['port'], timeout=.3) as mount:
            control = Controller(mount, reference, positive_directions=motion['positive_directions'], settings=settings)
            if not motion.get('delta'):
                for point in points: settings.validate_target(*frame.inverse(**point))
            control.read()  # Read-only preflight before signalling readiness.
            print('READY', flush=True)
            while not stop.is_set() and not acquisition_active(out):
                control.read()
                stop.wait(config['interval_s'])
            if stop.is_set(): return
            (out / 'motion-started').touch()
            print('MOTION starting after verified acquisition batch', flush=True)
            try:
                waypoints = [(target['azimuth'], target['elevation'], target['duration_s']) for target in targets]
                with sweep_log(dict(waypoints=waypoints, delta=motion.get('delta', False),
                                    settings=asdict(settings), frame=asdict(frame), reference=asdict(reference),
                                    positive_directions=motion['positive_directions'], port=config['port']),
                               path=out / 'logs/sweep.jsonl') as log:
                    sweep(control, frame, waypoints, delta=motion.get('delta', False), log=log, cancel=stop)
                if not stop.is_set():
                    (out / 'motion-complete').touch()
                    print('MOTION complete; stopping acquisition', flush=True)
                while not stop.is_set():
                    control.read()
                    stop.wait(config['interval_s'])
            except Exception:
                if not stop.is_set(): raise
            finally:
                mount.stop()
