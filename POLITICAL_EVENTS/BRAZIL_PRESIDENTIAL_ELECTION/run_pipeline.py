#!/usr/bin/env python3
"""Run this event family's configured political-market pipeline."""

from pathlib import Path
import sys


POLITICAL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POLITICAL_ROOT))

from pipeline import main  # noqa: E402


if __name__ == "__main__":
    main(default_event_root=Path(__file__).resolve().parent)

