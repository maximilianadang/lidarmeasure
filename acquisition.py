"""Shared run lifecycle with separate histogram and continuous-event acquisition."""
from contextlib import contextmanager
import ctypes as ct
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np
from capture_config import ROOT, load_settings, snapshot_settings, configure, check_rates, check_histogram
from plotting import generate_plots


def write_json(path, value):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value
    path.write_text(json.dumps(clean(value), indent=2, allow_nan=False) + '\n')


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


def check_acquisition(sn, mode):
    library = Path(os.environ['LIDAR_RUNTIME_DIR']) / 'package/snapi-1.1.2/snAPI/libmhlib.so'
    flags = ct.c_int()
    rc = ct.CDLL(str(library)).MH_GetFlags(ct.c_int(sn.deviceConfig['Index']), ct.byref(flags))
    mask = 0x16 if mode == 'T3' else 0x12
    if rc < 0 or flags.value & mask:
        raise RuntimeError(f'Hardware flags check failed: rc={rc}, flags={flags.value}')
    if not sn.getMeasDescription():
        raise RuntimeError('Could not read acquisition metadata')
    description = sn.measDescription
    if description.get('WarningsFlag', 0) or description.get('StopReason') != 'TimeOver':
        raise RuntimeError(f'Incomplete or invalid acquisition: {description}')
    return dict(hardware_flags=flags.value, measurement_description=description)


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


def events(sn, measurement, settings, profile, out, rates):
    resolution = float(sn.deviceConfig['Resolution'])
    if resolution != profile['expected_bin_width_ps']:
        raise RuntimeError(f'Unexpected resolution: {resolution} ps')
    predicted = sum(rates[1:]) * profile['duration_ms'] / 1000
    if predicted * 2 >= profile['max_records']:
        raise RuntimeError('Increase max_records or shorten duration_ms: insufficient buffer headroom')
    if not measurement.measure(acqTime=profile['duration_ms'], size=profile['max_records'],
                               waitFinished=True, savePTU=profile['save_ptu']):
        raise RuntimeError('Continuous event acquisition failed')
    packed, channels = (np.array(x, copy=True) for x in measurement.getData())
    np.savez(out / 'events.npz', packed_t3=packed, channels=channels)
    if len(packed) >= profile['max_records']:
        raise RuntimeError('Event buffer reached capacity; recording may be incomplete')
    if not sn.getMeasDescription():
        raise RuntimeError('Could not read timestamp metadata')
    average = sn.measDescription.get('AveSyncRate', 0)
    sync_rate = float(average or rates[0])
    if not math.isfinite(sync_rate) or sync_rate <= 0:
        raise RuntimeError('Invalid SYNC rate for timestamps')
    delay_ps = measurement.dTime_T3(packed).astype(np.float64) * resolution
    elapsed_s = measurement.nSync_T3(packed).astype(np.float64) / sync_rate + delay_ps * 1e-12
    np.savez(out / 'decoded-events.npz', elapsed_s=elapsed_s, delay_ps=delay_ps, channels=channels)
    return dict(bin_width_ps=resolution, records=len(packed),
                channel_counts=np.bincount(channels, minlength=5).tolist(),
                sync_rate_for_timestamps_hz=sync_rate,
                sync_rate_source='AveSyncRate' if average else 'pre-capture SYNC rate')


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
            summary.update(acquire(sn, measurement, settings, profile, out, rates))
            summary.update(check_acquisition(sn, profile['mode']))
        summary['status'] = 'plotting'
        write_json(out / 'summary.json', summary)
        generate_plots(out, script='plot_waterfall.py' if profile_name == 'waterfall' else 'plot_histogram.py')
        summary['status'] = 'complete'
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
