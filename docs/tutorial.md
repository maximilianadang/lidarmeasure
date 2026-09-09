# Reproduce PicoQuant's simple histogram example

Official example:
https://github.com/PicoQuant/snAPI/blob/main/demos/Demo_HistogramSimple.py

API reference:
https://picoquant.github.io/snAPI/snAPI.Main.html#snAPI.Main.Histogram

1. Run `./run-python probe.py` and check that serial 1052684 is found.
2. Ensure the laser is operating at 10 MHz with its timing signal on SYNC
   and the detector on CH1.
3. Run `./run-python examples/histogram-simple.py`.
4. Open the directory recorded in `latest-vendor-demo.txt`.
5. Inspect `summary.json`, plot `histogram.csv`, or open `measurement.ptu` in
   compatible PicoQuant software.

The core vendor flow is:

```python
sn.initDevice(MeasMode.T2)
# Apply the known input configuration before starting.
sn.histogram.setRefChannel(0)
sn.histogram.setBinWidth(100)  # picoseconds
sn.histogram.setNumBins(1000)
sn.histogram.measure(acqTime=1000, waitFinished=True, savePTU=True)
data, bins = sn.histogram.getData()
```

This excerpt assumes an opened device. Use the complete local example for
initialization, settings, error checks, output paths, and cleanup.

The first adapted run recorded 16,651 CH1 counts and a peak at 42.8 ns. The
final normal-buffer run recorded 17,352 counts and a peak at 42.4 ns, with
hardware flags 0 and no overrun warnings. Both saved approximately 40 MB of PTU
data. The histogram range is 100 ns; bin width is 100 ps as in the vendor demo.

The original Windows clipboard histogram used 80 ps bins over 80 ns. Its zero
SYNC histogram column is not a count-rate reading and does not establish that
SYNC was absent. Its peak timing cannot be converted to calibrated distance
without the missing offsets and an optical/electrical delay calibration.
