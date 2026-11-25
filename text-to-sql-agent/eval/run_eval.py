"""Run the agent over the benchmark and score it by execution match.

    python -m eval.run_eval                      # full agent (reviewer on, 3 attempts)
    python -m eval.run_eval --no-reviewer        # ablation: single-shot SQL writer
    python -m eval.run_eval --limit 20 --difficulty hard

Writes per-question results to results/<run_name>.jsonl and a summary to
results/<run_name>_summary.json and .md.

Metrics:
- execution accuracy: predicted rows == gold rows (see eval/matching.py)
- failed query rate: final SQL was blocked by the guard or errored in Postgres
- latency: wall-clock time per question for the whole graph (no answer step)
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sqlagent.config import Settings
from sqlagent.factory import build_agent

from .matching import results_match

ROOT = Path(__file__).resolve().parent.parent

# Approximate USD per 1M tokens; override with --price-in / --price-out.
DEFAULT_PRICE_IN = 0.40
DEFAULT_PRICE_OUT = 1.60


def load_benchmark(path: Path, limit: int | None, difficulty: str | None) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if difficulty:
        rows = [r for r in rows if r["difficulty"] == difficulty]
    return rows[:limit] if limit else rows


def evaluate_one(agent, item: dict[str, Any], gold_cache: dict[str, Any]) -> dict[str, Any]:
    gold = gold_cache[item["id"]]
    record: dict[str, Any] = {
        "id": item["id"], "difficulty": item["difficulty"], "category": item["category"],
        "question": item["question"], "gold_sql": item["gold_sql"],
    }
    try:
        state = agent.run(item["question"], generate_answer=False)
    except Exception as exc:  # API errors etc.
        record.update(status="agent_error", correct=False, failed_query=True, error=str(exc),
                      latency_ms=None, attempts=0, sql="")
        return record

    result = state.get("result") or {}
    failed = bool(result.get("error"))
    correct = (not failed) and results_match(result.get("rows", []), gold["rows"])
    record.update(
        sql=state.get("sql", ""),
        status=state.get("status"),
        attempts=state.get("attempts", 0),
        correct=correct,
        failed_query=failed,
        error=result.get("error"),
        latency_ms=state.get("latency_ms"),
        history=state.get("history", []),
        predicted_preview=result.get("rows", [])[:5],
        gold_preview=gold["rows"][:5],
    )
    return record


def summarise(records: list[dict[str, Any]], usage, args) -> dict[str, Any]:
    n = len(records)
    latencies = [r["latency_ms"] for r in records if r.get("latency_ms") is not None]
    by_diff: dict[str, dict[str, Any]] = {}
    for r in records:
        d = by_diff.setdefault(r["difficulty"], {"n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += int(r["correct"])
    for d in by_diff.values():
        d["accuracy"] = round(100 * d["correct"] / d["n"], 1)

    cost = (usage.prompt_tokens * args.price_in + usage.completion_tokens * args.price_out) / 1e6
    q = statistics.quantiles(latencies, n=20) if len(latencies) >= 20 else []
    return {
        "run_name": args.run_name,
        "model": args.model,
        "reviewer": not args.no_reviewer,
        "max_attempts": 1 if args.no_reviewer else args.max_attempts,
        "questions": n,
        "execution_accuracy": round(100 * sum(r["correct"] for r in records) / n, 1),
        "failed_query_rate": round(100 * sum(r["failed_query"] for r in records) / n, 1),
        "by_difficulty": by_diff,
        "avg_attempts": round(statistics.mean(r.get("attempts", 0) for r in records), 2),
        "median_latency_s": round(statistics.median(latencies) / 1000, 2) if latencies else None,
        "p95_latency_s": round(q[18] / 1000, 2) if q else None,
        "llm_calls": usage.calls,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "estimated_cost_usd": round(cost, 3),
    }


def to_markdown(s: dict[str, Any]) -> str:
    lines = [
        f"## Benchmark run: `{s['run_name']}`",
        "",
        f"Model `{s['model']}`, reviewer {'on' if s['reviewer'] else 'off'}, "
        f"max attempts {s['max_attempts']}, {s['questions']} questions.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Execution accuracy | {s['execution_accuracy']}% |",
        f"| Failed query rate | {s['failed_query_rate']}% |",
        f"| Median latency | {s['median_latency_s']} s |",
        f"| p95 latency | {s['p95_latency_s']} s |",
        f"| Avg attempts | {s['avg_attempts']} |",
        f"| Est. LLM cost | ${s['estimated_cost_usd']} |",
        "",
        "| Difficulty | Questions | Accuracy |",
        "|---|---|---|",
    ]
    for diff in ("easy", "medium", "hard"):
        if diff in s["by_difficulty"]:
            d = s["by_difficulty"][diff]
            lines.append(f"| {diff} | {d['n']} | {d['accuracy']}% |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--benchmark", type=Path, default=ROOT / "eval" / "benchmark.jsonl")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    parser.add_argument("--model")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--no-reviewer", action="store_true", help="Single-shot baseline")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--run-name")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--price-in", type=float, default=DEFAULT_PRICE_IN)
    parser.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUT)
    args = parser.parse_args()

    overrides: dict[str, Any] = {
        "enable_reviewer": not args.no_reviewer,
        "max_attempts": 1 if args.no_reviewer else args.max_attempts,
        "pool_max_size": max(args.concurrency + 1, 2),
    }
    if args.model:
        overrides["openai_model"] = args.model
    settings = Settings(**overrides)
    args.model = settings.openai_model
    args.run_name = args.run_name or (
        f"{settings.openai_model}_{'baseline' if args.no_reviewer else 'reviewer'}_{time.strftime('%Y%m%d-%H%M')}"
    )

    items = load_benchmark(args.benchmark, args.limit, args.difficulty)
    agent = build_agent(settings)

    print(f"Running gold queries for {len(items)} questions...")
    gold_cache = {}
    for item in items:
        gold = agent.db.execute(item["gold_sql"], max_rows=10_000)
        if gold.error:
            raise SystemExit(f"Gold SQL for {item['id']} failed: {gold.error}")
        gold_cache[item["id"]] = gold.to_dict()

    print(f"Running agent ({args.run_name})...")
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(evaluate_one, agent, item, gold_cache): item for item in items}
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            records.append(rec)
            mark = "✓" if rec["correct"] else ("✗ (failed)" if rec["failed_query"] else "✗")
            print(f"[{i:3d}/{len(items)}] {mark} {rec['id']}")

    records.sort(key=lambda r: r["id"])
    summary = summarise(records, agent.llm.usage, args)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{args.run_name}.jsonl").write_text(
        "\n".join(json.dumps(r, default=str) for r in records) + "\n"
    )
    (args.out_dir / f"{args.run_name}_summary.json").write_text(json.dumps(summary, indent=2))
    md = to_markdown(summary)
    (args.out_dir / f"{args.run_name}_summary.md").write_text(md)
    print("\n" + md)
    agent.db.close()


if __name__ == "__main__":
    main()
