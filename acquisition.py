"""Shared run lifecycle with separate histogram and continuous-event acquisition."""
from contextlib import contextmanager
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
from stream_capture import stream_events as events, atomic_json


def write_json(path, value):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
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
            if measurement is not None:
                measurement.stopMeasure()
        finally:
            sn.closeDevice()


def hardware_flags(sn, mode):
    library = Path(os.environ['LIDAR_RUNTIME_DIR']) / 'package/snapi-1.1.2/snAPI/libmhlib.so'
    flags = ct.c_int()
    rc = ct.CDLL(str(library)).MH_GetFlags(ct.c_int(sn.deviceConfig['Index']), ct.byref(flags))
    mask = 0x16 if mode == 'T3' else 0x12
    if rc < 0 or flags.value & mask:
        raise RuntimeError(f'Hardware flags check failed: rc={rc}, flags={flags.value}')
    return flags.value


def check_acquisition(sn, mode, stopped=False):
    flags = hardware_flags(sn, mode)
    if not sn.getMeasDescription():
        raise RuntimeError('Could not read acquisition metadata')
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



def run(profile_name):
    settings, profile = load_settings(profile_name)
    out = ROOT / 'measurements' / (profile_name + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    snapshot_settings(settings, out)
    summary = dict(schema_version=1, profile_name=profile_name, status='acquiring',
                   started_utc=datetime.now(timezone.utc).isoformat(), duration_ms=profile['duration_ms'],
                   requested_profile=profile, settings_source=settings['settings_source'],
                   runtime_directory=str(Path(os.environ['LIDAR_RUNTIME_DIR']).resolve()),
                   code_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(ROOT.glob('*.py'))},
                   configuration_sha256={name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                                         for name in ('lidar-settings.json', 'device.ini', 'system.ini')})
    write_json(out / 'summary.json', summary)
    print(f'RUN {profile_name}: {profile["duration_ms"]} ms; settings {settings["settings_source"]}; output {out}', flush=True)
    try:
        with device_session(settings, profile, out) as (sn, measurement):
            time.sleep(.3)
            rates = sn.getCountRates().tolist()
            summary.update(rates_before_Hz=rates, config=sn.deviceConfig)
            check_rates(settings, rates)
            acquire = events if profile_name == 'waterfall' else histogram
            options = {'check_health': lambda: hardware_flags(sn, profile['mode'])} if profile_name == 'waterfall' else {}
            summary.update(acquire(sn, measurement, settings, profile, out, rates, **options))
            summary.update(check_acquisition(sn, profile['mode'], summary.get('stop_requested', False)))
        summary['status'] = 'plotting'
        write_json(out / 'summary.json', summary)
        generate_plots(out, script='plot_waterfall.py' if profile_name == 'waterfall' else 'plot_histogram.py')
        summary['status'] = 'stopped' if summary.get('stop_requested') else 'complete'
    except BaseException as error:
        summary.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        summary['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(out / 'summary.json', summary)
    pointer = {'capture': 'latest-measurement.txt', 'waterfall': 'latest-waterfall.txt',
               'vendor-demo': 'latest-vendor-demo.txt'}[profile_name]
    (ROOT / pointer).write_text(str(out))
    print('COMPLETE', out, flush=True)
