"""
PrivacyLens: re-apply the certainty rules to records that are already extracted.

No LLM calls, so it costs nothing and gives the same result every time. Use it after
changing certainty_rules.py instead of re-running extraction.

Usage:
    python pipeline/postprocess_flows.py
Then reload Neo4j:
    python pipeline/load_neo4j.py --reset
"""
import json
from collections import Counter
from pathlib import Path

from certainty_rules import apply

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "data" / "extracted"


def main():
    totals = Counter()
    for path in sorted(EXTRACTED.glob("*.jsonl")):
        if path.name.endswith(".rejected.jsonl"):
            continue
        records = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
        for r in records:
            apply(r)
            totals["records"] += 1
            if r["certainty"] != r["llm_certainty"]:
                totals["corrected"] += 1
            if any(not f.startswith("corrected") for f in r["review_flags"]):
                totals["still flagged"] += 1
        with path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Records checked: {totals['records']}")
    print(f"Certainty corrected (does -> may): {totals['corrected']}")
    print(f"Still flagged for review: {totals['still flagged']}")


if __name__ == "__main__":
    main()
