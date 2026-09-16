# lidarmeasure

Record MultiHarp lidar events and baseline-relative mount coordinates on the Orin,
with bounded RAM, chunked disk writes, and a browser preview over SSH.

## Run

Connect from your computer with port forwarding:

```bash
ssh -L 8766:127.0.0.1:8766 dusty@ORIN_HOST
```

On the Orin:

```bash
cd ~/workspace/terraforming_mars/lidarmeasure
./run-python lidarmeasure.py
```

Open http://localhost:8766 in your computer's browser. The command starts the
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
- `preview`: enable webpage, loopback port (8766), refresh period (500 ms).
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
Decoded writes occur on size/time limits and at final drain. Raw batches are saved
first in `raw-batches/`; a native worker processes them independently. Durable
checkpoints allow it to remove processed raw files without keeping duplicate data
for the whole run. A backlog occupies disk, not an expanding RAM queue. Disk reserve
checks still apply. After a processing failure, pending raw files and the decoder
checkpoint remain available. `blocks`, `batches`, `records`, and `committed_records` distinguish
disk chunks, API deliveries, received detector events, and committed detector events.
Completed chunks remain readable; failed writes are reported.

`histogram-summary.npz` is accumulated online. Default plotting reads it without
rereading events and exports `waterfall.npz` plus the two-panel `waterfall.png`.
The default uses raw counts and linear colors. The overview covers the full run;
the histogram uses `plot.histogram_interval.start_s` and `duration_s`.
Custom intervals or changed settings rebin saved events using the same code.
Older `decoded-events.npz` and waterfall-profile recordings remain readable.

`preview.json` is one atomically replaced latest-processed-batch histogram, independent of
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

This commands physical motion using astromount's `sweep.py` behavior and shared CLI.
CSV durations schedule continuous az/el segments with no intermediate settling.
Only the final target settles; `--timeout` allows extra time for final arrival.
Acquisition starts first, motion waits for a valid batch, and recording continues
until the last target settles. There is no separate lidar cutoff. The sum of CSV
durations is only the planned motion duration (and overview sizing estimate);
settling and startup can make the actual recording longer.

The same acquisition lifecycle and native mount helper are used. One mount
connection both logs the existing coordinate schema and runs astromount's
`astromount_trajectory.sweep(..., cancel=...)`. Motion waits for a valid detector batch
while acquisition reports running. With no such batch, it never starts. In range
mode a valid batch requires a pulse-relative return; background mode uses detector
events without SYNC. This is software coordination, not a shared hardware trigger.

After the initial valid batch, lidar progress age and missing/delayed batches do
not cancel mount motion. Astromount's arrival/progress timeouts, feedback and
workspace checks remain active. Acquisition errors do not cancel a sequence that has started: cleanup waits for
its completion or an astromount fault. Explicit Ctrl+C still requests a mount stop. A mount helper failure propagates to acquisition
on its next polling iteration. Stop commands cannot guarantee stopping after power/USB loss; keep the physical stop
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

`lidarmove.py` stops acquisition at final move completion, drains buffered events, saves
and plots, then exits (including the preview server). It has no separately configured
capture duration. Stop detection follows the acquisition polling cadence.

`lidarmove.py` also generates `motion.png` (waterfall, selected histogram, measured
polar elevation/range samples, and az/el history) and `motion-alignment.csv`.
It uses `clock.json` and mount query midpoints, without extrapolating outside mount
coverage. Default interval comes from `plot.histogram_interval`. Replot with:

```bash
/usr/bin/python3 -I plot_motion.py output/TIMESTAMP-measurement --start-seconds 18 --end-seconds 22 --color-max 10
```

`examples/motion-plot.py` preserves the supplied reference logic and
hardcoded paths, with Python indentation restored from the pasted Markdown. It
includes the original artificial elevation ramp and independent time origins;
use `plot_motion.py` for measured coordinates and recorded clock alignment.

In `motion.png`, the waterfall, polar panel, and mount history show the full capture.
Only the histogram uses the selected time interval.


The default motion configuration reads `motion.csv`, using the same shared CSV
loader as astromount's `sweep.py`. Columns are `az,el,duration` (degrees,
degrees, seconds); angles are absolute baseline-relative coordinates. Set
`motion.path` relative to the configuration file or override it with:

```bash
./run-python lidarmove.py --settings motion-settings.json --path motion.csv
```

The run saves `motion.csv`, the resolved targets, and native sweep telemetry in `logs/sweep.jsonl`.

All motion entrypoints, including standalone astromount `point.py`, `sequence.py`, and `sweep.py`,
use `astromount/motion-settings.json` via astromount's shared loader. This repository's
`motion-settings.json` only selects LiDAR acquisition settings and the CSV path;
it does not duplicate controller defaults. Custom LiDAR profiles may explicitly
override motion values; CLI overrides take precedence. Controller limits live
under `motion` in astromount's file: `deadband` is
0.01° (`--deadband` overrides it). Software excursion limits and stopping margins
have been removed, including the former ±22.5° kinematic cap. IK still selects
the front-facing branch and rejects singular/rear-facing targets. Operation
requires visual supervision and an accessible E-stop; there is no software
collision or travel envelope. Firmware/mechanical restrictions are unchanged.
Resolved values are passed to both dry-run validation and the live controller,
and saved in run settings. Invalid combinations fail the controller's existing
validation; they are never silently clamped. Gain (`kp`), default rate (`max_speed`),
speed ceiling (`speed_limit`), loop period (`period`), sample age (`max_sample_age`),
arrival/progress timeouts (`timeout`, `progress_timeout`), settling (`settle_samples`),
and streaming-worker `heartbeat` are defined here too. Times are seconds and angles
are degrees. CLI options use hyphens, e.g. `--progress-timeout`; `--speed` on point.py
overrides the default rate. The physical command ceiling remains at most 3°/s.

CSV input cannot be combined
with inline targets or speed. The CSV argument definitions and continuous sweep implementation are shared with astromount.
Lidar lifecycle remains owned by `lidarmove`. `--delta` accumulates each row from the initial measured pointing and previous planned endpoint, exactly as `sweep.py` does.

`lidarmove` accepts the same sweep options directly, with no `--` separator:

```bash
./run-python lidarmove.py --path motion.csv
./run-python lidarmove.py --path motion.csv --delta
```

`--timeout`, `--port`, `--baseline`, `--polarity`, and `--dry-run` also use the
shared CSV parser. Mount defaults (including frame signs) come from
astromount/config.json. CLI path overrides are relative to the working directory;
`motion.path` is relative to its settings file. `--settings` selects lidar settings.
`--dry-run` delegates to sweep.py without opening either device; like sweep.py,
it rejects `--delta` because relative validation needs a live position.
Resolved sweep arguments and mount settings are saved with every acquisition.
Motion completion stops recording; there is no lidar duration cap. Astromount faults and explicit user interruption still stop the experiment.

After device cleanup, plotting is attempted for saved data even when acquisition
fails. Failed-run plots are visibly marked incomplete; the original failure remains
in `summary.json`. Each plotting error is recorded separately so it cannot hide an
acquisition error or prevent another plot from being attempted. Without a saved
clock origin, only the clock-aligned motion plot is skipped. Runs that saved no
measurement data have nothing to plot.

High-rate acquisition uses snAPI Raw T2 for MultiHarp devices. The emulated Unfold
path is bypassed. Acquisition atomically saves each returned batch to `raw-batches/`
and immediately resumes polling; it never waits for decoding, histogramming, or
preview rendering. One native ARM worker reads saved batches in order, writes the
existing detector-event NPZ schema, and updates the preview. Only finalization,
after acquisition stops, waits for processing to catch up. Worker failure does not
stop raw recording; it is reported at finalization and the raw backlog is retained.

`raw_batches_saved` and `processing_pending_batches` distinguish recording progress
from processing progress. The console reports both. A processing backlog can delay
the preview, but cannot create an unbounded in-memory queue. Disk write latency and
snAPI/hardware throughput still limit acquisition; this is not a guarantee against
all overruns. Raw files are removed only after decoded chunks and a decoder
checkpoint have been committed. Successful finalization removes the spool directory.
The raw decoder follows PicoQuant's MultiHarp/Generic T2 V2 format:
https://github.com/PicoQuant/PicoQuant-Time-Tagged-File-Format-Demos/blob/master/PTU/Python/Read_PTU.py

Above one million detector events/s (initial or observed), full histogram summaries
are deferred to native postprocessing so they do not compete with recording.
Default postprocessing caches the summary for subsequent plots. Raw records read
are checked against snAPI's final record total. Buffer-overrun log entries stop the
recording as incomplete and still trigger salvage plotting. This does not cancel
an already-started mount sequence. Stage timing maxima/totals are saved in
`stream-progress.json`; native worker diagnostics are in `logs/processing.log`.

The earlier five-second hardware test passed, but a subsequent motion run overflowed
at about 7.2 seconds: synchronous decoding delayed a read by 3.38 seconds. That
result motivated the disk-backed handoff above; it is not validation of this new
implementation. Tests cover a stopped processing worker, worker crashes, checkpoint
recovery across SYNC boundaries, and the final acquisition drain. Hardware endurance
validation is still required.

If the mount helper cannot start (including an out-of-workspace current position),
recording continues without motion for the sum of the CSV waypoint durations.
The reason is saved in `mount-status.json` and `summary.json`; range/count plots
are still generated, and the motion plot is skipped. Mount travel limits remain
enforced. Once a helper starts successfully, normal motion-completion timing applies.
