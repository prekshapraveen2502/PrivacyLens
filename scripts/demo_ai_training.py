"""
PrivacyLens demo: what each service's documents say about using data to train AI models.

Reads the extracted records (data/extracted/*.jsonl) and prints every record whose
purpose is AI model training (PL-012), grouped by document, with its certainty,
opt-out status, the policy's own wording for the data, and the source sentence.

This is a preview straight from the extraction output; the same question will be
answered from the knowledge graph once the records are loaded.

Usage:
    python scripts/demo_ai_training.py
    python scripts/demo_ai_training.py --service zoom      # one service only
"""
import argparse
import json
import textwrap
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "data" / "extracted"
LABELS = {"does": "DOES", "may": "MAY", "does_not": "DOES NOT"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", help="show only documents whose name starts with this")
    args = parser.parse_args()

    by_doc = defaultdict(list)
    for path in sorted(EXTRACTED.glob("*.jsonl")):
        if path.name.endswith(".rejected.jsonl"):
            continue
        if args.service and not path.name.startswith(args.service.lower()):
            continue
        for line in path.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                if r.get("purpose") == "PL-012":
                    by_doc[r["document"]].append(r)

    if not by_doc:
        print("No AI model training statements found.")
        return

    total = sum(len(v) for v in by_doc.values())
    print(f"\nAI model training statements: {total} records in {len(by_doc)} documents\n")
    for doc, records in by_doc.items():
        print("=" * 78)
        print(doc.replace(".txt", ""))
        print("=" * 78)
        seen = set()
        for r in records:
            if r["source_sentence"] in seen:          # one entry per sentence
                continue
            seen.add(r["source_sentence"])
            print(f"  [{LABELS.get(r['certainty'], r['certainty'])}]  data: {r['data_phrase']}"
                  f"   |  opt-out: {r['opt_out']}")
            print(textwrap.fill(f'"{r["source_sentence"]}"', width=74,
                                initial_indent="    ", subsequent_indent="    "))
            print()


if __name__ == "__main__":
    main()
