"""Ask a question from the terminal.

    python -m sqlagent.cli "Which 5 pickup zones had the most trips in March 2023?"
"""

from __future__ import annotations

import argparse
import json

from .config import get_settings
from .factory import build_agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the text-to-SQL agent a question.")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="Print the full agent state as JSON")
    args = parser.parse_args()

    agent = build_agent(get_settings())
    try:
        state = agent.run(args.question)
    finally:
        agent.db.close()

    if args.json:
        print(json.dumps(state, indent=2, default=str))
        return

    result = state.get("result") or {}
    print(f"\n{state.get('answer', '')}\n")
    print(f"SQL ({state.get('status')}, {state.get('attempts')} attempt(s), {state.get('latency_ms')} ms):")
    print(state.get("sql", ""))
    if result.get("rows"):
        print("\n" + " | ".join(result["columns"]))
        for row in result["rows"][:10]:
            print(" | ".join(str(v) for v in row))
        if result["row_count"] > 10:
            print(f"... {result['row_count'] - 10} more rows")


if __name__ == "__main__":
    main()
