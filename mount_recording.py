"""Read-only native astromount sampling alongside the emulated lidar process."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_process import background


@contextmanager
def record_mount(settings, out):
    config = settings.get('mount', {})
    if settings.get('motion') and not config.get('enabled', False): raise ValueError('Motion requires mount logging enabled')
    if not config.get('enabled', False):
        yield
        return
    root = Path(__file__).resolve().parent
    repo = (root / config['repo']).resolve()
    baseline = (repo / config['baseline']).resolve()
    interval = config['interval_s']
    if not math.isfinite(interval) or interval <= 0: raise ValueError('mount.interval_s must be finite and positive')
    snapshot = out / 'mount-baseline.json'
    snapshot.write_bytes(baseline.read_bytes())
    config = dict(config, repo=str(repo), baseline=str(snapshot),
                  baseline_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                  code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in repo.glob('astromount*.py')},
                  coordinates='Model-estimated baseline-relative FRD az/el, degrees',
                  timing='Host monotonic/UTC query brackets; not hardware-synchronized to photons')
    if settings.get('motion'):
        motion = dict(settings['motion'])
        polarity = (repo / motion.pop('polarity')).read_text()
        (out / 'mount-polarity.json').write_text(polarity)
        config['motion'] = dict(motion, positive_directions=json.loads(polarity)['positive_directions'])
    (out / 'mount-settings.json').write_text(json.dumps(config, indent=2)+'\n')
    command = [str(repo / '.venv/bin/python'), '-I', '-u', str(root / 'mount_recording.py'), str(out)]
    with background(command, out / 'logs/mount.log', 'READY', graceful=True, process_handle=True) as process:
        print('MOUNT recording az/el to mount-coordinates.jsonl', flush=True)
        def check_health():
            if process.poll() is not None: raise RuntimeError('Mount helper exited during acquisition; see logs/mount.log')
            return (out / 'motion-complete').exists()
        yield check_health


def sample(mount, reference, frame, previous): return read_sample(mount.joint_sample, reference, frame, previous)


def read_sample(query, reference, frame, previous):
    started, utc_started = time.monotonic(), time.time()
    hour_angle, declination, status = query()
    finished, utc_finished = time.monotonic(), time.time()
    joints = reference.offsets(hour_angle, declination, previous)
    azimuth, elevation = frame.forward(*joints)
    return dict(started_monotonic_s=started, finished_monotonic_s=finished,
                started_unix_s=utc_started, finished_unix_s=utc_finished,
                hour_angle_deg=hour_angle, declination_deg=declination,
                joint_offsets_deg=joints, azimuth_deg=azimuth, elevation_deg=elevation, status=status)


def main(out):
    config = json.loads((out / 'mount-settings.json').read_text())
    sys.path.insert(0, config['repo'])
    from astromount import Mount
    from astromount_control import Reference
    from astromount_kinematics import Pointing
    reference = Reference.from_baseline(config['baseline'])
    frame = Pointing(pitch_sign=config['pitch_sign'], yaw_sign=config['yaw_sign'])
    stop = threading.Event()
    if config.get('motion'):
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: stop.set())
    def wait_for_parent():
        sys.stdin.readline()
        stop.set()
    threading.Thread(target=wait_for_parent, daemon=True).start()
    if config.get('motion'):
        from mount_motion import run_motion
        return run_motion(out, config, reference, frame, stop)
    previous, count = (0, 0), 0
    with Mount(config['port'], timeout=.3) as mount, (out / 'mount-coordinates.jsonl').open('w') as output:
        while not stop.is_set():
            row = sample(mount, reference, frame, previous)
            output.write(json.dumps(row, allow_nan=False)+'\n')
            output.flush()
            previous = row['joint_offsets_deg']
            count += 1
            if count == 1: print('READY', flush=True)
            stop.wait(max(0, config['interval_s'] - (time.monotonic()-row['started_monotonic_s'])))
        os.fsync(output.fileno())


if __name__ == '__main__': main(Path(sys.argv[1]))
