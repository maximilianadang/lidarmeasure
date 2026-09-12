"""Relay snAPI output with a hardware-start banner and live elapsed timer."""
import queue
import signal
import re
import sys
import threading
import time

START = re.compile(r"MH_StartMeas:\s+\d+\s+(\d+)")
VENDOR = re.compile(r'\d{6}_\d{2}:\d{2}:\d{2}\.\d+ (DEB|INF|DEV) ')
STOP = re.compile(r"MH_StopMeas:|ACQUISITION FINISHED")


def relay(source, destination, timer_destination=None):
    timer_destination = timer_destination or destination
    inline = timer_destination.isatty()
    timer_visible = False
    messages = queue.Queue(maxsize=128)

    def clear_timer(ending='\r\033[2K'):
        nonlocal timer_visible
        if timer_visible:
            print(ending, end='', file=timer_destination, flush=True)
            timer_visible = False

    def read_lines():
        try:
            for line in source: messages.put(line)
        finally:
            messages.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    started = None
    duration = 0
    last_second = -1
    while True:
        try:
            line = messages.get(timeout=0.1)
        except queue.Empty: line = ''
        if line is None:
            clear_timer('\n')
            break
        if line:
            if VENDOR.search(line) and not (START.search(line) or STOP.search(line)): continue
            if timer_visible:
                clear_timer()
                last_second = -1
            destination.write(line)
            match = START.search(line)
            if match:
                duration = int(match.group(1)) / 1000
                started = time.monotonic()
                last_second = -1
                destination.write(
                    "\n\033[0m" + "=" * 68 + "\n"
                    "                 MEASUREMENT START NOW\n"
                    f"                 Duration: {duration:g} seconds\n"
                    "                 Timer follows receipt of MH_StartMeas\n"
                    + "=" * 68 + "\n\n"
                )
            elif STOP.search(line) and started is not None:
                elapsed = min(time.monotonic() - started, duration)
                destination.write(f'MEASUREMENT TIMER STOPPED: {elapsed:.1f} / {duration:g} s\n')
                started = None
        if started is not None:
            elapsed = min(time.monotonic() - started, duration)
            second = int(elapsed)
            if second != last_second or elapsed >= duration:
                if inline:
                    destination.flush()
                    timer_destination.write(f'\r\033[2K>>> MEASUREMENT: {elapsed:.1f} / {duration:g} s elapsed <<<')
                    timer_destination.flush()
                    timer_visible = True
                else:
                    destination.write(f'>>> MEASUREMENT: {elapsed:.1f} / {duration:g} s elapsed <<<\n')
                last_second = second
            if elapsed >= duration:
                clear_timer('\n')
                destination.write('Requested duration reached; waiting for acquisition completion and saving.\n')
                started = None
        destination.flush()


if __name__ == '__main__':
    # Acquisition handles Ctrl+C; keep draining its output until the pipe closes.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    relay(sys.stdin, sys.stdout, sys.stderr)
