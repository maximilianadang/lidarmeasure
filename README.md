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
- `histogram.csv`: the first 1000 bins at 80 ps (80 ns span).
- `summary.json`: count rates, peak timing, configuration, and hardware flags.
- `histogram.png`: raw photon counts versus measured delay.
- `range-counts-over-r4.png` and `.csv`: range versus raw counts divided by range⁴.
- `range-plot-calibration.json`: the range formula and calibration assumptions.

Each capture gets a timestamped directory under `measurements/`.
`latest-measurement.txt` points to the latest capture with successful plotting.
If plotting fails, the command fails but the raw capture remains on disk.
Scripts apply settings when run and should not be imported as libraries.
Do not run multiple acquisition clients against the same device concurrently.

## Configuration

Both acquisition scripts load `lidar-settings.json` through `capture_config.py`.
Edit the files below to change subsequent captures:

- `system.ini`: snAPI data/log paths and buffer size.
- `device.ini`: native snAPI device settings, loaded with `loadIniConfig()` after
  device initialization. Physical CH1 is `[Channel_0]`; edge `1` means rising.
- `lidar-settings.json`: device serial, paths to those INIs, expected SYNC rate
  and tolerance, and named `capture` (T3) / `vendor-demo` (T2) profiles.
  Profiles control duration, PTU recording, binning, and CSV export length.

For a separate setup use `--settings /absolute/path/to/lidar-settings.json` with
either script. INI paths resolve relative to that JSON file. The launcher runs
from the repository root, so `Data = ./data` in system.ini is relative to that root.
The expected SYNC rate is a validation check; it does not set the laser rate.

T3 uses the configured device binning code (0 = base resolution); its
`expected_bin_width_ps` checks the returned width, and its full native histogram
length is retained. T2 explicitly sets `bin_width_ps` and `num_bins` through the
histogram API. `export_bins` controls the CSV subset in either mode. Full arrays
always go to NPZ. Each capture directory includes copies of both INIs and the
JSON settings; `summary.json` includes the requested profile and reported
device configuration. Histogram dimensions and bin width are checked before export.

## One-command capture and plotting

```bash
./run-python capture-returns.py
# Or choose a complete settings bundle:
./run-python capture-returns.py --settings /absolute/path/to/lidar-settings.json
```

The `plot` section in `lidar-settings.json` controls the plotted detector channel,
delay window, and fixed reference calibration. The current reference is the
user's approximate 18.3 m target and the 58.40 ns peak from capture
`20260910T015320Z`. We use `R = R_ref + c*(t-t_ref)/2`, with delays converted to
seconds. New captures do **not** re-anchor their peaks. Change the calibration
only when calibrating against another known target or after timing setup changes.
The 10 MHz pulse rate leaves a roughly 15 m range ambiguity; this reference
selects a range branch and is not an independent validation of absolute distance.

The requested y-axis is **raw counts / R⁴**, with no background subtraction.
This emphasizes nearer returns; it is not compensation for inverse-fourth-power
loss (which would multiply by R⁴). Nonpositive ranges are excluded. Full raw
histograms remain in NPZ regardless of plotting and CSV windows.

Acquisition runs under QEMU; after releasing the device, it automatically invokes
`plot_histogram.py` using native `/usr/bin/python3 -I`. The isolated interpreter
avoids the host's user-installed NumPy conflicting with system Matplotlib.
Native Python must have NumPy and Matplotlib installed (on Ubuntu, packages
`python3-numpy` and `python3-matplotlib`). No GUI is needed.
To regenerate plots from a saved capture and its saved settings, without hardware:

```bash
/usr/bin/python3 -I plot_histogram.py /absolute/path/to/measurement-directory
```

Run the range conversion tests with `python3 -s -m unittest discover -s tests`.
The separate vendor T2 tutorial remains an acquisition example; the main T3
capture command provides the complete capture-and-plot workflow.

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

## Continuous waterfall capture

```bash
./run-python waterfall.py
# Optional alternate settings bundle:
./run-python waterfall.py --settings /absolute/path/to/lidar-settings.json
```

This runs **one continuous T3 acquisition**, waits for the configured duration,
then produces a range–time heatmap on native Python. Setup and plotting add wall
clock time beyond the acquisition duration. There are no intentional stop/start
gaps between waterfall columns and no live display in this version.

Edit `profiles.waterfall` in `lidar-settings.json`:

- `duration_ms`: total acquisition time; default 10000 (10 seconds).
- `window_ms`: waterfall column exposure; default 100 (100 columns in 10 seconds).
- `max_records`: in-memory event capacity; default 2000000 (about 18 MB for the
  API event arrays, plus processing copies). This is a record count, not bytes.
- `save_ptu`: save the original time-tag stream; default true.
- `binning_code` and `expected_bin_width_ps`: same T3 controls as static capture.

The shared `plot` section supplies CH1 selection, delay window, fixed range
calibration, and counts/R⁴ weighting. One logarithmic color scale applies to the
whole image. Zero counts appear dark. A shortened last window retains its actual
exposure and raw counts, so it may appear fainter; choose a duration divisible by
window_ms for equal exposures.

Outputs live under `measurements/waterfall-TIMESTAMP/`:

- `waterfall.png`: elapsed time on x, range on y, counts/R⁴ as color.
- `waterfall.npz`: raw counts and weighted matrices (time × range), plus bin edges.
- `events.npz`: packed unfolded T3 events and channels.
- `decoded-events.npz`: elapsed times, fine delays, and channels.
- `measurement.ptu` when enabled; settings snapshots and JSON summaries.

`latest-waterfall.txt` points to the latest successful result. Replot without
hardware using `/usr/bin/python3 -I plot_waterfall.py /path/to/capture-directory`.

The implementation follows the event-decoding approach of PicoQuant's
[Demo_RecordViewer_UF.py](https://github.com/PicoQuant/snAPI/blob/main/demos/Demo_RecordViewer_UF.py)
and the histogram-window concept of
[Demo_HistogramsOverTime.py](https://github.com/PicoQuant/snAPI/blob/main/demos/Demo_HistogramsOverTime.py).
It uses methods available in snAPI 1.1.2. Timestamp windows use unfolded SYNC
counters and fine delays, not host polling times; the clock starts at acquisition,
not at the first detected photon. Elapsed seconds assume a stable SYNC rate.

A 10-second hardware test recorded 83,801 CH1 photons in 100 windows, with hardware
flags and acquisition warnings both zero. All selected photons were accounted for
in the waterfall. Memory capacity and acquisition-completion checks reject
obviously incomplete recordings. This bounded in-memory workflow is intended for
short experiments; indefinite streaming and sustained high-rate acquisition need
block processing and further validation.

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
