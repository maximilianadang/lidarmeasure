# lidarmeasure

Capture and plot lidar returns from a PicoQuant MultiHarp 150 4N using snAPI on
ARM64 Linux through the verified x86-64 QEMU runtime.

## Run

From this repository:

```bash
./run-python capture-returns.py          # One static histogram and range plot
./run-python waterfall.py                # Continuous recording, then waterfall
./run-python examples/histogram-simple.py # Vendor-style T2 histogram and plots
```

Each command loads `lidar-settings.json`. To choose another settings bundle:

```bash
./run-python waterfall.py --settings /absolute/path/to/lidar-settings.json
```

The local `.runtime` symlink points to the established runtime. Alternatively set
`LIDAR_RUNTIME_DIR=/home/dusty/snapi-arm64-investigation`. For another machine,
see [ARM64 setup](docs/arm64-setup.md). Native `/usr/bin/python3` needs NumPy and
Matplotlib; plotting runs with `-I` to avoid incompatible user-site packages.
Use `./run-python probe.py` to enumerate the device without acquiring.
Do not run multiple acquisition commands against the device concurrently.

## Settings

`lidar-settings.json` is the entry point for all three commands:

- `profiles.capture`: T3, default 1000 ms; full histogram plus selected CSV bins.
- `profiles.waterfall`: T3, default 28800000 ms (8 hours), with 100 ms waterfall windows.
- `profiles.vendor-demo`: T2, default 1000 ms, 100 ps × 1000 bins, with PTU output.
- `plot`: detector channel, delay window, fixed reference calibration, and counts/R⁴.
- `system_ini`: snAPI paths, logging, and buffer settings; default `system.ini`.
- `device_ini`: thresholds, edges, divider, channel enables and offsets; default `device.ini`.

INI paths resolve relative to the JSON file. The launcher runs from the repository
root, so `Data = ./data` is relative to that root. Expected SYNC rate is a check,
not a command to set the laser frequency. Physical CH1 is `[Channel_0]` in the
native device INI and row/channel **1** in output arrays; row 0 represents SYNC.

The verified setup is serial `1052684`, laser timing connected to dedicated SYNC,
MPD detector on CH1, 10 MHz SYNC, -170 mV rising edges, divider 1, trigger output off.
Settings are validated before opening hardware. Device-specific limits are also
checked by snAPI when the INI is applied.

## What each run saves

Every run has a unique directory under `measurements/`. The command prints its
profile, settings source, duration and output directory.

All runs save copies of the JSON and both INIs, and a common `summary.json` with:

- Selected profile, duration, start/end timestamps, and completion/failure status.
- Settings source, SHA-256 hashes of settings snapshots and top-level Python code,
  and runtime directory.
- Count rates, reported device configuration, hardware flags, and acquisition metadata
  when available. Unavailable non-finite vendor values become JSON `null`.
- Mode-specific counts and histogram or event details. Schema version is `1`.

Static/T2 runs save `histogram.npz`, `histogram.csv`, `histogram.png`,
`range-counts-over-r4.png`, its CSV, and `range-plot-calibration.json`.
Waterfall runs save incrementally committed `blocks/00000000.npz` files containing
elapsed timestamps, fine delays and channels, plus `stream-progress.json` with
counters, elapsed time, and stream status. Block files are flushed and atomically
renamed before the progress checkpoint advances. Incomplete `.tmp` files are not
used. At completion, `waterfall.npz`, `waterfall.png`, and `waterfall-summary.json`
contain a bounded overview. `measurement.ptu` is optional (`save_ptu`); it defaults
to false for waterfall to avoid storing the same events twice. Static PTU settings
are unchanged.

The corresponding `latest-measurement.txt`, `latest-vendor-demo.txt`, or
`latest-waterfall.txt` is updated only after acquisition and plotting succeed.
Failures are recorded in the run summary; already saved raw data remains available.
Generated measurements and runtimes are excluded from Git.

## Range and waterfall interpretation

The fixed reference is the approximate 18.3 m target at 58.40 ns in capture
`20260910T015320Z`. Both plotters use `R = R_ref + c*(t-t_ref)/2` and raw counts/R⁴.
New captures never re-anchor their peaks. This weighting emphasizes nearer returns;
it is not inverse-fourth-power loss compensation. Background is not subtracted.
The 10 MHz repetition rate leaves about 15 m of range ambiguity; calibration selects
one range branch and has not been independently validated at a second distance.
80 ps bin spacing corresponds to about 12 mm of range, not guaranteed accuracy.

The waterfall uses one continuous block acquisition. It writes events while the
device keeps recording; it never restarts acquisition between files. Elapsed time
comes from unfolded SYNC counters and fine delays using the initial measured SYNC
rate, which is recorded and held fixed for the run. This assumes a stable laser
clock. No first-photon time subtraction is performed.

### Many-hour operation

`./run-python waterfall.py` records for **8 hours by default** and plots afterward.
Set `profiles.waterfall.duration_ms` to another positive duration. Ctrl+C or SIGTERM
requests a graceful stop, drains the final block, checks acquisition integrity,
and plots the saved interval. Signal handling under the emulated runtime still
needs live validation. Do not use SIGKILL if you want a graceful final drain;
previously committed blocks remain readable after an abrupt exit.

Streaming settings in the same JSON profile:

- `max_records: 2000000`: capacity of each API block, no longer the full run.
  The API allocates two buffers (about 36 MB combined), plus processing copies.
- `poll_ms: 1000`: read/write interval. The script checks initial rate headroom
  against this interval and checks hardware flags after each block.
- `min_free_disk_gb: 10`: stop with an explicit failed status when disk free space
  falls below the reserve. Already committed blocks remain available.
- `window_ms: 100`: requested analysis window.
- `preview_max_columns: 2000`: cap overview size. Long recordings use an integer
  multiple of window_ms; the actual width is saved in waterfall-summary.json.
- `save_ptu: false`: event blocks are sufficient for replotting; enable only if
  the additional original PTU representation is needed.

The acquisition and overview do not load the entire event history into RAM.
Disk use still grows with time and photon rate. Files are written at approximately
one per poll interval when events are present. Timestamp storage is about 17 bytes
per photon plus file overhead. Progress is printed about once a minute. Final
plot generation reads the recording block by block and can take time on long runs.
A single log color scale covers the displayed interval; zero counts appear dark.
A partial last window retains raw counts for its shorter exposure.

Use the replot command below while recording or after a failure to inspect committed
data. For a detailed slice, add `--start-seconds 120 --end-seconds 130`; this uses
the requested 100 ms windows if the interval fits preview_max_columns. Replotting
overwrites the overview files, never the event blocks. Older decoded-events.npz
recordings remain supported.

The tests exercise final draining, graceful stop, disk-reserve failure, and window
count conservation. Short block acquisition and many-hour stability are separate
hardware validation requirements; successful unit tests do not establish lossless
sustained operation. Capacity checks and hardware flags cannot alone prove that
no native software buffer overruns occurred; review acquisition warnings as well.

## Replot and test without hardware

```bash
/usr/bin/python3 -I plot_histogram.py /path/to/static-capture
/usr/bin/python3 -I plot_waterfall.py /path/to/waterfall-capture
python3 -s -m unittest discover -s tests
```

Replotting reads the saved settings and data. The plotting window selects native
bins by their left-edge delay; nonpositive ranges are excluded consistently.

## Code layout

- `capture-returns.py`, `waterfall.py`, and the T2 example: thin entry scripts.
- `acquisition.py`: one run lifecycle, guaranteed device-close attempt, integrity
  checks, metadata and export; separate histogram and event acquisition functions.
- `capture_config.py`: settings loading, validation, snapshots and device setup.
- `range_transform.py`: shared range conversion and counts/R⁴ weighting.
- `plot_histogram.py`, `plot_waterfall.py`: their respective visualizations.
- `plotting.py`: the native plotting subprocess boundary.

Importing acquisition modules does not open hardware. No service layer is required.
The runtime remains snAPI 1.1.2 / MHLib 4.0 under QEMU 10 with patched libusb.
See [the vendor tutorial](docs/tutorial.md), [third-party notices](THIRD_PARTY_NOTICES.md),
and [ARM64 setup](docs/arm64-setup.md) for provenance and setup details.
On a new host, persistent USB access can be configured with
`sudo ./scripts/install-usb-access.sh "$USER" 1052684`.
