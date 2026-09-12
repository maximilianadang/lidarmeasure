"""Native Python helpers: clean environment, readiness, and guaranteed cleanup."""
from contextlib import contextmanager
import os
import selectors
import subprocess


def native_env():
    return {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH')}


@contextmanager
def background(command, log_path, ready, *, graceful=False, process_handle=False):
    with log_path.open('w') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, env=native_env(), text=True)
        failed = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                message = process.stdout.readline().strip() if selector.select(15) else ''
            if not message.startswith(ready): raise RuntimeError(f'Helper failed to start; see {log_path}')
            yield process if process_handle else message
        except BaseException:
            failed = True
            raise
        finally:
            if not graceful and process.poll() is None: process.terminate()
            try:
                process.communicate('\n' if graceful else None, timeout=10 if graceful else 3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            if graceful and process.returncode and not failed:
                raise RuntimeError(f'Helper failed; recorded data preserved; see {log_path}')
