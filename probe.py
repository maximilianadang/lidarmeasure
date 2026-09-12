import os
import ctypes as ct
import platform
import configparser
from run_paths import create_run
from pathlib import Path

base = Path(__file__).resolve().parent
runtime = Path(os.environ['LIDAR_RUNTIME_DIR'])
print("Emulated Python:", platform.machine(), platform.python_version(), flush=True)
lib = ct.CDLL(str(runtime / "package/snapi-1.1.2/snAPI/libmhlib.so"))
lib.MH_GetLibraryVersion.argtypes = [ct.c_char_p]
version = ct.create_string_buffer(32)
print("MH_GetLibraryVersion:", lib.MH_GetLibraryVersion(version), version.value.decode(), flush=True)
from snAPI.Main import snAPI
print("snAPI imported", flush=True)
out = Path(os.environ['LIDAR_RUN_DIR']) if os.environ.get('LIDAR_RUN_DIR') else create_run(base / 'output', 'probe')
config = configparser.ConfigParser()
config.optionxform = str
config.read(base / 'system.ini')
config['Paths']['Data'] = str(out / 'snapi')
with (out / 'system.ini').open('w') as handle: config.write(handle)
sn = snAPI(str(out / 'system.ini'))
try:
    ok = sn.getDeviceIDs()
    print("Enumeration:", ok, "IDs:", sn.deviceIDs, flush=True)
finally:
    sn.closeDevice()
print("Probe complete; no acquisition or channel configuration performed.", flush=True)
