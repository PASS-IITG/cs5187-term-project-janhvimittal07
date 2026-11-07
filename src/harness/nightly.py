"""Latest DuckDB main nightly CLI, downloaded once per CI run (no rebuild; see the "Main" workflow artifacts).

    python harness/nightly.py            # prints the path of the CLI binary of the newest successful Main run

Read-only GitHub calls only (gh run list / gh api GET). The artifact is a raw tar.gz; it is unpacked in its own new folder
$FUZZ_WORK/ci-main-run<id>/ and the binary is run with -no-agent (AI_AGENT makes the shell print JSON).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402

WORK = runner.WORK


def gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-200:])
    return p.stdout


def latest_cli() -> Path:
    runs = json.loads(gh("run", "list", "--repo", "duckdb/duckdb", "--branch", "main", "--workflow", "Main", "--status", "success",
                         "--limit", "1", "--json", "databaseId"))
    if not runs:
        raise RuntimeError("no successful Main run")
    rid = runs[0]["databaseId"]
    d = WORK / f"ci-main-run{rid}"
    binp = d / "duckdb"
    if binp.exists():
        return binp
    arts = json.loads(gh("api", f"repos/duckdb/duckdb/actions/runs/{rid}/artifacts?per_page=100"))["artifacts"]
    art = next((a for a in arts if a["name"] == "duckdb-cli-linux-amd64.tar.gz" and not a.get("expired")), None)
    if art is None:
        raise RuntimeError(f"run {rid} has no duckdb-cli-linux-amd64 artifact")
    d.mkdir(parents=True, exist_ok=True)
    blob = d / "dl.bin"
    with open(blob, "wb") as fh:
        p = subprocess.run(["gh", "api", f"repos/duckdb/duckdb/actions/artifacts/{art['id']}/zip"], stdout=fh, timeout=900)
    if p.returncode != 0:
        raise RuntimeError("artifact download failed")
    with tarfile.open(blob) as tf:
        member = next(m for m in tf.getmembers() if m.isfile() and Path(m.name).name == "duckdb")
        member.name = "duckdb"
        tf.extract(member, d, filter="data")
    binp.chmod(0o755)
    return binp


def run_sql(binp: Path, sql: str, timeout: float = 120) -> tuple[int, str]:
    p = subprocess.run([str(binp), "-no-agent", "-csv"], input=sql, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def version(binp: Path) -> str:
    return run_sql(binp, "SELECT version() || ' ' || (SELECT source_id FROM pragma_version());")[1].strip().splitlines()[-1]


if __name__ == "__main__":
    b = latest_cli()
    print(b, version(b))
