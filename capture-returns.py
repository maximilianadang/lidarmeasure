import os
import ctypes as c,json,time
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from snAPI.Main import snAPI
from snAPI.Constants import MeasMode
p=Path(__file__).resolve().parent
runtime=Path(os.environ['LIDAR_RUNTIME_DIR'])
out=p/'measurements'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out.mkdir(parents=True,exist_ok=False)
sn=snAPI(str(p/'system.ini'))
started=False
try:
    if not sn.getDevice('1052684') or not sn.initDevice(MeasMode.T3):raise RuntimeError('Initialization failed')
    for ok in [sn.device.setSyncDiv(1),sn.device.setSyncEdgeTrig(-170,1),sn.device.setInputEdgeTrig(0,-170,1)]:
        if not ok:raise RuntimeError('Input configuration failed')
    time.sleep(.3)
    rates=sn.getCountRates().tolist()
    if not 9e6<rates[0]<11e6:raise RuntimeError(f'Expected approximately 10 MHz sync, got {rates}')
    started=True
    if not sn.histogram.measure(1000,True,False):raise RuntimeError('Histogram acquisition failed')
    data,bins=sn.histogram.getData();data=np.array(data,copy=True);bins=np.array(bins,copy=True)
    lib=c.CDLL(str(runtime/'package/snapi-1.1.2/snAPI/libmhlib.so'))
    flags=c.c_int();rc=lib.MH_GetFlags(c.c_int(0),c.byref(flags))
    if rc<0 or flags.value&0x16:raise RuntimeError(f'Invalid acquisition flags: rc={rc}, flags={flags.value}')
    np.savez(out/'histogram.npz',counts=data,time_ps=bins)
    np.savetxt(out/'histogram-80ns.csv',np.column_stack([bins[:1000]/1000,data[:,:1000].T]),delimiter=',',header='time_ns,sync,CH1,CH2,CH3,CH4',comments='',fmt=['%.5f']+['%d']*data.shape[0])
    peak=int(np.argmax(data[1]));summary={'duration_ms':1000,'rates_before_Hz':rates,'bin_width_ps':int(bins[1]-bins[0]),'shape':list(data.shape),'counts_per_channel':data.sum(axis=1).tolist(),'ch1_peak_bin':peak,'ch1_peak_time_ns':float(bins[peak]/1000),'ch1_peak_counts':int(data[1,peak]),'hardware_flags':flags.value,'config':sn.deviceConfig}
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    (p/'latest-measurement.txt').write_text(str(out))
    print('CAPTURE',str(out),json.dumps({k:v for k,v in summary.items() if k!='config'}),flush=True)
finally:
    if started:sn.histogram.stopMeasure()
    sn.closeDevice()
