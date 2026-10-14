"""End-to-end test of client + generator against a fake OpenAI-compatible server.

    python llm/test_generate.py

No real model is needed. The fake server returns 2 valid queries, 1 query with a
wrong column name, and 1 forbidden ATTACH; on the repair prompt it returns the fix.
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
sys.path.insert(0, str(HERE))
from client import LLM, LLMError  # noqa: E402

calls = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"data": [{"id": "fake-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        calls.append("repair" if "failed on DuckDB" in prompt else "generate")
        if "failed on DuckDB" in prompt:
            text = "```sql\nSELECT count(*) FROM t WHERE a > 1;\n```"
        else:
            text = ("Here you go:\n```sql\nSELECT a, count(*) FROM t GROUP BY a;\n"
                    "SELECT sum(a) OVER (ORDER BY a) FROM t;\n"
                    "SELECT nope_column FROM t;\n"
                    "ATTACH 'x.db';\n```")
        self._send({"choices": [{"message": {"content": text}}]})


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/v1"

# --- P1: client
c = LLM(base_url=url, model="fake-model")
assert c.models() == ["fake-model"]
assert "SELECT" in c.complete("hello")
try:
    LLM(base_url="http://127.0.0.1:9/v1", model="x", timeout=2).complete("hi")
    raise SystemExit("expected LLMError")
except LLMError:
    pass
print("client ok")

# --- P2: generator through the real CLI
with tempfile.TemporaryDirectory() as tmp:
    gen = Path(tmp) / "gen_out"
    gen.mkdir()
    (gen / "s1.sql").write_text("CREATE TABLE t(a INTEGER);\nINSERT INTO t VALUES (1),(2),(3);\n")
    env = dict(os.environ, FUZZ_WORK=tmp, LLM_BASE_URL=url, LLM_MODEL="fake-model")
    r = subprocess.run([sys.executable, str(HERE / "generate.py"), "--run", "t1", "--n-prompts", "2",
                        "--per-prompt", "4", "--schemas", str(gen), "--targets", "hints"],
                       capture_output=True, text=True, env=env, timeout=120)
    print(r.stdout.strip())
    assert r.returncode == 0, r.stderr
    files = sorted((Path(tmp) / "corpus" / "llm" / "t1").glob("*.sql"))
    assert len(files) == 2, files
    text = files[0].read_text()
    assert "GROUP BY a" in text and "OVER (ORDER BY a)" in text, text
    assert "nope_column" not in text, "invalid query should have been dropped or repaired"
    assert "count(*) FROM t WHERE a > 1" in text, "repair result should be kept"
    assert "ATTACH" not in text, "forbidden statement must be filtered"
    rows = [json.loads(x) for x in (Path(tmp) / "results" / "llm-t1.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["n_valid_first"] == 2 and rows[0]["n_repaired"] == 1, rows[0]
    assert rows[0]["target_source"] == "hint", rows[0]
    # 1 generate call came from the client check above, then 2 prompts x (generate + repair)
assert calls.count("generate") == 3 and calls.count("repair") == 2, calls
print("generator ok: filter, EXPLAIN validation, one repair round, metrics, hint targets")
