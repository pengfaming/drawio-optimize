#!/usr/bin/env python3
"""Environment and optional-tool report for drawio-optimize."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drawio_optimize.cli import main

if __name__ == "__main__":
    raise SystemExit(main("doctor"))
