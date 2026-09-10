import os
import ctypes as c,json,time
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from snAPI.Main import snAPI
from plotting import generate_plots
from capture_config import load_settings, snapshot_settings, configure, check_rates, check_histogram
settings, profile = load_settings("capture")
p=Path(__file__).resolve().parent
runtime=Path(os.environ['LIDAR_RUNTIME_DIR'])
out=p/'measurements'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out.mkdir(parents=True,exist_ok=False)
snapshot_settings(settings, out)
sn=snAPI(str(out/'system.ini'))
started=False
try:
    configure(sn, settings, profile, out)
    time.sleep(.3)
    rates=sn.getCountRates().tolist()
    check_rates(settings, rates)
    started=True
    if not sn.histogram.measure(profile["duration_ms"],True,profile["save_ptu"]):raise RuntimeError('Histogram acquisition failed')
    data,bins=sn.histogram.getData();data=np.array(data,copy=True);bins=np.array(bins,copy=True)
    check_histogram(profile, data, bins)
    export_bins = profile["export_bins"]
    lib=c.CDLL(str(runtime/'package/snapi-1.1.2/snAPI/libmhlib.so'))
    flags=c.c_int();rc=lib.MH_GetFlags(c.c_int(0),c.byref(flags))
    if rc<0 or flags.value&0x16:raise RuntimeError(f'Invalid acquisition flags: rc={rc}, flags={flags.value}')
    np.savez(out/'histogram.npz',counts=data,time_ps=bins)
    np.savetxt(out/'histogram.csv',np.column_stack([bins[:export_bins]/1000,data[:,:export_bins].T]),delimiter=',',header='time_ns,sync,CH1,CH2,CH3,CH4',comments='',fmt=['%.5f']+['%d']*data.shape[0])
    peak=int(np.argmax(data[1]));summary={'duration_ms':profile['duration_ms'],'requested_profile':profile,'rates_before_Hz':rates,'bin_width_ps':int(bins[1]-bins[0]),'shape':list(data.shape),'counts_per_channel':data.sum(axis=1).tolist(),'ch1_peak_bin':peak,'ch1_peak_time_ns':float(bins[peak]/1000),'ch1_peak_counts':int(data[1,peak]),'hardware_flags':flags.value,'config':sn.deviceConfig}
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print('CAPTURE',str(out),json.dumps({k:v for k,v in summary.items() if k!='config'}),flush=True)
finally:
    if started:sn.histogram.stopMeasure()
    sn.closeDevice()

# Release hardware before launching native plotting. Failure leaves raw data intact.
generate_plots(out)
(p / "latest-measurement.txt").write_text(str(out))
