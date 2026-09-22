#!/usr/bin/env python3
"""Combine completed Brazil, Iceland, and Massachusetts analysis outputs."""

from pathlib import Path

from analysis import combine_event_outputs


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    counts = combine_event_outputs(root)
    print(
        "Combined political panel: "
        f"events={counts['events']} pairs={counts['pairs']} "
        f"panel_rows={counts['panel']} summary_rows={counts['summary']}"
    )
