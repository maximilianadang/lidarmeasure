"""Mount-only motion orchestration; acquisition remains owned by acquisition.py."""
import json
import math
import threading
import time


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


def acquisition_active(out, max_age):
    path = out / 'stream-progress.json'
    try:
        progress = json.loads(path.read_text())
        return progress.get('motion_ready', False) and progress['status'] == 'acquiring' and time.time()-path.stat().st_mtime < max_age
    except FileNotFoundError: return False


def run_motion(out, config, reference, frame, stop):
    from astromount import Mount
    from astromount_control import Controller, Settings, duration_settings
    from mount_recording import read_sample
    motion = config['motion']
    validate_motion(motion)
    settings = Settings(margin=1.25, timeout=motion['timeout_s'], **({'max_speed': motion['speed_deg_s']} if 'speed_deg_s' in motion else {}))
    targets = motion['targets']
    points = [{k: v for k, v in target.items() if k != 'duration_s'} for target in targets]
    for point in points: frame.inverse(**point)
    max_age = motion.get('acquisition_timeout_s', 2)
    if not math.isfinite(max_age) or max_age <= 0: raise ValueError('Invalid acquisition_timeout_s')
    cancelled, active = threading.Event(), threading.Event()
    def watch():
        while not stop.wait(.05):
            if active.is_set() and not acquisition_active(out, max_age):
                cancelled.set()
                return
        cancelled.set()
    threading.Thread(target=watch, daemon=True).start()
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
            for point in points: control._target(*frame.inverse(**point))
            control.read()  # Read-only preflight before signalling readiness.
            print('READY', flush=True)
            while not stop.is_set() and not acquisition_active(out, max_age):
                control.read()
                stop.wait(config['interval_s'])
            if stop.is_set(): return
            active.set()
            print('MOTION starting after verified acquisition batch', flush=True)
            try:
                for target, point in zip(targets, points):
                    if cancelled.is_set() or not acquisition_active(out, max_age): break
                    duration = target.get('duration_s')
                    control.settings = settings if duration is None else duration_settings(settings, control.read(), frame.inverse(**point), duration)
                    if cancelled.is_set() or not acquisition_active(out, max_age): break
                    control.run_pointing(frame, **point, cancel=cancelled)
                else:
                    active.clear()
                    (out / 'motion-complete').touch()
                    print('MOTION complete; stopping acquisition', flush=True)
                active.clear()
                while not stop.is_set():
                    control.read()
                    stop.wait(config['interval_s'])
            except Exception:
                progress = json.loads((out / 'stream-progress.json').read_text())
                if not stop.is_set() and progress['status'] not in ('acquired', 'stopped', 'stopping'): raise
            finally:
                active.clear()
                mount.stop()
