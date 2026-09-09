# ARM64 runtime setup

The original development host is ARM64 Ubuntu on NVIDIA Tegra, kernel 5.15.
PicoQuant's snAPI and MHLib Linux binaries are x86-64, so this project runs an
x86-64 Python process through QEMU's ARM64 user-mode emulator.

## Reuse the verified environment

On the original host, the complete environment already exists:

```bash
export LIDAR_RUNTIME_DIR=/home/dusty/snapi-arm64-investigation
./run-python probe.py
```

Alternatively, link that directory to `.runtime` in the repository. The link and
all runtime files are ignored by Git. No system library replacements are needed.

## Runtime components

These are reconstruction notes for a new host; the complete clean-host process
has not been automated or tested end to end. The assembled environment and the
local scripts have been tested on the original host.

| Component | Verified version/source |
| --- | --- |
| ARM64 static QEMU | Debian trixie `qemu-user_10.0.11+ds-0+deb13u1_arm64.deb` |
| x86-64 Python | Astral python-build-standalone CPython 3.11.16, release 20260901 |
| NumPy | 2.2.6, CPython 3.11 manylinux x86-64 wheel |
| snAPI | 1.1.2, CPython 3.11 manylinux x86-64 wheel |
| MHLib | 4.0, included with the snAPI wheel |
| x86-64 runtime base | Ubuntu Base 22.04.5 amd64 |
| x86-64 libc | Ubuntu `libc6_2.39-0ubuntu8_amd64.deb` |
| x86-64 C++ runtime | Ubuntu `libstdc++6_14-20240412-0ubuntu1_amd64.deb` and matching `libgcc-s1` |
| libusb source | Official libusb 1.0.29 release |
| Cross-compiler | Zig 0.13.0, Linux aarch64 |

Official download locations:

- https://deb.debian.org/debian/pool/main/q/qemu/qemu-user_10.0.11+ds-0+deb13u1_arm64.deb
- https://github.com/astral-sh/python-build-standalone/releases/tag/20260901
- https://pypi.org/project/snAPI/1.1.2/#files
- https://pypi.org/project/numpy/2.2.6/#files
- https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/
- https://archive.ubuntu.com/ubuntu/pool/main/g/glibc/
- https://archive.ubuntu.com/ubuntu/pool/main/g/gcc-14/
- https://github.com/libusb/libusb/releases/tag/v1.0.29
- https://ziglang.org/download/0.13.0/zig-linux-aarch64-0.13.0.tar.xz

`download-sha256.json` records the downloaded files from the investigation,
including superseded versions used during diagnosis. Follow the table above for
the working combination. Ubuntu archive versions may move as distributions update.
PicoQuant's binaries must be obtained separately under its license.

## Required directory layout

The launcher expects the following beneath `LIDAR_RUNTIME_DIR`:

```text
qemu-current/usr/bin/qemu-x86_64
python/bin/python3
runtime/lib64/ld-linux-x86-64.so.2
runtime/usr/lib/x86_64-linux-gnu/
package/snapi-1.1.2/snAPI/
package/snapi-1.1.2/snapi.libs/
libusb-local/lib/libusb-1.0.so.0
libusb-1.0.29/
zig-linux-aarch64-0.13.0/
```

1. Extract the ARM64 QEMU Debian package with `dpkg-deb -x` into `qemu-current`.
   This does not install it as a system package.
2. Extract the x86-64 Python standalone archive at the runtime root; it creates
   `python/`.
3. Extract Ubuntu Base into `runtime/`, then extract the x86-64 C/C++ runtime
   packages there. Preserve Ubuntu's merged `/usr` layout: `runtime/lib` must
   resolve to `usr/lib`. Debian-package extraction can replace that symlink with
   a directory; merge those new files into `runtime/usr/lib` and restore the link.
4. Make absolute symlinks within the extracted runtime relocatable, particularly
   the loader link. They must resolve within the extracted tree. Do not map host
   `/proc`, `/run`, `/sys`, or `/dev` into it; broad mappings were not needed.
5. Unzip the snAPI wheel into `package/snapi-1.1.2` without discarding its sibling
   `snapi.libs` directory. Download the x86-64 NumPy wheel using the native host,
   then install it through the emulated Python with `-m pip install --no-index`.
   Use explicit local filenames; DNS inside the extracted runtime was unreliable.
6. Extract libusb and Zig at the runtime root and run the compatibility build:

   ```bash
   export LIDAR_RUNTIME_DIR=/absolute/path/to/runtime-root
   ./scripts/build-libusb.sh
   ```

7. Grant USB access with `scripts/install-usb-access.sh`, then run `probe.py`.

## Why the libusb rebuild matters

The snAPI wheel bundles libusb 1.0.23 with libudev. Libudev's component-wise path
traversal failed under this extracted-root QEMU setup, producing an empty device
list even after USB permissions were fixed.

The build script compiles libusb 1.0.29 with `--disable-udev`, using its direct
Linux USB backend. It links with Zig directly because libtool selected the ARM64
host linker during the cross-build. It replaces only this runtime-local file:

```text
package/snapi-1.1.2/snapi.libs/libusb-1-1b307d35.0.so.0.2.0
```

The original is retained with suffix `.original`. MHLib's embedded RPATH and
renamed dependency select that exact filename, so adjusting LD_LIBRARY_PATH alone
was insufficient. No host libusb is modified.

## Version findings

- snAPI 1.2.5 hit an illegal instruction during native initialization under QEMU,
  including with `-cpu max`; 1.1.2 worked.
- QEMU 8.2.2 hit an internal startup crash; QEMU 10.0.11 worked.
- Use the normal 32 MB snAPI buffer. A trial with 256 MB caused approximately
  5.6 GB resident process memory on this 8 GB host. The final vendor example
  succeeded with the normal buffer.
- T2's 10 MHz SYNC event stream makes processing slower and output much larger
  than T3 at the observed detector rates. Acquisition duration and process
  wall-clock runtime are different under emulation.
