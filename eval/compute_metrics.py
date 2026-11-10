"""
PrivacyLens metrics for the midterm (compare against PoliGraph).

Works from a CSV export of flows, so it does not depend on the Neo4j schema.
Expected columns in flows.csv (made by eval/midterm_evidence.py; extra columns ignored):
  flow_id, service, action, data_type, purpose, recipient, certainty, quote

Usage:
  python eval/compute_metrics.py sample --flows flows.csv --n 60
  # hand-label eval/precision_sample.csv: label = correct | partial | wrong
  python eval/compute_metrics.py score --flows flows.csv \
      --labeled eval/precision_sample.csv --raw raw_extractions.jsonl

raw_extractions.jsonl (optional): one JSON object per raw LLM extraction,
before the quote check, with a boolean field "quote_ok".
"""
import argparse
import json
import math
import os

import pandas as pd

SEED = 42
LAWS = ["gdpr", "ccpa", "dpdp"]


def wilson_ci(k, n, z=1.96):
    """95% Wilson interval: honest range for a small sample."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (center - half, center + half)


def is_filled(series):
    s = series.fillna("").astype(str).str.strip().str.lower()
    return ~s.isin(["", "none", "null", "nan", "[]"])


def sample(args):
    flows = pd.read_csv(args.flows)
    # Spread the sample across services so one big policy doesn't dominate.
    per_service = max(1, args.n // flows["service"].nunique())
    parts = [
        g.sample(min(len(g), per_service), random_state=SEED)
        for _, g in flows.groupby("service")
    ]
    out = pd.concat(parts)
    if len(out) < args.n:
        rest = flows.drop(out.index)
        out = pd.concat([out, rest.sample(min(len(rest), args.n - len(out)),
                                          random_state=SEED)])
    out = out.sample(frac=1, random_state=SEED)  # shuffle so services are mixed
    cols = [c for c in ["flow_id", "service", "action", "data_type", "purpose",
                        "recipient", "certainty", "quote"] if c in out.columns]
    out = out[cols].copy()
    out["label"] = ""   # correct | partial | wrong
    out["note"] = ""
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"Wrote {len(out)} flows to {args.out} (seed={SEED}). Label each row.")


def score(args):
    flows = pd.read_csv(args.flows)
    rows = []

    # M1: extraction precision from the hand-labeled sample
    if args.labeled:
        lab = pd.read_csv(args.labeled)
        lab["label"] = lab["label"].fillna("").str.strip().str.lower()
        lab = lab[lab["label"].isin(["correct", "partial", "wrong"])]
        n = len(lab)
        c = (lab["label"] == "correct").sum()
        p = (lab["label"] == "partial").sum()
        strict_lo, strict_hi = wilson_ci(c, n)
        rows.append(("M1 precision (strict: correct only)",
                     f"{c}/{n} = {c/n:.1%} (95% CI {strict_lo:.0%}-{strict_hi:.0%})"
                     if n else "no labels yet"))
        if n:
            rows.append(("M1 precision (lenient: correct + partial)",
                         f"{c+p}/{n} = {(c+p)/n:.1%}"))

    # M2: hallucinated extractions caught by the quote check
    if args.raw and os.path.exists(args.raw):
        with open(args.raw) as fh:
            raw = [json.loads(line) for line in fh if line.strip()]
        total = len(raw)
        rejected = sum(1 for r in raw if not r.get("quote_ok", True))
        rows.append(("M2 raw extractions rejected by quote check",
                     f"{rejected}/{total} = {rejected/total:.1%}" if total else "empty file"))

    # M3: certainty breakdown (what PoliGraph's main graph would flatten or drop)
    if "certainty" in flows.columns:
        dist = flows["certainty"].fillna("unknown").str.lower().value_counts()
        for level, count in dist.items():
            rows.append((f"M3 certainty = {level}",
                         f"{count}/{len(flows)} = {count/len(flows):.1%}"))

    # M4: law coverage and vocabulary coverage
    for law in LAWS:
        if law in flows.columns:
            k = is_filled(flows[law]).sum()
            rows.append((f"M4 flows mapped to {law.upper()}",
                         f"{k}/{len(flows)} = {k/len(flows):.1%}"))
    if "vocab_term" in flows.columns:
        k = is_filled(flows["vocab_term"]).sum()
        rows.append(("Flows mapped to the 38-term vocabulary",
                     f"{k}/{len(flows)} = {k/len(flows):.1%}"))

    # Corpus shape
    rows.append(("Total flows", str(len(flows))))
    if "service" in flows.columns:
        per = flows["service"].value_counts()
        rows.append(("Flows per service (min / median / max)",
                     f"{per.min()} / {int(per.median())} / {per.max()}"))

    print("| Metric | Value |\n|---|---|")
    for name, value in rows:
        print(f"| {name} | {value} |")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample")
    s.add_argument("--flows", required=True)
    s.add_argument("--n", type=int, default=60)
    s.add_argument("--out", default="eval/precision_sample.csv")
    s.set_defaults(func=sample)

    sc = sub.add_parser("score")
    sc.add_argument("--flows", required=True)
    sc.add_argument("--labeled")
    sc.add_argument("--raw")
    sc.set_defaults(func=score)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
