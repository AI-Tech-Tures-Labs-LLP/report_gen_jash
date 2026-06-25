"""
Chat pipeline benchmark — runs 4 test questions through the full optimized
chat pipeline exactly as the /ask endpoint does:

  Change 1: Interpreter uses Haiku instead of Sonnet
  Change 2: Intent classification + schema/rels/profile cache warm run in
            parallel before the SQL agent starts

Cache is reset before each question to simulate a real cold-cache request
(first hit after startup or TTL expiry) so Change 2 has real work to do.

Run from the backend/ directory:
    python benchmark_chat.py
"""

import concurrent.futures as _futures
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core import config
from core.config import estimate_cost
from services.claude_report_llm import classify_query_intent, answer_chat_question

QUESTIONS = [
    "Product which has the highest sales",
    "Customer who got highest sales",
    "Which customer has the highest number of orders which are fulfilled?",
    "Tell me all products which has not been sold since oct 2025",
]

SEP = "=" * 72


def _cost_for_log(log_entry: dict) -> float:
    return estimate_cost(
        log_entry.get("model", ""),
        input_tokens          = log_entry.get("input_tokens", 0),
        output_tokens         = log_entry.get("output_tokens", 0),
        cache_read_tokens     = log_entry.get("cache_read_tokens", 0),
        cache_creation_tokens = log_entry.get("cache_creation_tokens", 0),
    )


def _reset_caches() -> None:
    """Expire all in-memory caches so each question starts cold."""
    import db.schema as _s
    import db.relationships as _r
    import db.profiler as _p
    _s._schema_cache = None
    _s._cache_ts = 0.0
    _r._rel_cache = None
    _r._rel_cache_ts = 0.0
    _p._profile_cache = None
    _p._profile_ts = 0.0


def _parallel_intent_and_warm(question: str) -> dict:
    """Run intent classify + schema/rels/profile warm concurrently.
    Returns the intent result. Schema functions populate their module-level
    caches as a side effect so answer_chat_question() reads from cache.
    """
    from db.schema import format_schema
    from db.relationships import format_relationships
    from db.profiler import get_data_profile

    with _futures.ThreadPoolExecutor(max_workers=2) as pool:
        intent_f = pool.submit(classify_query_intent, question, None, "")
        schema_f = pool.submit(lambda: (format_schema(), format_relationships(), get_data_profile()))
        intent = intent_f.result()
        schema_f.result()
    return intent


def run_benchmark():
    print(SEP)
    print("  CHAT PIPELINE BENCHMARK  (Changes 1-7 active)")
    print(f"  {len(QUESTIONS)} questions")
    print(f"  Sonnet: {config.CLAUDE_MODEL}  |  Haiku: {config.CLAUDE_HAIKU_MODEL}")
    print(f"  Ch1: Interpreter -> Haiku")
    print(f"  Ch2: Intent + schema warm in parallel")
    print(f"  Ch3: SQL Agent max_tokens=4096")
    print(f"  Ch4: SQL Agent no row echo")
    print(f"  Ch5: Mongo write -> background thread (UI only)")
    print(f"  Ch6: Validator warn-only for heuristic patterns")
    print(f"  Ch7: Interpreter input column pruning")
    print(f"  Ch8: History fetch parallel with intent+schema (UI only)")
    print(SEP)

    all_results = []

    for idx, question in enumerate(QUESTIONS, 1):
        print(f"\n{'─'*72}")
        print(f"  Q{idx}: {question}")
        print(f"{'─'*72}")

        record = {
            "q_idx": idx, "question": question,
            "parallel_step_s": 0.0, "chat_time_s": 0.0, "total_time_s": 0.0,
            "intent_mode": "", "intent_complexity": "",
            "sql": "", "row_count": 0, "answer": "", "insights": "",
            "agents": [],
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read_tokens": 0, "total_cache_creation_tokens": 0,
            "total_cost_usd": 0.0, "error": None,
        }

        try:
            # Reset caches so parallel warm has real DB work to do
            _reset_caches()

            # ── Step 1: intent + schema warm in parallel (Change 2) ───────────
            print(f"  [1/2] Intent + schema warm (parallel) ...", end=" ", flush=True)
            t0 = time.time()
            intent = _parallel_intent_and_warm(question)
            parallel_elapsed = time.time() - t0
            record["parallel_step_s"] = round(parallel_elapsed, 2)

            mode = intent.get("mode", "data")
            complexity = intent.get("complexity", "complex")
            record["intent_mode"] = mode
            record["intent_complexity"] = complexity
            print(f"done ({parallel_elapsed:.2f}s)  mode={mode}  complexity={complexity}")
            print(f"         reason: {intent.get('reason', '')}")

            if mode != "data":
                print(f"  Skipping SQL — mode is '{mode}'")
                record["error"] = f"Non-data intent: {mode}"
                all_results.append(record)
                continue

            from core import config as _cfg
            use_haiku = complexity == "simple"
            sql_model = _cfg.CLAUDE_HAIKU_MODEL if use_haiku else None
            print(f"  SQL model: {'Haiku (simple)' if use_haiku else 'Sonnet (complex)'}")

            # ── Step 2: chat pipeline (schema already warm from step 1) ───────
            print(f"\n  [2/2] Chat pipeline (schema cache warm, interpreter=Haiku) ...")
            t1 = time.time()
            final_data = None

            for event in answer_chat_question(question, sql_model=sql_model):
                stage = event.get("stage", "")
                if stage == "analyze":
                    print(f"         analyze ...", end=" ", flush=True)
                elif stage == "sql":
                    print(f"sql ...", end=" ", flush=True)
                elif stage == "execute":
                    rows = event.get("data", {}).get("row_count", "?")
                    print(f"execute ({rows} rows) ...", end=" ", flush=True)
                elif stage == "interpret":
                    print(f"interpret (Haiku) ...", end=" ", flush=True)
                elif stage == "complete":
                    print(f"done")
                    final_data = event.get("data", {})

            chat_elapsed = time.time() - t1
            record["chat_time_s"]  = round(chat_elapsed, 2)
            record["total_time_s"] = round(parallel_elapsed + chat_elapsed, 2)

            if final_data:
                record["sql"]       = final_data.get("sql", "")
                record["row_count"] = len(final_data.get("data") or [])
                record["answer"]    = final_data.get("answer", "")
                record["insights"]  = final_data.get("insights", "")
                for ag in final_data.get("metrics", {}).get("agents", []):
                    record["agents"].append(ag)
                    record["total_input_tokens"]          += ag.get("input_tokens", 0)
                    record["total_output_tokens"]         += ag.get("output_tokens", 0)
                    record["total_cache_read_tokens"]     += ag.get("cache_read_tokens", 0)
                    record["total_cache_creation_tokens"] += ag.get("cache_creation_tokens", 0)
                    record["total_cost_usd"]              += _cost_for_log(ag)

            # ── Per-question result ───────────────────────────────────────────
            print(f"\n  {'─'*60}")
            print(f"  RESULT Q{idx}")
            print(f"  {'─'*60}")
            print(f"  Parallel step : {record['parallel_step_s']:.2f}s  (intent + schema overlapped)")
            print(f"  Chat pipeline : {record['chat_time_s']:.2f}s")
            print(f"  TOTAL         : {record['total_time_s']:.2f}s")
            print(f"  Rows returned : {record['row_count']}")
            print(f"  SQL           :\n    {record['sql'][:300].replace(chr(10), chr(10)+'    ')}")
            print(f"\n  Answer:\n    {record['answer']}")
            if record["insights"]:
                print(f"\n  Insights:\n    {record['insights'][:400]}")
            print(f"\n  Tokens:")
            print(f"    Input          : {record['total_input_tokens']:,}")
            print(f"    Output         : {record['total_output_tokens']:,}")
            print(f"    Cache read     : {record['total_cache_read_tokens']:,}")
            print(f"    Est. cost      : ${record['total_cost_usd']:.5f}")
            if record["agents"]:
                print(f"\n  Per-agent breakdown:")
                for ag in record["agents"]:
                    print(
                        f"    [{ag['agent']:<22}]  "
                        f"{ag.get('elapsed_ms',0)/1000:.2f}s  "
                        f"in={ag.get('input_tokens',0):,}  "
                        f"out={ag.get('output_tokens',0):,}  "
                        f"cache_hit={ag.get('cache_read_tokens',0):,}  "
                        f"rounds={ag.get('tool_rounds',1)}"
                    )

        except Exception as exc:
            import traceback
            print(f"\n  ERROR: {exc}")
            traceback.print_exc()
            record["error"] = str(exc)

        all_results.append(record)

    # ── Aggregate summary ─────────────────────────────────────────────────────
    successful = [r for r in all_results if not r["error"]]

    print(f"\n{SEP}")
    print("  AGGREGATE SUMMARY")
    print(SEP)
    print(f"  {'#':<4} {'Question':<46} {'Total':>7} {'Parallel':>9} {'Chat':>7} {'Rows':>5} {'Cost':>9}")
    print(f"  {'─'*4} {'─'*46} {'─'*7} {'─'*9} {'─'*7} {'─'*5} {'─'*9}")
    for r in all_results:
        q = r["question"][:45]
        if r["error"]:
            print(f"  {r['q_idx']:<4} {q:<46}  ERROR: {r['error']}")
        else:
            print(
                f"  {r['q_idx']:<4} {q:<46} "
                f"{r['total_time_s']:>6.1f}s "
                f"{r['parallel_step_s']:>8.2f}s "
                f"{r['chat_time_s']:>6.1f}s "
                f"{r['row_count']:>5} "
                f"${r['total_cost_usd']:>8.5f}"
            )

    if successful:
        avg_total    = sum(r["total_time_s"]       for r in successful) / len(successful)
        avg_parallel = sum(r["parallel_step_s"]    for r in successful) / len(successful)
        avg_chat     = sum(r["chat_time_s"]        for r in successful) / len(successful)
        total_cost   = sum(r["total_cost_usd"]     for r in successful)
        avg_cost     = total_cost / len(successful)
        print(f"  {'─'*72}")
        print(
            f"  {'AVG':<4} {'─'*46} "
            f"{avg_total:>6.1f}s "
            f"{avg_parallel:>8.2f}s "
            f"{avg_chat:>6.1f}s "
            f"{'─':>5} "
            f"${avg_cost:>8.5f}"
        )
        print(f"\n  Total cost for {len(successful)} questions: ${total_cost:.5f}")

    print(f"\n  BASELINE COMPARISON (from original benchmark, no optimizations):")
    print(f"  Q1: 22.6s  Q2: 15.7s  Q3: 17.4s  Q4: 46.5s  AVG: 25.6s")
    print(SEP)

    out_path = HERE / "benchmark_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Raw results saved to: {out_path}")
    print(SEP)


if __name__ == "__main__":
    run_benchmark()
