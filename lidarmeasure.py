"""Record lidar events, mount coordinates, and plots."""
import os
import sys
from acquisition import run

def main(moving=False):
    try: run('measurement', moving=moving)
    except KeyboardInterrupt: sys.exit(130)
    except Exception as error:
        print(f"ERROR: {error}\nDetails: {os.environ.get('LIDAR_RUN_DIR', 'output')}/summary.json", file=sys.stderr, flush=True)
        sys.exit(1)

if __name__ == '__main__': main()
