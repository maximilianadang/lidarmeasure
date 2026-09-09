#!/bin/bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${LIDAR_RUNTIME_DIR:-$repo_dir/.runtime}"
runtime_dir="$(realpath -- "$runtime_dir")"
zig="$runtime_dir/zig-linux-aarch64-0.13.0/zig"
export ZIG_GLOBAL_CACHE_DIR="$runtime_dir/zig-cache"
cd "$runtime_dir/libusb-1.0.29"
CC="$zig cc -target x86_64-linux-gnu" AR="$zig ar" RANLIB="$zig ranlib"   ./configure --host=x86_64-linux-gnu --disable-udev --disable-static   --enable-shared --prefix="$runtime_dir/libusb-local"
mkdir -p "$runtime_dir/libusb-local/lib"
cd libusb
"$zig" cc -target x86_64-linux-gnu -shared -fPIC -O2 -pthread   -DHAVE_CONFIG_H -I.. -I. -Wl,-soname,libusb-1.0.so.0   -o "$runtime_dir/libusb-local/lib/libusb-1.0.so.0"   core.c descriptor.c hotplug.c io.c sync.c strerror.c   os/linux_usbfs.c os/linux_netlink.c os/events_posix.c os/threads_posix.c
bundled="$runtime_dir/package/snapi-1.1.2/snapi.libs/libusb-1-1b307d35.0.so.0.2.0"
if [[ ! -e "$bundled.original" ]]; then
  cp -p -- "$bundled" "$bundled.original"
fi
cp -- "$runtime_dir/libusb-local/lib/libusb-1.0.so.0" "$bundled"
echo "Installed the local libusb compatibility build."
