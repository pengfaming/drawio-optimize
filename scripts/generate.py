#!/usr/bin/env python3
"""Entry point for `drawio-optimize generate`. Kept thin; logic lives in drawio_optimize.cli."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drawio_optimize.cli import main

if __name__ == "__main__":
    raise SystemExit(main("generate"))
