# Adapted from PicoQuant Demo_HistogramSimple.py; see THIRD_PARTY_NOTICES.md.
"""Local adaptation of PicoQuant demos/Demo_HistogramSimple.py.
Uses the same T2, SYNC reference, 100 ps bins, 1000 bins, 1 s, PTU flow.
Changes: known device settings, local paths, file output,
return-code checks, and cleanup. Plot separately on the native host.
"""
import os
import ctypes as ct
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from snAPI.Main import snAPI
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_config import load_settings, snapshot_settings, configure, check_rates, check_histogram
config, profile = load_settings("vendor-demo")

base = Path(__file__).resolve().parents[1]
runtime = Path(os.environ["LIDAR_RUNTIME_DIR"])
out = base / "measurements" / ("vendor-demo-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
out.mkdir(parents=True)
settings = out / "system.ini"
snapshot_settings(config, out)
sn = snAPI(str(settings))
started = False
try:
    configure(sn, config, profile, out)
    time.sleep(0.3)
    rates = sn.getCountRates().tolist()
    check_rates(config, rates)

    started = True
    if not sn.histogram.measure(acqTime=profile["duration_ms"], waitFinished=True, savePTU=profile["save_ptu"]):
        raise RuntimeError("Measurement failed")
    data, bins = sn.histogram.getData()
    data, bins = np.array(data, copy=True), np.array(bins, copy=True)

    check_histogram(profile, data, bins)
    export_bins = profile["export_bins"]
    lib = ct.CDLL(str(runtime / "package/snapi-1.1.2/snAPI/libmhlib.so"))
    flags = ct.c_int()
    if lib.MH_GetFlags(ct.c_int(0), ct.byref(flags)) < 0 or flags.value & 0x12:
        raise RuntimeError(f"Data-integrity error: flags={flags.value}")
    np.savez(out / "histogram.npz", counts=data, time_ps=bins)
    np.savetxt(out / "histogram.csv", np.column_stack((bins[:export_bins] / 1000, data[:, :export_bins].T)),
               delimiter=",", header="time_ns,sync,CH1,CH2,CH3,CH4", comments="",
               fmt=["%.4f"] + ["%d"] * data.shape[0])
    peak = int(np.argmax(data[1]))
    summary = dict(mode="T2", duration_ms=profile["duration_ms"], bin_width_ps=float(bins[1]-bins[0]), num_bins=len(bins), requested_profile=profile,
                   rates_Hz=rates, channel_counts=data.sum(axis=1).tolist(),
                   peak_time_ns=float(bins[peak] / 1000), peak_counts=int(data[1, peak]),
                   flags=flags.value, configuration=sn.deviceConfig)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (base / "latest-vendor-demo.txt").write_text(str(out))
    print("DEMO_RESULT", out, json.dumps({k:v for k,v in summary.items() if k!="configuration"}), flush=True)
finally:
    if started:
        sn.histogram.stopMeasure()
    sn.closeDevice()
