# lidarmeasure

Record MultiHarp lidar events and baseline-relative mount coordinates on the Orin,
with bounded RAM, chunked disk writes, and a browser preview over SSH.

## Run

Connect from your computer with port forwarding:

```bash
ssh -L 8765:127.0.0.1:8765 dusty@ORIN_HOST
```

On the Orin:

```bash
cd ~/workspace/terraforming_mars/lidarmeasure
./run-python lidarmeasure.py
```

Open http://localhost:8765 in your computer's browser. The command starts the
preview for its own run folder before opening the lidar. After recording and
plotting finish, the preview remains open until Ctrl+C. Refresh for a new run.
During acquisition, Ctrl+C requests a stop and final drain; SIGKILL cannot drain.

## Configuration

`lidar-settings.json` is the configuration entry point. Override it with
`./run-python lidarmeasure.py --settings /absolute/path/settings.json`.

- `profiles.measurement.duration_ms`: currently 120000 (two minutes).
- `mode`, `bin_width_ps`, `num_bins`: currently T2, 800 ps, 1200 bins.
- `window_ms`, `preview_max_columns`: 100 ms windows, at most 2000 overview columns.
- `max_records`, `poll_ms`: 12 million API records; request reads every 100 ms.
- `chunk_records`, `chunk_seconds`: commit at one million detector events or five seconds.
- `min_free_disk_gb`: stop on disk reserve (currently 10 GB).
- `save_ptu`: optionally retain the vendor PTU stream; normally false.
- `plot`: channel, calibration, delay/range bounds, and selected histogram interval.
- `mount`: enable sampling, sibling repository, port, baseline, signs, and sample period.
- `preview`: enable webpage, loopback port (8765), refresh period (500 ms).
- `system_ini`, `device_ini`: vendor paths/logging and hardware input settings.

Laser repetition rate is set externally; `expected_sync_rate_hz` checks it.
At 1 MHz the unambiguous target range is about 150 m, not 300 m: flight is round-trip.
An 800 ps bin is about 12 cm of target range; it is not the optical resolution.
The configured zero reference is the baffle at approximately 38 ns.

## Data and memory

All outputs live in `output/TIMESTAMP-measurement/`: event `blocks/*.npz`, frozen
settings, `summary.json`, `stream-progress.json`, vendor files under `snapi/`,
and diagnostics under `logs/`. `output/latest-measurement.txt` identifies the
last successful run; live preview does not depend on that pointer.

Raw chunks contain detector acquisition timestamps, delays since preceding SYNC,
and channels. SYNC records are used for T2 decoding but not retained in these
chunks. Events before the first SYNC are counted and excluded. Enable PTU for the
original stream. Delay ambiguity cannot be removed by changing the plot axis.

snAPI allocates about 216 MB in double buffers, plus processing/runtime overhead.
The detector chunk buffer adds 17 MB; the overview is bounded by its column cap.
Writes occur on size/time limits and at final drain. Time limits are checked when
control returns from snAPI, not hard deadlines. An abrupt failure may lose the
uncommitted chunk. `blocks`, `batches`, `records`, and `committed_records` distinguish
disk chunks, API deliveries, received detector events, and committed detector events.
Completed chunks remain readable; failed writes are reported.

`histogram-summary.npz` is accumulated online. Default plotting reads it without
rereading events and exports `waterfall.npz` plus the two-panel `waterfall.png`.
The default uses raw counts and linear colors. The overview covers the full run;
the histogram uses `plot.histogram_interval.start_s` and `duration_s`.
Custom intervals or changed settings rebin saved events using the same code.
Older `decoded-events.npz` and waterfall-profile recordings remain readable.

`preview.json` is one atomically replaced latest-batch histogram, independent of
disk chunking. The browser has one request in flight and retains no batch history.
Closing it does not affect recording. Preview errors do not erase recorded events.

## Mount coordinates

Acquisition owns the mount connection; another controller must not open it.
The native astromount helper issues getters only. Each run saves
`mount-coordinates.jsonl`, `mount-settings.json`, `mount-baseline.json`, and
`logs/mount.log`. Default requested sample period is 100 ms; actual UTC/monotonic
query brackets are recorded. Az/el are model-estimated, baseline-relative FRD
coordinates, not compass bearings or hardware-synchronized photon pointing.
The baseline must remain physically valid. Startup read failure prevents capture;
a later helper failure marks the run failed while preserving lidar data.

## Saved-data tools and diagnostics

```bash
/usr/bin/python3 -I plot_waterfall.py output/RUN --start-seconds 2 --end-seconds 3 --color-max 25
/usr/bin/python3 -I plot_waterfall.py output/RUN --counts-over-r4
/usr/bin/python3 -I live_preview.py output/RUN
./run-python probe.py
./run-python examples/histogram-simple.py
/usr/bin/python3 -I plot_histogram.py output/VENDOR_RUN
/usr/bin/python3 -s -m unittest discover -s tests
```

The vendor example runs the direct snAPI histogram API for comparison with our
streaming decoder; `plot_histogram.py` renders that format and historical captures.
It uses `profiles.vendor-demo` (currently T2, 800 ps × 1200 bins, 1 second, PTU on).
The device probe enumerates hardware without starting acquisition.
`samples/` retains the original UniHarp reference measurement, not current calibration.

## Installation and verification

The ignored `.runtime` link points to the assembled QEMU/x86-64 snAPI environment.
Alternatively set `LIDAR_RUNTIME_DIR`. See [ARM64 setup](docs/arm64-setup.md), its
[download checksums](docs/download-sha256.json), and `scripts/` for runtime/USB setup.
Native plotting needs NumPy/Matplotlib; mount logging uses the sibling astromount
virtualenv. The webpage itself requires no GUI, frontend packages, or hosting service.

Tests cover chunk boundaries, final draining, errors, calibration, interval counts,
cached plotting, helper cleanup, and snapshot replacement. Short live captures
have succeeded; many-hour stability and guaranteed losslessness are not established.
Final hardware flags and vendor logs are checked for reported data loss. They do
not prove that every record was received, nor detect every missing-SYNC condition.

## Background counting (laser/SYNC off)

```bash
./run-python lidarmeasure.py --settings background-settings.json
```

This small configuration inherits `lidar-settings.json` and selects
`profiles.measurement.kind: "background"`. Duration, chunking, mount logging,
and the automatically started webpage use the same settings and lifecycle.
Omitting `kind` selects normal range capture, which still requires SYNC.
Background mode requires T2 and does not enable or fire the laser.

Background chunks contain `elapsed_s` and `channels`, retaining detector events
before or without SYNC; they deliberately omit `delay_ps`. SYNC events themselves
are discarded. `background.npz`, `background.csv`, and `background.png` contain
counts versus acquisition time for the selected detector channel. The live page shows individual arrival timestamps from the latest batch as event
marks, with no counting windows or averaging. `preview_max_events` in the background
profile caps the display at the latest 10000 events; any omitted events are clearly
labeled and all events are still recorded. The saved full-run count summaries remain
windowed for compact analysis. No range calibration or R⁴ weighting
is applied. Overview windows may be combined for long captures to keep memory bounded;
raw events remain available for finer analysis.

Settings may use `extends` to inherit another JSON file and override only selected
keys. Inherited file paths resolve relative to the file that defines them; the run
saves the fully resolved configuration. Circular inheritance is rejected.

## Synchronized mount motion

```bash
./run-python lidarmove.py --settings motion-settings.json
```

This commands physical motion. Edit `motion.targets` for ordered az/el points;
each is reached and settled before the next. For nominal travel time, add
`duration_s` to a target, e.g. `{ "azimuth": 0, "elevation": 6, "duration_s": 60 }`.
This reuses astromount's duration calculation used by `point.py` and `sequence.py`:
maximum joint displacement divided by duration, capped at 3°/s. Choose either `duration_s` on every target or one `motion.speed_deg_s` with no
target durations. Both or neither raises an error before hardware initialization,
with a minimal configuration fix.
Arrival is not guaranteed at exactly the requested time. `timeout_s` is the arrival
timeout for speed-based moves, or extra time after the nominal duration for timed
moves. For `lidarmove`, recording ends when the last target settles; inherited capture
duration is ignored. The saved duration is a derived summary-sizing budget from
the moves and their timeouts, not a recording timer. `polarity` selects the astromount polarity file.
Baseline and direction calibration must still be valid.

The same acquisition lifecycle and native mount helper are used. One mount
connection both logs the existing coordinate schema and runs astromount's
`Controller.run_pointing(..., cancel=...)`. Motion waits for a valid detector batch
while acquisition reports running. With no such batch, it never starts. In range
mode a valid batch requires a pulse-relative return; background mode uses detector
events without SYNC. This is software coordination, not a shared hardware trigger.

`acquisition_timeout_s` (2 s) bounds allowed progress-file age while moving.
The helper cancels motion if acquisition stops, loses readiness, or ceases updating;
controller timeouts/faults and parent interruption also stop motion. A mount helper
failure propagates to acquisition on its next polling iteration. Controller stop
commands cannot guarantee stopping after power/USB loss; keep the physical stop
available during testing. Motion is always stopped before the mount connection closes.

All usual mount filenames and coordinate fields are preserved. `mount-settings.json`
adds the motion settings; `mount-polarity.json` snapshots the selected polarity.
`lidarmeasure.py` rejects motion configs, so normal acquisition cannot accidentally
start motion. The preview and completed outputs behave exactly as in normal capture.

Range waterfall plotting also writes `histogram.csv`: range-bin bounds in meters, raw
photon counts, and acquisition-relative interval bounds in seconds. It exports the
selected histogram interval across all saved range bins, regardless of display limits
or R⁴ coloring. Replotting replaces this export with the newly selected interval.

Completed and gracefully stopped recordings save `clock.json` (also in
`summary.json`) with the hardware measurement's Unix origin from `MH_GetStartTime`.
It is read after acquisition, before device close, to avoid concurrent MHLib calls.
`blocks/*.npz` retains `elapsed_s` relative to that origin; keep `clock.json` with
those files. Unix event time is the saved integer seconds plus subsecond
picoseconds / 1e12 plus `elapsed_s`. The full epoch in picoseconds is saved as a
decimal string to avoid JSON/float precision loss. No per-event Unix float array
is stored. Internal clocking inherits PC clock accuracy; it does not guarantee
picosecond alignment or eliminate clock drift over long captures. Failed runs may
lack the origin; older recordings cannot be retroactively anchored by this API.

`lidarmove.py` stops acquisition after the final move, drains buffered events, saves
and plots, then exits (including the preview server). It does not wait out a separate
capture duration. Stop detection follows the acquisition polling cadence.

`lidarmove.py` also generates `motion.png` (waterfall, selected histogram, measured
polar elevation/range samples, and az/el history) and `motion-alignment.csv`.
It uses `clock.json` and mount query midpoints, without extrapolating outside mount
coverage. Default interval comes from `plot.histogram_interval`. Replot with:

```bash
/usr/bin/python3 -I plot_motion.py output/TIMESTAMP-measurement --start-seconds 18 --end-seconds 22 --color-max 10
```

`examples/collaborator-motion-plot.py` preserves the supplied reference logic and
hardcoded paths, with Python indentation restored from the pasted Markdown. It
includes the original artificial elevation ramp and independent time origins;
use `plot_motion.py` for measured coordinates and recorded clock alignment.

In `motion.png`, the waterfall, polar panel, and mount history show the full capture.
Only the histogram uses the selected time interval.
