"""Install optional dependencies only; never logs into QQ or enables hybrid chat."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from optional_engine_setup import install

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.root)
        print('Optional dialogue environment prepared. No account or key was printed.')
    except Exception:
        print('Optional installation did not finish. Existing configuration was kept; see the local setup page.')
        raise SystemExit(1)
