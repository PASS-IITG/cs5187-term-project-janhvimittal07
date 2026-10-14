"""Test the coverage-guided loop on the real coverage/ASan builds with a fake model.

    FUZZ_WORK=/path/with/builds python llm/test_loop.py

Runs under FUZZ_WORK with the run name "selftest" and deletes its own outputs
afterwards. Needs build-cov, duckdb/ and corpus/seeds under FUZZ_WORK.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REAL = Path(os.environ.get("FUZZ_WORK", HERE.parent))
for need in ("build-cov/duckdb", "duckdb", "corpus/seeds"):
    if not (REAL / need).exists():
        sys.exit(f"missing {REAL / need}; set FUZZ_WORK to your work dir")

REPLY = """```sql
SELECT a, count(*) FROM t GROUP BY ROLLUP(a);
SELECT a FROM t ORDER BY a DESC LIMIT 2;
SELECT sum(a) OVER (PARTITION BY a % 2 ORDER BY a) FROM t;
```"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        b = json.dumps({"choices": [{"message": {"content": REPLY}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/v1"

import shutil

RUN = "selftest"
with tempfile.TemporaryDirectory() as tmp:
    gen = Path(tmp) / "gen_out"
    gen.mkdir()
    (gen / "s1.sql").write_text("CREATE TABLE t(a INTEGER);\nINSERT INTO t SELECT range FROM range(5000);\n")
    env = dict(os.environ, FUZZ_WORK=str(REAL), LLM_BASE_URL=url, LLM_MODEL="fake")
    artifacts = [REAL / "corpus" / "llm" / RUN, REAL / "results" / f"loop-{RUN}.jsonl",
                 REAL / "results" / f"cov-loop-{RUN}"]
    try:
        r = subprocess.run([sys.executable, str(HERE / "loop.py"), "--run", RUN, "--rounds", "2",
                            "--prompts", "2", "--per-prompt", "3", "--warm-seeds", "3",
                            "--schemas", str(gen), "--no-asan"],
                           capture_output=True, text=True, env=env, timeout=1500)
        print(r.stdout[-1500:])
        assert r.returncode == 0, r.stderr[-2000:]
        rows = [json.loads(x) for x in (REAL / "results" / f"loop-{RUN}.jsonl").read_text().splitlines()]
        assert len(rows) == 2, rows
        assert all(x["n_valid"] > 0 and x["n_scripts"] > 0 for x in rows), rows
        assert rows[0]["targets"] == 2, rows[0]
        assert rows[-1]["lines_hit"] >= rows[0]["lines_hit"] > 0, rows
        assert (REAL / "corpus" / "llm" / RUN).exists()
    finally:                                   # leave the real results untouched
        for a_ in artifacts:
            shutil.rmtree(a_, ignore_errors=True) if a_.is_dir() else a_.unlink(missing_ok=True)
print("loop ok: targets from uncovered regions, scripts run on the coverage build, per-round metrics")
