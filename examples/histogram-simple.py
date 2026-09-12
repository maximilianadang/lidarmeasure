# Adapted from PicoQuant Demo_HistogramSimple.py.
"""Run the vendor T2 profile through the shared acquisition workflow."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acquisition import run

if __name__ == '__main__': run('vendor-demo')
