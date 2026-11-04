"""
Search the original policy texts sentence by sentence.

Prints every sentence that matches ALL the given patterns (regular expressions,
case-insensitive), with the file it came from. Useful for checking what the graph
should contain, independent of the extraction.

Usage:
    python scripts/search_policies.py location "advertis|\\bads?\\b"
    python scripts/search_policies.py "train" "model|algorithm" --max 400
"""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICIES = ROOT / "data" / "policies"
HEADER_DIVIDER = "=" * 60


def sentences(text):
    if HEADER_DIVIDER in text:
        text = text.split(HEADER_DIVIDER, 1)[1]
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("patterns", nargs="+", help="regular expressions that must all match")
    parser.add_argument("--max", type=int, default=300, help="characters to show per sentence")
    args = parser.parse_args()
    patterns = [re.compile(p, re.I) for p in args.patterns]

    hits = 0
    for path in sorted(POLICIES.glob("*.txt")):
        for s in sentences(path.read_text(encoding="utf-8")):
            if all(p.search(s) for p in patterns):
                hits += 1
                print(f"{path.name} | {s[:args.max]}\n")
    print(f"{hits} matching sentences")


if __name__ == "__main__":
    main()
