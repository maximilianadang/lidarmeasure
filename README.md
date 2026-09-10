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
- `profiles.waterfall`: T3, default 10000 ms, with 100 ms waterfall windows.
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
Waterfall runs save packed `events.npz`, `decoded-events.npz`, `waterfall.npz`
(time × range counts, weighted counts and edges), `waterfall.png`, and
`waterfall-summary.json`. `measurement.ptu` is saved when `save_ptu` is enabled.

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

The waterfall is one uninterrupted acquisition followed by plotting, not repeated
static captures or a live display. Elapsed time comes from unfolded SYNC counters
and fine delays using the measured SYNC rate. No first-photon time subtraction is
performed. The color scale is shared across the image; zero counts appear dark.
A partial final window retains raw counts for its shorter exposure. Use a duration
that is a multiple of `window_ms` for equal exposures.

`max_records` is event capacity, not bytes; the default 2000000 needs about 18 MB
for API arrays plus processing copies. A pre-capture rate check requires buffer
headroom. Completion, capacity, and warning checks reject detected failures.
Indefinite streaming, high rates, and long-term stability still need validation.

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
