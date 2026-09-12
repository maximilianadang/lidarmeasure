"""Launch plotting with the native host's matched NumPy/Matplotlib packages."""
from pathlib import Path
import subprocess
from native_process import native_env


def generate_plots(out, script="plot_histogram.py"):
    subprocess.run(['/usr/bin/python3', '-I', str(Path(__file__).with_name(script)), str(out)],
                   env=native_env(), check=True)
