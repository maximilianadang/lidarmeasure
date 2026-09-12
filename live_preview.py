"""Display the single latest-batch snapshot without reading the recording archive."""
from contextlib import contextmanager
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_process import background


def publish(out, delay_ps, channels, profile, channel, batch, elapsed=None):
    import numpy as np
    background = profile.get('kind') == 'background'
    if background:
        events = elapsed[channels == channel]
        limit = profile.get('preview_max_events', 10000)
        snapshot = dict(batch=batch, channel=channel, event_times_s=events[-limit:].tolist(),
                        total_events=len(events), omitted_events=max(0, len(events)-limit))
    else:
        width = profile.get('bin_width_ps', profile.get('expected_bin_width_ps'))
        bins = profile.get('num_bins', 1200)
        selected = delay_ps[channels == channel]
        indices = np.floor(selected / width).astype(np.int64)
        indices = indices[(indices >= 0) & (indices < bins)]
        counts = np.bincount(indices, minlength=bins)
        snapshot = dict(batch=batch, channel=channel, bin_width_ps=width, counts=counts.tolist())
    path = out / 'preview.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot))
    temporary.replace(path)


@contextmanager
def preview_server(settings, out):
    config = settings.get('preview', {})
    if not config.get('enabled', True):
        yield
        return
    command = ['/usr/bin/python3', '-I', '-u', str(Path(__file__).resolve()), str(out),
               '--port', str(config.get('port', 8765)), '--refresh-ms', str(config.get('refresh_ms', 500))]
    with background(command, out / 'logs/preview.log', 'Live preview:') as message:
        print(message, flush=True)
        yield


def serve(directory, port, refresh_ms):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    settings = json.loads((directory / 'lidar-settings.json').read_text())['plot']
    static = {
        '/': (Path(__file__).with_name('live_preview.html').read_bytes(), 'text/html; charset=utf-8'),
        '/config.json': (json.dumps(dict(plot=settings, refresh_ms=refresh_ms, run=directory.name)).encode(), 'application/json'),
    }
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            route = self.path.split('?', 1)[0]
            try:
                body, kind = ((directory / 'preview.json').read_bytes(), 'application/json') if route == '/preview.json' else static[route]
            except (KeyError, FileNotFoundError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass

    with HTTPServer(('127.0.0.1', port), Handler) as server:
        print(f'Live preview: http://127.0.0.1:{server.server_port} (forward this port over SSH)', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt: pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--refresh-ms', type=int, default=500)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.refresh_ms < 100: parser.error('refresh-ms must be at least 100')
    if not 1 <= args.port <= 65535: parser.error('port must be between 1 and 65535')
    serve(args.run_directory.resolve(), args.port, args.refresh_ms)


if __name__ == '__main__': main()
