import argparse

from fsw import __version__
from fsw.config import load_config


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fsw", description="固件码表")
    parser.add_argument(
        "--version", action="version", version=f"firmware-stopwatch {__version__}"
    )
    parser.parse_args(argv)
    print(f"firmware-stopwatch {__version__}")
    for problem in load_config().problems():
        print(f"· {problem}")


if __name__ == "__main__":
    main()
