"""Allocate one directory before launching Python so startup logs belong to the run."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def create_run(output, kind):
    path = Path(output) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + kind)
    (path / 'logs').mkdir(parents=True)
    (path / 'snapi').mkdir()
    return path


def prepare(arguments):
    script = Path(arguments[0]).name if arguments else 'python'
    kind = {'capture-returns.py': 'capture', 'waterfall.py': 'waterfall',
            'histogram-simple.py': 'vendor-demo', 'probe.py': 'probe'}.get(script, 'python')
    config = ROOT / 'lidar-settings.json'
    for i, arg in enumerate(arguments):
        if arg == '--settings':
            config = Path(arguments[i+1]).resolve()
        elif arg.startswith('--settings='):
            config = Path(arg.split('=', 1)[1]).resolve()
    settings = json.loads(config.read_text())
    output = (config.parent / settings.get('output_dir', 'output')).resolve()
    return create_run(output, kind)


if __name__ == '__main__':
    print(prepare(sys.argv[1:]))
