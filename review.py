#!/usr/bin/env python3
"""Run lean-vouch from a clone, without installing it. See lean_vouch/cli.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lean_vouch.cli import main  # noqa: E402

main()
