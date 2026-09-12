"""Shared run lifecycle with separate histogram and continuous-event acquisition."""
from contextlib import contextmanager, ExitStack
import ctypes as ct
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import time

import numpy as np
from capture_config import ROOT, load_settings, snapshot_settings, configure, check_rates, check_histogram
from plotting import generate_plots
from mount_recording import record_mount
from live_preview import preview_server
from run_paths import create_run
from stream_capture import stream_events as events, atomic_json


def write_json(path, value):
    def clean(value):
        if isinstance(value, dict): return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)): return [clean(v) for v in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value
    atomic_json(path, clean(value))


@contextmanager
def device_session(settings, profile, out):
    from snAPI.Main import snAPI
    sn = snAPI(str(out / 'system.ini'))
    measurement = None
    try:
        configure(sn, settings, profile, out)
        measurement = sn.unfold if 'window_ms' in profile else sn.histogram
        yield sn, measurement
    finally:
        try:
            if measurement is not None: measurement.stopMeasure()
        finally:
            sn.closeDevice()


def measurement_clock(sn):
    """Read the latest hardware start epoch only after snAPI's worker finishes."""
    library = Path(os.environ['LIDAR_RUNTIME_DIR']) / 'package/snapi-1.1.2/snAPI/libmhlib.so'
    get_start = ct.CDLL(str(library)).MH_GetStartTime
    get_start.argtypes = [ct.c_int] + [ct.POINTER(ct.c_uint32)] * 3
    get_start.restype = ct.c_int
    words = [ct.c_uint32() for _ in range(3)]
    rc = get_start(sn.deviceConfig['Index'], *(ct.byref(word) for word in words))
    if rc < 0: raise RuntimeError(f'MH_GetStartTime failed: {rc}; Unix clock correspondence unavailable')
    epoch_ps = (words[0].value << 64) | (words[1].value << 32) | words[2].value
    if not epoch_ps: raise RuntimeError('MH_GetStartTime returned zero; Unix clock correspondence unavailable')
    seconds, picoseconds = divmod(epoch_ps, 10**12)
    return dict(source='MH_GetStartTime', measurement_start_unix_ps=str(epoch_ps),
                measurement_start_unix_seconds=seconds, measurement_start_subsecond_ps=picoseconds,
                event_time='Unix seconds = measurement_start_unix_seconds + measurement_start_subsecond_ps / 1e12 + elapsed_s',
                accuracy='Depends on device reference clock; internal reference uses PC clock accuracy, not picosecond Unix accuracy')


def hardware_flags(sn, mode):
    library = Path(os.environ['LIDAR_RUNTIME_DIR']) / 'package/snapi-1.1.2/snAPI/libmhlib.so'
    flags = ct.c_int()
    rc = ct.CDLL(str(library)).MH_GetFlags(ct.c_int(sn.deviceConfig['Index']), ct.byref(flags))
    mask = 0x5e if mode == 'T3' else 0x5a
    if rc < 0 or flags.value & mask: raise RuntimeError(f'Hardware flags check failed: rc={rc}, flags={flags.value}')
    return flags.value


def check_acquisition(sn, mode, stopped=False):
    flags = hardware_flags(sn, mode)
    if not sn.getMeasDescription(): raise RuntimeError('Could not read acquisition metadata')
    description = sn.measDescription
    if description.get('WarningsFlag', 0) or description.get('StopReason') not in (('Manual', 'TimeOver') if stopped else ('TimeOver',)):
        raise RuntimeError(f'Incomplete or invalid acquisition: {description}')
    return dict(hardware_flags=flags, measurement_description=description)


def histogram(sn, measurement, settings, profile, out, rates):
    if not measurement.measure(profile['duration_ms'], True, profile['save_ptu']):
        raise RuntimeError('Histogram acquisition failed')
    data, bins = (np.array(x, copy=True) for x in measurement.getData())
    check_histogram(profile, data, bins)
    np.savez(out / 'histogram.npz', counts=data, time_ps=bins)
    count = profile['export_bins']
    np.savetxt(out / 'histogram.csv', np.column_stack((bins[:count] / 1000, data[:, :count].T)),
               delimiter=',', header='time_ns,sync,' + ','.join(f'CH{i}' for i in range(1, len(data))),
               comments='', fmt=['%.5f'] + ['%d'] * len(data))
    channel = settings['plot']['channel']
    peak = int(np.argmax(data[channel]))
    return dict(bin_width_ps=float(bins[1] - bins[0]), shape=list(data.shape),
                channel_counts=data.sum(axis=1).tolist(), peak_channel=channel,
                peak_bin=peak, peak_time_ns=float(bins[peak] / 1000), peak_counts=int(data[channel, peak]))



def run(profile_name, moving=False):
    settings, profile = load_settings(profile_name)
    if bool(settings.get('motion')) != moving:
        raise ValueError('Use lidarmove.py with a motion configuration; lidarmeasure.py does not command motion')
    if moving:
        from mount_motion import validate_motion
        validate_motion(settings['motion'])
    if moving and 'window_ms' not in profile: raise ValueError('Motion requires streaming acquisition')
    output = Path(settings.get('output_dir', ROOT / 'output'))
    out = Path(os.environ['LIDAR_RUN_DIR']) if os.environ.get('LIDAR_RUN_DIR') else create_run(output, profile_name)
    snapshot_settings(settings, out)
    summary = dict(schema_version=1, profile_name=profile_name, status='acquiring',
                   started_utc=datetime.now(timezone.utc).isoformat(), duration_ms=profile['duration_ms'],
                   requested_profile=profile, settings_source=settings['settings_source'],
                   runtime_directory=str(Path(os.environ['LIDAR_RUNTIME_DIR']).resolve()),
                   code_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(ROOT.glob('*.py'))},
                   configuration_sha256={name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                                         for name in ('lidar-settings.json', 'device.ini', 'system.ini', 'system-source.ini')})
    write_json(out / 'summary.json', summary)
    duration_label = 'until motion completes' if moving else f'{profile["duration_ms"]} ms'
    print(f'RUN {profile_name}: {duration_label}; settings {settings["settings_source"]}; output {out}', flush=True)
    with ExitStack() as services:
        try:
            if 'window_ms' in profile: services.enter_context(preview_server(settings, out))
            print('INITIALIZING device and applying settings', flush=True)
            with device_session(settings, profile, out) as (sn, measurement):
                time.sleep(.3)
                rates = sn.getCountRates().tolist()
                summary.update(rates_before_Hz=rates, config=sn.deviceConfig)
                if profile.get('kind', 'range') != 'background': check_rates(settings, rates)
                acquire = events if 'window_ms' in profile else histogram
                # MHLib calls must not race snAPI's acquisition worker. Check flags after it finishes.
                with record_mount(settings, out) as mount_health:
                    summary["acquisition_call_started_unix_s"] = time.time()
                    summary["acquisition_call_started_monotonic_s"] = time.monotonic()
                    summary.update(acquire(sn, measurement, settings, profile, out, rates,
                                           **({'check_health': mount_health} if moving else {})))
                    summary["acquisition_call_finished_unix_s"] = time.time()
                clock = measurement_clock(sn)
                write_json(out / 'clock.json', clock)
                summary['clock'] = clock
                summary.update(check_acquisition(sn, profile['mode'], summary.get('stop_requested', False)))
            # Vendor warnings may be transient and absent from final flags. Logs are flushed on close.
            warnings = []
            for log in (out / 'snapi').rglob('*.log'):
                for line in log.open(errors='replace'):
                    if any(term in line.lower() for term in ('cnts_dropped', 'buffer overrun', 'buffer full')):
                        warnings.append(line.strip())
            if warnings:
                summary['data_integrity_warnings'] = list(dict.fromkeys(warnings))
                raise RuntimeError('Native log reports dropped counts or buffer exhaustion; saved data is suspect')
            summary['status'] = 'plotting'
            write_json(out / 'summary.json', summary)
            print('PLOTTING saved acquisition', flush=True)
            generate_plots(out, script='plot_waterfall.py' if 'window_ms' in profile else 'plot_histogram.py')
            if moving: generate_plots(out, script='plot_motion.py')
            summary['status'] = 'stopped' if summary.get('stop_requested') and not summary.get('motion_complete') else 'complete'
        except BaseException as error:
            summary.update(status='failed', error=f'{type(error).__name__}: {error}')
            raise
        finally:
            summary['finished_utc'] = datetime.now(timezone.utc).isoformat()
            write_json(out / 'summary.json', summary)
        pointer = {'measurement': 'latest-measurement.txt', 'capture': 'latest-measurement.txt', 'waterfall': 'latest-waterfall.txt',
                   'vendor-demo': 'latest-vendor-demo.txt'}[profile_name]
        (output / pointer).write_text(str(out))
        print('COMPLETE', out, flush=True)
        try:
            if not moving and 'window_ms' in profile and settings.get('preview', {}).get('enabled', True):
                print('Recording finished. Preview remains at http://localhost:'
                      f"{settings.get('preview', {}).get('port', 8765)} — Ctrl+C to close.", flush=True)
                while True:
                    time.sleep(1)
        except KeyboardInterrupt: pass
