"""Shared capture configuration; importing this module does not open hardware."""
import argparse
import configparser
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_settings(profile_name, argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path, default=ROOT / "lidar-settings.json")
    args = parser.parse_args(argv)
    path = args.settings.resolve()
    settings = json.loads(path.read_text())
    profile = settings["profiles"][profile_name]
    expected_mode = {"capture": "T3", "vendor-demo": "T2", "waterfall": "T3"}[profile_name]
    if profile["mode"] != expected_mode:
        raise ValueError(f"{profile_name} requires mode {expected_mode}")
    keys = ["duration_ms"] + (["window_ms", "max_records", "poll_ms", "preview_max_columns"] if profile_name == "waterfall" else ["export_bins"])
    keys += ["bin_width_ps", "num_bins"] if expected_mode == "T2" else ["expected_bin_width_ps"]
    for key in keys:
        if type(profile[key]) is not int or profile[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if expected_mode == "T2" and profile["export_bins"] > profile["num_bins"]:
        raise ValueError("export_bins exceeds num_bins")
    if expected_mode == "T3" and (type(profile["binning_code"]) is not int or not 0 <= profile["binning_code"] <= 24):
        raise ValueError("Invalid binning_code")
    if type(profile["save_ptu"]) is not bool:
        raise ValueError("save_ptu must be a JSON boolean")
    if profile_name == "waterfall" and profile["window_ms"] > profile["duration_ms"]:
        raise ValueError("window_ms must not exceed duration_ms")
    if profile_name == "waterfall":
        reserve = profile["min_free_disk_gb"]
        if type(reserve) not in (float, int) or not math.isfinite(reserve) or reserve <= 0:
            raise ValueError("min_free_disk_gb must be finite and positive")
    expected = settings["expected_sync_rate_hz"]
    tolerance = settings["sync_rate_tolerance_hz"]
    if not 0 < tolerance < expected:
        raise ValueError("SYNC tolerance must be positive and below expected rate")
    settings["device_serial"] = str(settings["device_serial"])
    for key in ("system_ini", "device_ini"):
        settings[key] = str((path.parent / settings[key]).resolve())
        if not Path(settings[key]).is_file():
            raise FileNotFoundError(settings[key])
    plot = settings["plot"]
    for key in ("reference_distance_m", "reference_delay_ns"):
        if type(plot[key]) not in (int, float) or not math.isfinite(plot[key]):
            raise ValueError(f"Invalid plot {key}")
    if plot["reference_distance_m"] <= 0:
        raise ValueError("Reference distance must be positive")
    if type(plot["channel"]) is not int or not 1 <= plot["channel"] <= 4:
        raise ValueError("Plot channel must be CH1 through CH4")
    window = plot["delay_window_ns"]
    if len(window) != 2 or not all(type(x) in (int, float) and math.isfinite(x) for x in window) or not 0 <= window[0] < window[1]:
        raise ValueError("Invalid delay_window_ns")
    if plot["y_transform"] != "counts_divided_by_range_fourth_power":
        raise ValueError("Unsupported plot y_transform")
    lower, upper = plot.get("range_axis_min_m", 0), plot.get("range_axis_max_m")
    if type(lower) not in (int, float) or not math.isfinite(lower) or lower < 0:
        raise ValueError("range_axis_min_m must be finite and nonnegative")
    if upper is not None and (type(upper) not in (int, float) or not math.isfinite(upper) or upper <= lower):
        raise ValueError("range_axis_max_m must exceed range_axis_min_m or be null")
    settings["output_dir"] = str((path.parent / settings.get("output_dir", "output")).resolve())
    settings["settings_source"] = str(path)
    return settings, profile


def snapshot_settings(settings, out):
    """Freeze the exact inputs before opening the device; load these copies."""
    snapshot = dict(settings, system_ini="system.ini", device_ini="device.ini")
    (out / "lidar-settings.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    (out / "device.ini").write_text(Path(settings["device_ini"]).read_text())
    source = Path(settings["system_ini"]).read_text()
    (out / "system-source.ini").write_text(source)
    system = configparser.ConfigParser()
    system.optionxform = str
    system.read_string(source)
    if not system.has_section("Paths"):
        system.add_section("Paths")
    system.set("Paths", "Data", str(out / "snapi"))
    with (out / "system.ini").open("w") as handle:
        system.write(handle)


def configure(sn, settings, profile, out):
    from snAPI.Constants import MeasMode
    if not sn.getDevice(settings["device_serial"]) or not sn.initDevice(MeasMode[profile["mode"]]):
        raise RuntimeError("Device initialization failed")
    if not sn.loadIniConfig(str(out / "device.ini")):
        raise RuntimeError("Device configuration rejected")
    if profile["mode"] == "T2":
        sn.histogram.setRefChannel(0)  # Dedicated SYNC
        sn.histogram.setBinWidth(profile["bin_width_ps"])
        if not sn.histogram.setNumBins(profile["num_bins"]):
            raise RuntimeError("Histogram configuration rejected")
    elif not sn.device.setBinning(profile["binning_code"]):
        raise RuntimeError("T3 binning rejected")
    if profile["save_ptu"] and not sn.setPTUFilePath(str(out / "measurement.ptu")):
        raise RuntimeError("PTU path rejected")


def check_rates(settings, rates):
    if abs(rates[0] - settings["expected_sync_rate_hz"]) > settings["sync_rate_tolerance_hz"]:
        raise RuntimeError(f"SYNC rate outside configured tolerance: {rates}")


def check_histogram(profile, data, bins):
    width = profile.get("expected_bin_width_ps", profile.get("bin_width_ps"))
    if len(bins) < 2 or abs(float(bins[1] - bins[0]) - width) > 1e-6:
        raise RuntimeError(f"Histogram bin width differs from requested {width} ps")
    if profile["export_bins"] > len(bins):
        raise RuntimeError("export_bins exceeds returned histogram length")
    if profile["mode"] == "T2" and len(bins) != profile["num_bins"]:
        raise RuntimeError("Returned histogram length differs from configuration")
