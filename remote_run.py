"""Run the hardware launcher on Dusty and open its preview on this computer."""
import getpass
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_preview import PREVIEW_HOST, PREVIEW_READY, preview_forward_args, preview_ssh_target
from run_paths import ROOT


REMOTE_REPO = 'workspace/terraforming_mars/lidarmeasure'
PREVIEW_PORT = re.compile(re.escape(PREVIEW_READY.encode()) + rb'(\d+)(?:\s|$)')


def open_preview(port, target, control_socket):
    url = f'http://{PREVIEW_HOST}:{port}'
    forward = ['ssh', '-fM', '-S', control_socket, '-o', 'BatchMode=yes',
               '-o', 'ConnectTimeout=5', *preview_forward_args(port, target)]
    try:
        subprocess.run(forward, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        print(f'Live preview tunnel unavailable. Acquisition continues on Dusty. {error}',
              file=sys.stderr, flush=True)
        return
    print(f'Local live preview: {url}', flush=True)
    try:
        # Use a separate process so browser startup cannot hold up SSH output or shutdown.
        subprocess.Popen([sys.executable, '-m', 'webbrowser', '-t', url],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except OSError as error:
        print(f'Could not open your browser automatically. Open {url} manually. {error}',
              file=sys.stderr, flush=True)


def main(args):
    target = preview_ssh_target()
    user, _, host = target.rpartition('@')
    hostname = socket.gethostname().lower()
    if user == getpass.getuser() and host.lower() in (hostname, f'{hostname}.local'):
        launcher = str(ROOT / 'run-python')
        os.execve(launcher, [launcher, *args], {**os.environ, 'LIDAR_LAUNCH_MODE': 'direct'})

    remote = f'cd "$HOME"/{REMOTE_REPO} && exec ' + shlex.join([
        'env', 'LIDAR_LAUNCH_MODE=ssh', './run-python', *args,
    ])
    # With an interactive terminal, SSH forwards Ctrl+C to the remote foreground group.
    tty = '-tt' if sys.stdin.isatty() else '-T'
    ssh = ['ssh', tty, '-o', 'ConnectTimeout=10', '--', target, remote]
    with TemporaryDirectory(prefix='lidar-ssh-', dir='/tmp') as directory, subprocess.Popen(ssh, stdout=subprocess.PIPE) as process:
        control_socket = Path(directory) / 'preview.sock'
        preview = None
        try:
            for line in process.stdout:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
                ready = PREVIEW_PORT.match(line)
                if ready and preview is None:
                    preview = Thread(target=open_preview, args=(int(ready[1]), target, control_socket))
                    preview.start()
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
            if preview is not None:
                preview.join()
            if control_socket.exists():
                try:
                    subprocess.run(['ssh', '-S', control_socket, '-O', 'exit', '--', target],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired) as error:
                    print(f'Could not close the preview tunnel. {error}', file=sys.stderr)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
    except OSError as error:
        sys.exit(f'Unable to launch acquisition: {error}')
