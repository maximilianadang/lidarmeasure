"""Allocate one directory before launching Python so startup logs belong to the run."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def read_settings(path, seen=()):
    path = Path(path).resolve()
    if path in seen: raise ValueError('Circular settings inheritance')
    data = json.loads(path.read_text())
    base = read_settings(path.parent / data.pop('extends'), (*seen, path)) if 'extends' in data else {}
    def merge(target, values):
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict): merge(target[key], value)
            else: target[key] = value
    merge(base, data)
    for key in ('system_ini', 'device_ini', 'output_dir'):
        if key in data: base[key] = str((path.parent / data[key]).resolve())
    return base


def create_run(output, kind):
    path = Path(output) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + kind)
    (path / 'logs').mkdir(parents=True)
    (path / 'snapi').mkdir()
    return path


def prepare(arguments):
    script = Path(arguments[0]).name if arguments else 'python'
    kind = {'lidarmeasure.py': 'measurement', 'lidarmove.py': 'measurement',
            'histogram-simple.py': 'vendor-demo', 'probe.py': 'probe'}.get(script, 'python')
    config = ROOT / 'lidar-settings.json'
    for i, arg in enumerate(arguments):
        if arg == '--settings': config = Path(arguments[i+1]).resolve()
        elif arg.startswith('--settings='): config = Path(arg.split('=', 1)[1]).resolve()
    settings = read_settings(config)
    output = (config.parent / settings.get('output_dir', 'output')).resolve()
    return create_run(output, kind)


if __name__ == '__main__': print(prepare(sys.argv[1:]))
