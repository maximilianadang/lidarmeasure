# lidarmeasure

Acquire lidar return histograms from a PicoQuant MultiHarp 150 4N using snAPI
on an ARM64 Linux host through x86-64 QEMU emulation.

A one-second T3 acquisition and an adaptation of PicoQuant's T2 histogram demo
have been verified with real hardware. This repository contains command-line
clients of snAPI; it does not yet expose an application service or calibrated
range-estimation API.

## Working configuration

- MultiHarp serial: `1052684`; four detector inputs; 80 ps base resolution.
- Laser supplies approximately 10 MHz timing pulses to the dedicated SYNC input.
- MPD detector connects to physical CH1.
- SYNC and CH1: -170 mV threshold, rising edge; sync divider 1.
- MultiHarp programmable trigger output: off.
- The verified runtime uses snAPI 1.1.2 / MHLib 4.0, Python 3.11.16,
  NumPy 2.2.6, and QEMU 10.0.11 with a local libusb compatibility build.

CH1 is input index **0** for configuration, but row **1** in returned histogram
arrays. Row 0 represents SYNC. Histogram bin times from snAPI are in picoseconds;
the CSV exports use nanoseconds.

## Run on the existing machine

Point the launcher at the established runtime:

```bash
export LIDAR_RUNTIME_DIR=/home/dusty/snapi-arm64-investigation
./run-python probe.py
./run-python capture-returns.py
```

The launcher also accepts an ignored `.runtime` directory or symlink in this
repository. One has been created locally on the original development machine.
For another host, see [ARM64 setup](docs/arm64-setup.md).

The probe opens/scans the device without starting acquisition. The capture script
initializes T3 mode, applies the settings above, checks for approximately 10 MHz
SYNC, acquires for one second, checks hardware flags, and saves:

- `histogram.npz`: all channels and all native bins.
- `histogram-80ns.csv`: the first 1000 bins at 80 ps (80 ns span).
- `summary.json`: count rates, peak timing, configuration, and hardware flags.

Each capture gets a timestamped directory under `measurements/`.
`latest-measurement.txt` points to the latest successful capture.
Scripts apply settings when run and should not be imported as libraries.
Do not run multiple acquisition clients against the same device concurrently.

## Start with the official example

PicoQuant's [Demo_HistogramSimple.py](https://github.com/PicoQuant/snAPI/blob/main/demos/Demo_HistogramSimple.py)
shows the measurement flow directly. Our [local adaptation](examples/histogram-simple.py)
preserves T2 mode, SYNC reference, 100 ps bins, 1000 bins, a one-second measurement,
and PTU recording. It substitutes the working device settings, local file output,
return-code checks, and cleanup for the demo's Windows INI path and Tk plot.

```bash
./run-python examples/histogram-simple.py
```

This saves `measurement.ptu`, `histogram.csv`, `histogram.npz`, and `summary.json`
under `measurements/vendor-demo-TIMESTAMP/`. The successful normal-buffer test
recorded 17,352 CH1 counts with hardware flags 0 and no overrun warnings.
See [the walkthrough](docs/tutorial.md).

T2 transfers every SYNC event: approximately 40 MB of raw data per second at
10 MHz. T3 carries the timing reference in detector-event records and is much
lighter at the detector rates tested here. T3 is the practical starting point
for ongoing lidar acquisition on this emulated runtime.

## Results and limitations

The T3 test recorded 17,162 CH1 events in one second, with a peak at 42.88 ns and
no hardware error flags. The earlier [UniHarp reference](samples/uniharp-reference.txt)
contains 1162 CH1 counts, 80 ps bins, and a peak near 23 ns. Trigger settings and
timing offsets were not included in that clipboard export. The difference in
peak position has not been calibrated; these delays are **not absolute ranges**.

Short acquisitions are verified. Continuous operation, higher event rates,
long-term stability, and range calibration remain to be validated.
Generated captures, logs, and downloaded binary dependencies are excluded from Git.

## USB access

The original machine already has persistent access configured. On a new host:

```bash
sudo ./scripts/install-usb-access.sh "$USER" 1052684
```

The rule grants access only to that local user and matching device serial.

## Building an application API

snAPI is already a Python API; these scripts are its clients. A reusable layer
would expose configuration, count-rate reading, histogram acquisition, stop, and
close operations, with explicit units, structured errors, and no hardware actions
on import. A single worker should own the device and serialize commands.

Python code in this x86-64 runtime can call that layer directly. Native ARM64
applications need a process boundary to the emulated worker, such as JSON over
subprocess pipes or a local service. HTTP is optional.

See [third-party notices](THIRD_PARTY_NOTICES.md) for the adapted vendor example.
