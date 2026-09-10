"""Launch plotting with the native host's matched NumPy/Matplotlib packages."""
import os
from pathlib import Path
import subprocess


def generate_plots(out, script="plot_histogram.py"):
    env = dict(os.environ)
    for key in ('LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME'):
        env.pop(key, None)
    subprocess.run(['/usr/bin/python3', '-I', str(Path(__file__).with_name(script)), str(out)],
                   env=env, check=True)
