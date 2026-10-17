"""Long-running, resumable fuzzing campaign runner with periodic triage (Task N6).

Orchestrates long fuzzing campaigns on DuckDB under AddressSanitizer (ASan)
or code coverage builds, with:
1. Resumable execution: persistent checkpointing to disk so campaigns interrupted
   by signals (SIGINT, SIGTERM), timeouts, or crashes resume seamlessly.
2. Multi-engine generator: combines Schema-directed SQLsmith, Grammar-aware Seed Mutator,
   Metamorphic Oracles (NoREC & TLP), and Seed Corpus into a unified hybrid campaign.
3. In-process batch execution: high-throughput query execution with crash isolation.
4. Periodic triage & noise gating: automatically runs delta debugging, optimizer
   bisection, pinned-main triage (N2), and upstream dedupe with N8 noise filters.
5. Live reporting: updates results/campaign/CAMPAIGN_REPORT.md and timeline metrics.

Usage:
    # Run a 1-hour campaign with hybrid generators and periodic triage every 10 min
    python src/harness/campaign.py --hours 1 --mode hybrid --triage-interval 600

    # Run for 60 seconds with ASan
    python src/harness/campaign.py --seconds 60 --mode hybrid --build asan

    # Resume an interrupted campaign
    python src/harness/campaign.py --resume

    # Check status of the current campaign
    python src/harness/campaign.py --status

    # Force re-triaging of all pending campaign findings and update report
    python src/harness/campaign.py --report-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))

sys.path.insert(0, str(HERE))
import baseline  # noqa: E402
import fuzz  # noqa: E402
import oracle  # noqa: E402
import runner  # noqa: E402
import sqlsmith  # noqa: E402
import sweep_oracle  # noqa: E402
import triage  # noqa: E402

CAMPAIGN_DIR = WORK / "results" / "campaign"
CHECKPOINT_PATH = CAMPAIGN_DIR / "checkpoint.json"
CRASHES_LOG = CAMPAIGN_DIR / "crashes.jsonl"
FINDINGS_DIR = CAMPAIGN_DIR / "findings"
REPORT_PATH = CAMPAIGN_DIR / "CAMPAIGN_REPORT.md"
TIMELINE_LOG = CAMPAIGN_DIR / "timeline.jsonl"


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class GeneratorStats:
    queries: int = 0
    ok: int = 0
    errors: int = 0
    crashes: int = 0
    elapsed_seconds: float = 0.0

    @property
    def valid_pct(self) -> float:
        return round(100.0 * self.ok / self.queries, 1) if self.queries > 0 else 0.0

    @property
    def qps(self) -> float:
        return round(self.queries / self.elapsed_seconds, 1) if self.elapsed_seconds > 0 else 0.0


@dataclass
class CampaignState:
    campaign_id: str = ""
    build: str = "asan"
    mode: str = "hybrid"
    seed: int = 42
    target_seconds: float = 3600.0
    elapsed_seconds: float = 0.0
    start_timestamp: float = 0.0
    last_checkpoint_timestamp: float = 0.0
    last_triage_timestamp: float = 0.0
    status: str = "initialized"  # initialized, running, interrupted, completed
    total_queries: int = 0
    total_ok: int = 0
    total_errors: int = 0
    total_crashes: int = 0
    unique_crashes: int = 0
    seen_signatures: list[str] = field(default_factory=list)
    pending_triage: list[dict[str, Any]] = field(default_factory=list)
    triaged_finding_ids: list[str] = field(default_factory=list)
    generator_stats: dict[str, dict[str, Any]] = field(default_factory=dict)

    def save(self, path: Path = CHECKPOINT_PATH) -> None:
        """Atomically persist state to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.last_checkpoint_timestamp = time.time()
        data = asdict(self)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        tmp_path.replace(path)

    @classmethod
    def load(cls, path: Path = CHECKPOINT_PATH) -> CampaignState:
        if not path.exists():
            raise FileNotFoundError(f"No checkpoint file found at {path}")
        data = json.loads(path.read_text())
        return cls(**data)


# ---------------------------------------------------------------------------
# Campaign Engine & Orchestration
# ---------------------------------------------------------------------------

class CampaignRunner:
    def __init__(self, state: CampaignState,
                 triage_interval: float = 300.0,
                 checkpoint_interval: float = 30.0,
                 batch_size: int = 20,
                 max_triage_per_cycle: int = 10) -> None:
        self.state = state
        self.triage_interval = triage_interval
        self.checkpoint_interval = checkpoint_interval
        self.batch_size = batch_size
        self.max_triage_per_cycle = max_triage_per_cycle
        self._stop_requested = False
        self.rng = random.Random(state.seed)

        CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
        FINDINGS_DIR.mkdir(parents=True, exist_ok=True)

        # Initialize internal generator components
        self._init_generators()

        # Register signal handlers for clean interrupt & checkpointing
        self._register_signals()

    def _register_signals(self) -> None:
        def _handle_signal(sig, frame):
            print(f"\n[!] Signal {sig} received. Requesting graceful shutdown...", flush=True)
            self._stop_requested = True

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

    def _init_generators(self) -> None:
        """Initialize generator pools based on campaign mode."""
        print(f"[*] Initializing generators for mode: {self.state.mode} (build: {self.state.build})")

        # 1. SQLsmith & Metamorphic Schema Setup
        self.sqlsmith_gen = None
        self.sqlsmith_setup: list[str] = []
        self.meta_schemas: list[dict[str, list[dict[str, str]]]] = []

        if self.state.mode in ("hybrid", "sqlsmith", "metamorphic"):
            try:
                import duckdb
                con = duckdb.connect(":memory:")
                self.sqlsmith_setup = sqlsmith.populate_database_from_seeds(con)
                schema = oracle.extract_schema(con)
                con.close()
                if not schema:
                    con = duckdb.connect(":memory:")
                    self.sqlsmith_setup = sqlsmith.populate_database_from_seeds(con, max_seeds=0)
                    schema = oracle.extract_schema(con)
                    con.close()
                if schema:
                    self.sqlsmith_gen = sqlsmith.SQLsmithGenerator(schema, self.rng)
                    self.meta_schemas.append(schema)
                    print(f"    - SQLsmith initialized ({len(schema)} seed tables)")
                    print(f"    - Metamorphic Oracles (NoREC & TLP) initialized ({len(schema)} tables)")
            except Exception as e:
                print(f"    [!] Failed to initialize SQLsmith/schema: {e}")

        # 2. Seed Mutation Generator
        self.mutation_seeds: list[list[str]] = []
        if self.state.mode in ("hybrid", "mutation"):
            try:
                self.mutation_seeds = baseline.load_seeds()
                print(f"    - Seed Mutator initialized ({len(self.mutation_seeds)} seed files)")
            except Exception as e:
                print(f"    [!] Failed to load mutation seeds: {e}")

        # Ensure generator_stats dictionaries exist
        for gen_name in ("sqlsmith", "mutation", "metamorphic", "seeds"):
            if gen_name not in self.state.generator_stats:
                self.state.generator_stats[gen_name] = asdict(GeneratorStats())

    def _record_query_result(self, gen_name: str, outcome: str, elapsed: float) -> None:
        """Update global and per-generator execution counters."""
        self.state.total_queries += 1
        stats = self.state.generator_stats.setdefault(gen_name, asdict(GeneratorStats()))
        stats["queries"] += 1
        stats["elapsed_seconds"] += elapsed

        if outcome == "ok":
            self.state.total_ok += 1
            stats["ok"] += 1
        elif outcome in triage.CRASHY or outcome in ("timeout", "verify_diff"):
            self.state.total_crashes += 1
            stats["crashes"] += 1
        else:
            self.state.total_errors += 1
            stats["errors"] += 1

    def _handle_anomaly(self, kind: str, origin: str, query: str,
                        setup: list[str] | None = None,
                        outcome: str = "", signature: str = "",
                        differs: str = "") -> None:
        """Log crash/mismatch anomaly and enqueue for periodic triage if novel."""
        if not signature:
            norm_q = " ".join(query.split())
            signature = hashlib.sha1(f"{kind}:{outcome}:{norm_q}".encode()).hexdigest()[:12]

        is_novel = signature not in self.state.seen_signatures
        if is_novel:
            self.state.seen_signatures.append(signature)
            self.state.unique_crashes += 1

            anomaly = {
                "kind": kind,
                "origin": origin,
                "outcome": outcome,
                "signature": signature,
                "query": query,
                "setup": setup or [],
                "differs": differs,
                "timestamp": time.time(),
            }
            self.state.pending_triage.append(anomaly)

            # Append to crashes log
            with open(CRASHES_LOG, "a") as f:
                f.write(json.dumps(anomaly) + "\n")

            print(f"\n[!] NOVEL {kind.upper()} FOUND [{signature}] via {origin} (queued for triage)")

    # -----------------------------------------------------------------------
    # Generation Steps
    # -----------------------------------------------------------------------

    def _step_sqlsmith(self) -> None:
        if not self.sqlsmith_gen or not self.sqlsmith_gen.schema:
            return
        prelude_sql = "\n".join(self.sqlsmith_setup) + "\n" if self.sqlsmith_setup else ""
        chunk = []
        raw_queries = []
        for _ in range(self.batch_size):
            try:
                q = self.sqlsmith_gen.generate_query()
                qid = f"sqlsmith_{self.state.total_queries + len(chunk)}"
                chunk.append((qid, prelude_sql + q + ";\n"))
                raw_queries.append(q)
            except Exception:
                continue
        if not chunk:
            return

        t0 = time.time()
        res = runner.run_batch(chunk, build=self.state.build, timeout=10.0, isolate=True)
        dur = time.time() - t0

        for (qid, r), q_text in zip(res, raw_queries):
            self._record_query_result("sqlsmith", r.outcome, dur / len(chunk))
            if r.outcome in triage.CRASHY:
                self._handle_anomaly("crash", f"sqlsmith:{qid}", q_text,
                                     setup=self.sqlsmith_setup, outcome=r.outcome, signature=r.signature)

    def _step_mutation(self) -> None:
        if not self.mutation_seeds:
            return
        chunk = []
        raw_queries = []
        for _ in range(self.batch_size):
            s_list = self.rng.choice(self.mutation_seeds)
            s = self.rng.choice(s_list)
            mutated, _ = baseline.mutate_stmt(s, self.rng)
            qid = f"mut_{self.state.total_queries + len(chunk)}"
            chunk.append((qid, mutated + ";\n"))
            raw_queries.append(mutated)

        t0 = time.time()
        res = runner.run_batch(chunk, build=self.state.build, timeout=10.0, isolate=True)
        dur = time.time() - t0

        for (qid, r), q_text in zip(res, raw_queries):
            self._record_query_result("mutation", r.outcome, dur / len(chunk))
            if r.outcome in triage.CRASHY:
                self._handle_anomaly("crash", f"mutation:{qid}", q_text, outcome=r.outcome, signature=r.signature)

    def _step_metamorphic(self) -> None:
        if not self.meta_schemas:
            return
        schema = self.rng.choice(self.meta_schemas)
        tables = list(schema.keys())
        if not tables:
            return
        table = self.rng.choice(tables)
        cols = schema[table]

        # Interleave NoREC and TLP
        t0 = time.time()
        use_tlp = self.rng.random() < 0.5
        pairs = (oracle.generate_tlp_pairs(table, cols, self.rng, limit=self.batch_size)
                 if use_tlp else
                 oracle.generate_norec_pairs(table, cols, self.rng, limit=self.batch_size))

        if not pairs:
            return

        drv = runner.DuckDBRunner(self.state.build)
        try:
            for pair in pairs:
                p_type = pair.get("type", "norec")
                q_opt = pair.get("optimized", "")
                q_ref = pair.get("reference", "")
                pred = pair.get("predicate", "")

                if p_type == "norec":
                    mismatch, val_opt, val_ref = oracle.check_norec(drv, q_opt, q_ref)
                    outcome = "ok" if not mismatch else "value_mismatch"
                    self._record_query_result("metamorphic", outcome, 0.05)
                    if mismatch:
                        diff = f"NoREC mismatch: opt={val_opt} ref={val_ref} on WHERE {pred}"
                        self._handle_anomaly("value", f"norec:{table}", q_opt, setup=[],
                                             outcome="value_mismatch", differs=diff)
                else:
                    mismatch, val_whole, val_parts = oracle.check_tlp(drv, q_opt, q_ref)
                    outcome = "ok" if not mismatch else "value_mismatch"
                    self._record_query_result("metamorphic", outcome, 0.05)
                    if mismatch:
                        diff = f"TLP mismatch: whole={val_whole} parts={val_parts} on predicate {pred}"
                        self._handle_anomaly("value", f"tlp:{table}", q_opt, setup=[],
                                             outcome="value_mismatch", differs=diff)
        finally:
            drv.close()

    def run_one_generation_cycle(self) -> None:
        """Dispatch one generation batch according to active mode."""
        if self.state.mode == "sqlsmith":
            self._step_sqlsmith()
        elif self.state.mode == "mutation":
            self._step_mutation()
        elif self.state.mode == "metamorphic":
            self._step_metamorphic()
        elif self.state.mode == "hybrid":
            # Weighted mix: 40% SQLsmith, 35% Mutation, 25% Metamorphic
            p = self.rng.random()
            if p < 0.40 and self.sqlsmith_gen:
                self._step_sqlsmith()
            elif p < 0.75 and self.mutation_seeds:
                self._step_mutation()
            else:
                self._step_metamorphic()

    # -----------------------------------------------------------------------
    # Periodic Triage & Noise Filtering
    # -----------------------------------------------------------------------

    def run_triage_cycle(self) -> int:
        """Run triage on queued pending anomalies."""
        if not self.state.pending_triage:
            return 0

        to_process = self.state.pending_triage[:self.max_triage_per_cycle]
        self.state.pending_triage = self.state.pending_triage[self.max_triage_per_cycle:]

        print(f"\n[*] Starting Periodic Triage Cycle ({len(to_process)} findings to process)...")
        triaged_cnt = 0

        for item in to_process:
            kind = item["kind"]
            origin = item["origin"]
            query = item["query"]
            setup = item.get("setup", [])

            t0 = time.time()
            try:
                if kind == "crash":
                    stmts = setup + [query] if query else setup
                    finding = triage.triage_crash(stmts, origin=origin)
                else:
                    finding = triage.triage_value(setup, query, kind, item.get("differs", ""), origin=origin)
            except Exception as e:
                print(f"    [-] Triage error on {origin}: {e}")
                finding = None

            dur = time.time() - t0
            if finding:
                fid = finding.get("id", f"finding_{len(self.state.triaged_finding_ids)}")
                if fid not in self.state.triaged_finding_ids:
                    self.state.triaged_finding_ids.append(fid)
                    triaged_cnt += 1
                # Save into campaign findings directory
                fdir = FINDINGS_DIR / fid
                fdir.mkdir(parents=True, exist_ok=True)
                (fdir / "meta.json").write_text(json.dumps(finding, indent=2))
                repro_sql = triage.prelude(triage.CFG.get(finding.get("config", "default"), [])) + "\n".join(finding.get("minimal_sql") or [query])
                (fdir / "repro.sql").write_text(repro_sql)

                verdict = finding.get("upstream", {}).get("verdict", "untriaged")
                print(f"    [+] Triaged {fid} ({kind}/{finding.get('outcome')}) -> {verdict} ({dur:.1f}s)")
            else:
                print(f"    [-] Finding at {origin} did not reproduce ({dur:.1f}s)")

        self.state.last_triage_timestamp = time.time()
        self.generate_campaign_report()
        return triaged_cnt

    # -----------------------------------------------------------------------
    # Campaign Report Generation
    # -----------------------------------------------------------------------

    def generate_campaign_report(self) -> Path:
        """Generate comprehensive markdown report of campaign findings and throughput."""
        all_meta = []
        for mf in sorted(FINDINGS_DIR.glob("*/meta.json")):
            try:
                all_meta.append(json.loads(mf.read_text()))
            except Exception:
                continue

        # Also pull findings from main triage output if present
        for mf in sorted(triage.OUT.glob("*/meta.json")):
            try:
                d = json.loads(mf.read_text())
                if d.get("id") and not any(m.get("id") == d.get("id") for m in all_meta):
                    all_meta.append(d)
            except Exception:
                continue

        # Filter noise via N8 noise gates
        filtered_findings = []
        for f in all_meta:
            noise, reason = fuzz.is_noise_item(f)
            if not noise:
                filtered_findings.append(f)

        SEVERITY = {"crash": 0, "asan": 0, "internal_error": 1, "internal": 1, "value": 2, "value_mismatch": 2}
        filtered_findings.sort(key=lambda x: (
            SEVERITY.get(x.get("outcome", x.get("kind", "")), 3),
            x.get("id", "")
        ))

        qps = round(self.state.total_queries / self.state.elapsed_seconds, 1) if self.state.elapsed_seconds > 0 else 0.0
        valid_pct = round(100.0 * self.state.total_ok / self.state.total_queries, 1) if self.state.total_queries > 0 else 0.0

        lines = [
            "# DuckDB Directed Fuzzing Campaign Report",
            "",
            f"**Campaign ID:** `{self.state.campaign_id}`  ",
            f"**Status:** `{self.state.status.upper()}`  ",
            f"**Elapsed Time:** {self.state.elapsed_seconds:.1f}s ({self.state.elapsed_seconds / 60.0:.1f}m / {self.state.target_seconds / 60.0:.1f}m target)  ",
            f"**Build:** `{self.state.build}` | **Mode:** `{self.state.mode}` | **Random Seed:** `{self.state.seed}`  ",
            "",
            "## 1. Executive Summary & Throughput",
            "",
            "| Metric | Total | Rate / Percentage |",
            "| :--- | :--- | :--- |",
            f"| **Queries Executed** | {self.state.total_queries:,} | {qps} QPS |",
            f"| **Semantically Valid** | {self.state.total_ok:,} | {valid_pct}% |",
            f"| **Syntax / Catalog Errors** | {self.state.total_errors:,} | {100.0 - valid_pct:.1f}% |",
            f"| **Raw Anomalies Caught** | {self.state.total_crashes} | - |",
            f"| **Unique Triaged Defects** | {len(filtered_findings)} | - |",
            "",
            "## 2. Generator Performance Breakdown",
            "",
            "| Generator | Queries | Throughput (QPS) | Valid % | Crashes |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for gname, gstats in sorted(self.state.generator_stats.items()):
            g_q = gstats.get("queries", 0)
            g_ok = gstats.get("ok", 0)
            g_c = gstats.get("crashes", 0)
            g_t = gstats.get("elapsed_seconds", 0.0)
            g_qps = round(g_q / g_t, 1) if g_t > 0 else 0.0
            g_v = round(100.0 * g_ok / g_q, 1) if g_q > 0 else 0.0
            lines.append(f"| `{gname}` | {g_q:,} | {g_qps} | {g_v}% | {g_c} |")

        lines.extend([
            "",
            "## 3. Verified Findings & Upstream Triage (N8 Filtered)",
            "",
            f"Found {len(filtered_findings)} unique non-noise defects across all executed engines.",
            "",
            "| ID | Kind / Outcome | Config | Disabled Optimizers | Stmts | Stock Build | Pinned-Main (N2) | Upstream Match |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ])

        if not filtered_findings:
            lines.append("| *None* | - | - | - | - | - | - | - |")
        else:
            for f in filtered_findings:
                upstream = f.get("upstream") or {}
                st = f.get("stock_build") or f.get("asan") or {}
                mb = f.get("main_build") or f.get("main") or {}
                repro_st = st.get("reproduces") if "reproduces" in st else (st.get("outcome") in triage.CRASHY)
                repro_mb = mb.get("reproduces_on_main") if "reproduces_on_main" in mb else (mb.get("outcome") in triage.CRASHY)
                disabled = ",".join(f.get("disabled_optimizers") or []) or "none"
                lines.append(
                    f"| `{f.get('id', '-')}` | {f.get('kind', '-')}/{f.get('outcome', '-')} | "
                    f"`{f.get('config', 'default')}` | `{disabled}` | {f.get('n_statements', '-')} | "
                    f"{'yes' if repro_st else 'no'} | {'yes' if repro_mb else 'no'} | "
                    f"{upstream.get('verdict', 'untriaged')} |"
                )

        # Reproducer Details
        if filtered_findings:
            lines.extend(["", "## 4. Minimal Reproducers", ""])
            for f in filtered_findings:
                fid = f.get("id", "finding")
                repro_path = FINDINGS_DIR / fid / "repro.sql"
                if not repro_path.exists():
                    repro_path = triage.OUT / fid / "repro.sql"
                sql_content = repro_path.read_text().strip() if repro_path.exists() else "\n".join(f.get("minimal_sql", []))
                upstream = f.get("upstream", {})
                lines.extend([
                    f"### Finding `{fid}` ({f.get('kind', '-')}/{f.get('outcome', '-')})",
                    f"- **Origin:** `{f.get('origin', '-')}`",
                    f"- **Signature:** `{f.get('signature', '-')}`",
                    f"- **Upstream Verdict:** {upstream.get('verdict', 'untriaged')}",
                    "",
                    "```sql",
                    sql_content,
                    "```",
                    "",
                ])

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(lines) + "\n")
        return REPORT_PATH

    # -----------------------------------------------------------------------
    # Main Campaign Loop
    # -----------------------------------------------------------------------

    def run(self) -> CampaignState:
        """Execute the main campaign loop with checkpointing and periodic triage."""
        self.state.status = "running"
        if not self.state.start_timestamp:
            self.state.start_timestamp = time.time()
        if not self.state.last_triage_timestamp:
            self.state.last_triage_timestamp = time.time()

        t_loop_start = time.time()
        last_ckpt_time = time.time()
        last_triage_time = self.state.last_triage_timestamp

        print(f"\n{'=' * 65}")
        print(f"STARTING CAMPAIGN [{self.state.campaign_id}]")
        print(f"Target Budget: {self.state.target_seconds:.1f}s ({self.state.target_seconds / 60.0:.1f}m)")
        print(f"Elapsed So Far: {self.state.elapsed_seconds:.1f}s | Build: {self.state.build} | Mode: {self.state.mode}")
        print(f"Periodic Triage Every: {self.triage_interval:.1f}s | Checkpoint Every: {self.checkpoint_interval:.1f}s")
        print(f"{'=' * 65}\n")

        try:
            while not self._stop_requested:
                now = time.time()
                iter_dur = now - t_loop_start
                current_total_elapsed = self.state.elapsed_seconds + iter_dur

                # Check budget exhaustion
                if current_total_elapsed >= self.state.target_seconds:
                    print(f"\n[+] Campaign target budget of {self.state.target_seconds:.1f}s reached.")
                    self.state.status = "completed"
                    break

                # Run one generation batch
                self.run_one_generation_cycle()

                # Periodic Checkpoint
                if time.time() - last_ckpt_time >= self.checkpoint_interval:
                    self.state.elapsed_seconds += (time.time() - t_loop_start)
                    t_loop_start = time.time()
                    last_ckpt_time = time.time()
                    self.state.save()
                    qps = round(self.state.total_queries / self.state.elapsed_seconds, 1) if self.state.elapsed_seconds > 0 else 0.0
                    print(f"  ... [progress {self.state.elapsed_seconds:.0f}s/{self.state.target_seconds:.0f}s] "
                          f"queries={self.state.total_queries} ok={self.state.total_ok} "
                          f"crashes={self.state.total_crashes} unique={self.state.unique_crashes} "
                          f"QPS={qps}", flush=True)

                # Periodic Triage
                if time.time() - last_triage_time >= self.triage_interval:
                    self.run_triage_cycle()
                    last_triage_time = time.time()

        except KeyboardInterrupt:
            print("\n[!] KeyboardInterrupt received. Gracefully checkpointing...")
            self.state.status = "interrupted"
        finally:
            self.state.elapsed_seconds += (time.time() - t_loop_start)
            if self.state.status != "completed":
                self.state.status = "interrupted"

            # Final triage pass on remaining pending anomalies
            if self.state.pending_triage:
                print("\n[*] Running final triage on pending anomalies before saving...")
                self.run_triage_cycle()

            self.state.save()
            report_file = self.generate_campaign_report()
            print(f"\n[+] Campaign checkpoint saved to {CHECKPOINT_PATH}")
            print(f"[+] Campaign report updated at {report_file}")
            print(f"[+] Final status: {self.state.status} | Total queries: {self.state.total_queries} | Total elapsed: {self.state.elapsed_seconds:.1f}s\n")

        return self.state


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=0.0, help="Campaign duration in seconds")
    parser.add_argument("--minutes", type=float, default=0.0, help="Campaign duration in minutes")
    parser.add_argument("--hours", type=float, default=0.0, help="Campaign duration in hours")
    parser.add_argument("--budget", type=float, default=0.0, help="Alias for duration in seconds")
    parser.add_argument("--build", choices=["asan", "cov", "stock"], default="asan", help="DuckDB build target")
    parser.add_argument("--mode", choices=["hybrid", "sqlsmith", "mutation", "metamorphic"], default="hybrid",
                        help="Generator strategy (default: hybrid)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--triage-interval", type=float, default=300.0, help="Interval in seconds between periodic triage passes")
    parser.add_argument("--checkpoint-interval", type=float, default=30.0, help="Interval in seconds between state checkpoints")
    parser.add_argument("--batch-size", type=int, default=20, help="Query batch size per execution step")
    parser.add_argument("--resume", action="store_true", help="Resume previous campaign from checkpoint.json")
    parser.add_argument("--status", action="store_true", help="Display current checkpoint status and exit")
    parser.add_argument("--report-only", action="store_true", help="Regenerate CAMPAIGN_REPORT.md from disk and exit")
    args = parser.parse_args()

    # Handle status inquiry
    if args.status:
        if not CHECKPOINT_PATH.exists():
            print(f"[-] No active campaign checkpoint at {CHECKPOINT_PATH}")
            return
        state = CampaignState.load(CHECKPOINT_PATH)
        print(f"\nCampaign Status: {state.status.upper()}")
        print(f"ID:              {state.campaign_id}")
        print(f"Elapsed:         {state.elapsed_seconds:.1f}s / {state.target_seconds:.1f}s")
        print(f"Queries:         {state.total_queries:,} (Valid: {state.total_ok:,}, Errors: {state.total_errors:,})")
        print(f"Crashes Found:   {state.total_crashes} (Unique: {state.unique_crashes})")
        print(f"Pending Triage:  {len(state.pending_triage)}")
        print(f"Triaged Unique:  {len(state.triaged_finding_ids)}")
        return

    # Handle report-only request
    if args.report_only:
        state = CampaignState.load(CHECKPOINT_PATH) if CHECKPOINT_PATH.exists() else CampaignState()
        runner_inst = CampaignRunner(state)
        rf = runner_inst.generate_campaign_report()
        print(f"[+] Regenerated campaign report at {rf}")
        return

    # Determine campaign duration
    duration = 3600.0  # default 1 hour
    if args.hours > 0:
        duration = args.hours * 3600.0
    elif args.minutes > 0:
        duration = args.minutes * 60.0
    elif args.seconds > 0:
        duration = args.seconds
    elif args.budget > 0:
        duration = args.budget

    if args.resume:
        if not CHECKPOINT_PATH.exists():
            print(f"[!] Cannot resume: checkpoint file not found at {CHECKPOINT_PATH}")
            sys.exit(1)
        state = CampaignState.load(CHECKPOINT_PATH)
        print(f"[*] Resuming campaign {state.campaign_id} (elapsed: {state.elapsed_seconds:.1f}s / {state.target_seconds:.1f}s)")
        if duration != 3600.0:  # User explicitly specified a new budget
            state.target_seconds = state.elapsed_seconds + duration
    else:
        cid = f"camp_{int(time.time())}_{args.mode}_{args.build}"
        state = CampaignState(
            campaign_id=cid,
            build=args.build,
            mode=args.mode,
            seed=args.seed,
            target_seconds=duration,
        )

    runner_inst = CampaignRunner(
        state=state,
        triage_interval=args.triage_interval,
        checkpoint_interval=args.checkpoint_interval,
        batch_size=args.batch_size,
    )
    runner_inst.run()


if __name__ == "__main__":
    main()
