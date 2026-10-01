import argparse
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description="Run ground-station unit tests")
    parser.add_argument("-p", "--pattern", default="test_*.py")
    args = parser.parse_args()
    component = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(component / "src"), str(component / "packaging")]
    suite = unittest.defaultTestLoader.discover(str(component / "tests"), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
