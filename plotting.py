"""Launch plotting with the native host's matched NumPy/Matplotlib packages."""
from pathlib import Path
import subprocess
from native_process import native_env


def generate_plots(out, script="plot_histogram.py"):
    subprocess.run(['/usr/bin/python3', '-I', str(Path(__file__).with_name(script)), str(out)],
                   env=native_env(), check=True)


def mark_incomplete(fig, directory):
    """Keep salvaged plots visibly distinct from successful recordings."""
    import json
    path = directory / 'summary.json'
    if path.exists():
        summary = json.loads(path.read_text())
        if summary.get('status') == 'failed' or summary.get('data_integrity_warnings'):
            fig.suptitle('INCOMPLETE / FAILED RECORDING — saved data only; gaps may be present', color='darkred', fontsize=11)
