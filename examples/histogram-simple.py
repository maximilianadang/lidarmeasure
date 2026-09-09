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
from snAPI.Constants import MeasMode

base = Path(__file__).resolve().parents[1]
runtime = Path(os.environ["LIDAR_RUNTIME_DIR"])
out = base / "measurements" / ("vendor-demo-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
out.mkdir(parents=True)
settings = out / "system.ini"
settings.write_text((base / "system.ini").read_text())
sn = snAPI(str(settings))
started = False
try:
    if not sn.getDevice("1052684") or not sn.initDevice(MeasMode.T2):
        raise RuntimeError("Device initialization failed")
    if not sn.device.setSyncChannelEnable(1):
        raise RuntimeError("Could not enable SYNC")
    for ok in (sn.device.setSyncDiv(1), sn.device.setSyncEdgeTrig(-170, 1),
               sn.device.setInputEdgeTrig(0, -170, 1)):
        if not ok:
            raise RuntimeError("Input configuration failed")
    time.sleep(0.3)
    rates = sn.getCountRates().tolist()
    if not 9_000_000 < rates[0] < 11_000_000:
        raise RuntimeError(f"Expected approximately 10 MHz SYNC; got {rates}")

    # Same histogram setup and acquisition as PicoQuant's simple demo.
    sn.histogram.setRefChannel(0)
    sn.histogram.setBinWidth(100)
    if not sn.histogram.setNumBins(1000):
        raise RuntimeError("Histogram configuration failed")
    if not sn.setPTUFilePath(str(out / "measurement.ptu")):
        raise RuntimeError("PTU path rejected")
    started = True
    if not sn.histogram.measure(acqTime=1000, waitFinished=True, savePTU=True):
        raise RuntimeError("Measurement failed")
    data, bins = sn.histogram.getData()
    data, bins = np.array(data, copy=True), np.array(bins, copy=True)

    lib = ct.CDLL(str(runtime / "package/snapi-1.1.2/snAPI/libmhlib.so"))
    flags = ct.c_int()
    if lib.MH_GetFlags(ct.c_int(0), ct.byref(flags)) < 0 or flags.value & 0x12:
        raise RuntimeError(f"Data-integrity error: flags={flags.value}")
    np.savez(out / "histogram.npz", counts=data, time_ps=bins)
    np.savetxt(out / "histogram.csv", np.column_stack((bins / 1000, data.T)),
               delimiter=",", header="time_ns,sync,CH1,CH2,CH3,CH4", comments="",
               fmt=["%.4f"] + ["%d"] * data.shape[0])
    peak = int(np.argmax(data[1]))
    summary = dict(mode="T2", duration_ms=1000, bin_width_ps=100, num_bins=1000,
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
