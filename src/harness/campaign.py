"""Long, resumable fuzzing campaign.  Owner: Niket.  Task: N6.

    python harness/campaign.py --hours 8

TODO: loop { run a stage of fuzz.py (baseline / generate), triage new crashes,
  rewrite results/REPORT.md, sleep }, with state in results/campaign.json so it
  can be stopped and resumed; log time budget and counters.
"""
from __future__ import annotations


def main() -> None:
    raise NotImplementedError("N6")


if __name__ == "__main__":
    main()
