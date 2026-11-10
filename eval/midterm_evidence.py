"""
PrivacyLens: gather everything needed for the midterm in one run (read-only).

  1. Evidence for the held-out questions (what the graph says, with source sentences)
  2. eval/flows.csv, the input for eval/compute_metrics.py
  3. Two metrics straight from the graph:
       - flows that name only generic "Personal data" (compare: PoliGraph Finding 3)
       - flows found only through the location hierarchy (compare: PoliGraph's ablation)
  4. The Concept labels, to pick a law + data-type question

Usage (Neo4j running):
    python eval/midterm_evidence.py > eval/midterm_evidence.txt
Uses the agent's read-only Cypher helper; nothing is written to the database.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from tools import _cypher_read  # read-only transaction

PROJECTION = """
RETURN elementId(f) AS flow_id, s.name AS service,
       [(f)-[:HAS_ACTION]->(c:Concept) | c.label] AS action,
       [(f)-[:OF_DATA]->(c:Concept) | c.label] AS data_type,
       [(f)-[:FOR_PURPOSE]->(c:Concept) | c.label] AS purpose,
       [(f)-[:TO_RECIPIENT]->(c:Concept) | c.label] AS recipient,
       f.certainty AS certainty,
       [(f)-[:SUPPORTED_BY]->(st:Statement) | st.text][0] AS quote
"""
BASE = "MATCH (s:Service)-[:PUBLISHES]->(:Policy)-[:HAS_FLOW]->(f:Flow)\n"
SHARE_OR_SELL = ("EXISTS { (f)-[:HAS_ACTION]->(a:Concept) WHERE toLower(a.label) CONTAINS 'shar' "
                 "OR toLower(a.label) CONTAINS 'sell' OR toLower(a.label) CONTAINS 'disclos' "
                 "OR toLower(a.label) CONTAINS 'transfer' }")
LOCATION = ["Location data", "Precise location", "Approximate location"]


def j(xs):
    return "; ".join(x for x in (xs or []) if x) or "-"


def show(title, where, limit=15):
    rows = _cypher_read(BASE + "WHERE " + where + PROJECTION + "LIMIT 100")
    print(f"\n=== {title}  ({len(rows)} flows) ===")
    seen = set()
    shown = 0
    for r in rows:
        key = (r["quote"], j(r["action"]), j(r["purpose"]))
        if key in seen:
            continue
        seen.add(key)
        print(f"- {r['service']} | action: {j(r['action'])} | data: {j(r['data_type'])} | "
              f"purpose: {j(r['purpose'])} | recipient: {j(r['recipient'])} | {r['certainty']}")
        print(f"    \"{(r['quote'] or '')[:240]}\"")
        shown += 1
        if shown >= limit:
            break


def evidence():
    print("#" * 78 + "\n# 1. EVIDENCE FOR HELD-OUT QUESTIONS\n" + "#" * 78)
    show("LinkedIn + AI model training",
         "s.name = 'LinkedIn' AND EXISTS { (f)-[:FOR_PURPOSE]->(:Concept {label: 'AI model training'}) }")
    show("Device identifiers shared or sold (all services)",
         "EXISTS { (f)-[:OF_DATA]->(:Concept {label: 'Device identifier'}) } AND " + SHARE_OR_SELL)
    show("Zoom + contact information",
         "s.name = 'Zoom' AND EXISTS { (f)-[:OF_DATA]->(:Concept {label: 'Contact information'}) }")
    show("Zoom + sell (any data)",
         "s.name = 'Zoom' AND EXISTS { (f)-[:HAS_ACTION]->(a:Concept) "
         "WHERE toLower(a.label) CONTAINS 'sell' }")
    show("TikTok + 'may' statements", "s.name = 'TikTok' AND f.certainty = 'may'")
    show("Sensitive personal data, shared or sold (all services)",
         "EXISTS { (f)-[:OF_DATA]->(:Concept {label: 'Sensitive personal data'}) } AND " + SHARE_OR_SELL)

    print("\n=== Any kind of location data, by service (counts) ===")
    rows = _cypher_read(BASE + """
        MATCH (f)-[:OF_DATA]->(c:Concept) WHERE c.label IN $labels
        RETURN s.name AS service, c.label AS data_type, count(DISTINCT f) AS flows
        ORDER BY service, data_type""", labels=LOCATION)
    for r in rows:
        print(f"- {r['service']}: {r['data_type']} = {r['flows']}")


def export_flows(path):
    rows = _cypher_read(BASE + PROJECTION)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: j(v) if isinstance(v, list) else v for k, v in r.items()})
    print(f"\n# 2. Exported {len(rows)} flows to {path}")


def graph_metrics():
    print("\n" + "#" * 78 + "\n# 3. METRICS FROM THE GRAPH\n" + "#" * 78)
    r = _cypher_read("""
        MATCH (f:Flow)
        WITH f, [(f)-[:OF_DATA]->(c:Concept) | c.label] AS types
        RETURN count(f) AS flows,
               sum(CASE WHEN types = ['Personal data'] THEN 1 ELSE 0 END) AS generic_only""")[0]
    print(f"Generic only ('Personal data' and nothing more specific): "
          f"{r['generic_only']}/{r['flows']} = {r['generic_only'] / r['flows']:.1%}")

    r = _cypher_read("""
        MATCH (f:Flow)-[:OF_DATA]->(c:Concept) WHERE c.label IN $labels
        WITH f, collect(c.label) AS labels
        RETURN count(f) AS any_location,
               sum(CASE WHEN 'Location data' IN labels THEN 1 ELSE 0 END) AS literal""",
                     labels=LOCATION)[0]
    only = r["any_location"] - r["literal"]
    print(f"Location flows: {r['any_location']} in total; {r['literal']} labeled 'Location data' itself; "
          f"{only} ({only / r['any_location']:.0%}) found only through the hierarchy "
          f"(precise/approximate location)")

    r = _cypher_read("MATCH (f:Flow) RETURN f.certainty AS certainty, count(f) AS n ORDER BY n DESC")
    total = sum(x["n"] for x in r)
    print("Certainty: " + ", ".join(f"{x['certainty']} {x['n']} ({x['n'] / total:.1%})" for x in r))


def concepts():
    print("\n" + "#" * 78 + "\n# 4. CONCEPT LABELS\n" + "#" * 78)
    rows = _cypher_read("MATCH (c:Concept) RETURN c.label AS label, keys(c) AS keys ORDER BY label")
    if rows:
        print("Concept properties:", rows[0]["keys"])
    print(", ".join(r["label"] for r in rows if r["label"]))


if __name__ == "__main__":
    evidence()
    export_flows(ROOT / "eval" / "flows.csv")
    graph_metrics()
    concepts()
