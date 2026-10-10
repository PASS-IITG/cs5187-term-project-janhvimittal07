"""Generate diverse schemas + data as SQL scripts.   Owner: Jahnvi.  Task: J4.

    python gen/gen_schemas.py --n 50 --seed 0 --out gen/out
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path


def one_schema(rng: random.Random, i: int) -> str:
    raise NotImplementedError("J4: build a CREATE TABLE + INSERT script")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="gen/out")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(a.n):
        (out / f"schema_{i:03d}.sql").write_text(one_schema(rng, i))


if __name__ == "__main__":
    main()
