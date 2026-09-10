"""Continuous T3 recording followed by a range-time waterfall; run with ./run-python."""
import ctypes as ct
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

import numpy as np
from snAPI.Main import snAPI
from capture_config import load_settings, snapshot_settings, configure, check_rates
from plotting import generate_plots


def main():
    settings, profile = load_settings('waterfall')
    root = Path(__file__).resolve().parent
    out = root / 'measurements' / ('waterfall-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    snapshot_settings(settings, out)
    sn = snAPI(str(out / 'system.ini'))
    started = False
    try:
        configure(sn, settings, profile, out)
        time.sleep(.3)
        rates = sn.getCountRates().tolist()
        check_rates(settings, rates)
        resolution = float(sn.deviceConfig['Resolution'])
        if resolution != profile['expected_bin_width_ps']:
            raise RuntimeError(f'Unexpected resolution: {resolution} ps')
        # size is RECORDS in this version, not bytes. Allow headroom before starting.
        predicted = sum(rates[1:]) * profile['duration_ms'] / 1000
        if predicted * 2 >= profile['max_records']:
            raise RuntimeError('Increase max_records or shorten duration_ms: insufficient event-buffer headroom')
        print(f'ACQUIRING {profile["duration_ms"] / 1000:g} seconds continuously; output {out}', flush=True)
        started = True
        if not sn.unfold.measure(acqTime=profile['duration_ms'], size=profile['max_records'],
                                 waitFinished=True, savePTU=profile['save_ptu']):
            raise RuntimeError('Continuous event acquisition failed')
        packed, channels = sn.unfold.getData()
        packed, channels = np.array(packed, copy=True), np.array(channels, copy=True)
        np.savez(out / 'events.npz', packed_t3=packed, channels=channels)
        lib = ct.CDLL(str(Path(os.environ['LIDAR_RUNTIME_DIR']) / 'package/snapi-1.1.2/snAPI/libmhlib.so'))
        flags = ct.c_int()
        if lib.MH_GetFlags(ct.c_int(sn.deviceConfig['Index']), ct.byref(flags)) < 0 or flags.value & 0x16:
            raise RuntimeError(f'Hardware data-integrity flags: {flags.value}')
        if len(packed) >= profile['max_records']:
            raise RuntimeError('Event buffer reached capacity; recording may be incomplete')
        if not sn.getMeasDescription():
            raise RuntimeError('Could not read acquisition metadata')
        description = sn.measDescription
        if description.get('WarningsFlag', 0):
            raise RuntimeError(f'Acquisition warnings: {description}')
        if description.get('StopReason') != 'TimeOver':
            raise RuntimeError(f'Acquisition did not complete its configured duration: {description}')
        # The installed API can report Infinity for an unavailable AveSyncPeriod.
        description = {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                       for k, v in description.items()}
        # Unfolded T3 helpers include counter-overflow handling from the native API.
        sync_counts = sn.unfold.nSync_T3(packed)
        delay_ps = sn.unfold.dTime_T3(packed).astype(np.float64) * resolution
        sync_rate = float(description.get('AveSyncRate', 0)) or float(rates[0])
        elapsed_s = sync_counts.astype(np.float64) / sync_rate + delay_ps * 1e-12
        np.savez(out / 'decoded-events.npz', elapsed_s=elapsed_s, delay_ps=delay_ps, channels=channels)
        summary = dict(duration_ms=profile['duration_ms'], bin_width_ps=resolution,
                       requested_profile=profile, rates_before_Hz=rates,
                       sync_rate_for_timestamps_hz=sync_rate,
                       sync_rate_source='AveSyncRate' if description.get('AveSyncRate', 0) else 'pre-capture SYNC rate',
                       records=len(packed), channel_counts=np.bincount(channels, minlength=5).tolist(),
                       hardware_flags=flags.value, measurement_description=description,
                       config=sn.deviceConfig)
        (out / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    finally:
        if started:
            sn.unfold.stopMeasure()
        sn.closeDevice()
    generate_plots(out, script='plot_waterfall.py')
    (root / 'latest-waterfall.txt').write_text(str(out))
    print('WATERFALL', out, flush=True)


if __name__ == '__main__':
    main()
