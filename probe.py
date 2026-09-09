import os
import ctypes as ct
import json
import platform
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
sn = snAPI(str(base / "system.ini"))
try:
    ok = sn.getDeviceIDs()
    print("Enumeration:", ok, "IDs:", sn.deviceIDs, flush=True)
finally:
    sn.closeDevice()
print("Probe complete; no acquisition or channel configuration performed.", flush=True)
