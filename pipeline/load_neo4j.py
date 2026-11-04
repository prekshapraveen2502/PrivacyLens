"""
PrivacyLens Step 5: load the extracted records into Neo4j.

What it builds (the property graph = "what policies say"):

  (:Service)-[:PUBLISHES]->(:Policy)-[:HAS_FLOW]->(:Flow)
  (:Flow)-[:HAS_ACTION]->(:Concept)        collect / share / sell / use
  (:Flow)-[:OF_DATA]->(:Concept)           which data type
  (:Flow)-[:FOR_PURPOSE]->(:Concept)       why (if stated)
  (:Flow)-[:TO_RECIPIENT]->(:Concept)      which kind of recipient (if stated)
  (:Flow)-[:TO_ORGANISATION]->(:Organisation)   a named company (if stated)
  (:Flow)-[:SUPPORTED_BY]->(:Statement)    the exact source sentence
  (:Flow)-[:DESCRIBED_AS]->(:LocalTerm)-[:MAPS_TO]->(:Concept)

LocalTerm is the policy's own wording ("precise geolocation data"); Concept is the shared
vocabulary term (PL-003, Precise location). That pair is PoliGraph's local vs global idea.
Every Concept node stores the same IRI as the matching concept in GraphDB, so the two
stores can be joined.

Duplicate records (same policy, action, data type, purpose, recipient, certainty and
sentence) are merged into one Flow; their different wordings all become LocalTerms.
Each Flow keeps the certainty-check flags from extraction in f.reviewFlags (empty if none).

Usage:
    python pipeline/load_neo4j.py           # load (safe to re-run; merges, never duplicates)
    python pipeline/load_neo4j.py --reset   # delete everything in Neo4j first, then load
"""
import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "manifest.csv"
VOCAB = ROOT / "ontology" / "controlled_vocabulary.csv"
EXTRACTED = ROOT / "data" / "extracted"
CONCEPT_BASE = "https://privacylens.example.org/concept/"   # must match build_ontology.py
BATCH = 500


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def doc_name(row):
    return f"{row['service_id']}_{row['document_type']}_{row['region'].lower()}.txt"


def flow_id(r):
    key = "|".join(str(r.get(k) or "") for k in
                   ("document", "action", "data_type", "purpose", "recipient_category",
                    "certainty", "source_sentence"))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def load_flows():
    """Read accepted records and merge duplicates into flows."""
    flows = {}
    for path in sorted(EXTRACTED.glob("*.jsonl")):
        if path.name.endswith(".rejected.jsonl"):
            continue
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            fid = flow_id(r)
            f = flows.setdefault(fid, {
                "id": fid, "document": r["document"], "action": r["action"],
                "data_type": r["data_type"], "purpose": r["purpose"],
                "recipient_category": r["recipient_category"],
                "recipient_name": r["recipient_name"], "certainty": r["certainty"],
                "llm_certainty": r.get("llm_certainty", r["certainty"]),
                "opt_out": r["opt_out"], "excluded_regions": r["excluded_regions"],
                "sentence": r["source_sentence"], "phrases": set(), "review_flags": set()})
            if r.get("data_phrase"):
                f["phrases"].add(r["data_phrase"].strip())
            f["review_flags"].update(r.get("review_flags") or [])
    for f in flows.values():
        f["phrases"] = sorted(f["phrases"])
        f["review_flags"] = sorted(f["review_flags"])
    return list(flows.values())


SCHEMA = [
    "CREATE CONSTRAINT service_id IF NOT EXISTS FOR (n:Service) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT policy_id IF NOT EXISTS FOR (n:Policy) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT flow_id IF NOT EXISTS FOR (n:Flow) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT concept_id IF NOT EXISTS FOR (n:Concept) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT org_name IF NOT EXISTS FOR (n:Organisation) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT localterm_key IF NOT EXISTS FOR (n:LocalTerm) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT statement_key IF NOT EXISTS FOR (n:Statement) REQUIRE n.key IS UNIQUE",
]

LOAD_CONCEPTS = """
UNWIND $rows AS row
MERGE (c:Concept {id: row.id})
SET c.label = row.label, c.facet = row.facet, c.context = row.context, c.iri = row.iri
"""

LOAD_POLICIES = """
UNWIND $rows AS row
MERGE (s:Service {id: row.service_id}) SET s.name = row.service
MERGE (p:Policy {id: row.document})
SET p.documentType = row.document_type, p.region = row.region,
    p.url = row.url, p.collectedOn = row.collected_on
MERGE (s)-[:PUBLISHES]->(p)
"""

LOAD_FLOWS = """
UNWIND $rows AS row
MATCH (p:Policy {id: row.document})
MERGE (f:Flow {id: row.id})
SET f.certainty = row.certainty, f.optOut = row.opt_out,
    f.excludedRegions = row.excluded_regions, f.reviewFlags = row.review_flags,
    f.llmCertainty = row.llm_certainty
MERGE (p)-[:HAS_FLOW]->(f)
WITH f, row, p
MATCH (a:Concept {id: row.action})    MERGE (f)-[:HAS_ACTION]->(a)
WITH f, row, p
MATCH (d:Concept {id: row.data_type}) MERGE (f)-[:OF_DATA]->(d)
WITH f, row, p, d
MERGE (st:Statement {key: row.document + '|' + row.sentence})
SET st.text = row.sentence
MERGE (f)-[:SUPPORTED_BY]->(st)
WITH f, row, p, d
FOREACH (ph IN row.phrases |
  MERGE (t:LocalTerm {key: row.document + '|' + toLower(ph)})
  SET t.phrase = ph, t.policy = row.document
  MERGE (f)-[:DESCRIBED_AS]->(t)
  MERGE (t)-[:MAPS_TO]->(d))
WITH f, row
OPTIONAL MATCH (pu:Concept {id: row.purpose})
FOREACH (_ IN CASE WHEN pu IS NULL THEN [] ELSE [1] END | MERGE (f)-[:FOR_PURPOSE]->(pu))
WITH f, row
OPTIONAL MATCH (rc:Concept {id: row.recipient_category})
FOREACH (_ IN CASE WHEN rc IS NULL THEN [] ELSE [1] END | MERGE (f)-[:TO_RECIPIENT]->(rc))
WITH f, row
FOREACH (_ IN CASE WHEN row.recipient_name IS NULL OR row.recipient_name = '' THEN [] ELSE [1] END |
  MERGE (o:Organisation {name: row.recipient_name})
  MERGE (f)-[:TO_ORGANISATION]->(o))
"""

COUNTS = """
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete all Neo4j data first")
    args = parser.parse_args()

    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv(ROOT / ".env")
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])

    concepts = [{"id": r["id"], "label": r["preferred_label"], "facet": r["facet"],
                 "context": r["context"], "iri": CONCEPT_BASE + r["id"]}
                for r in read_csv(VOCAB)]
    policies = [{**r, "document": doc_name(r)} for r in read_csv(MANIFEST) if r["status"] == "ok"]
    flows = load_flows()

    with GraphDatabase.driver(uri, auth=auth) as driver:
        driver.verify_connectivity()
        if args.reset:
            driver.execute_query("MATCH (n) DETACH DELETE n")
            print("Deleted existing data.")
        for stmt in SCHEMA:
            driver.execute_query(stmt)
        driver.execute_query(LOAD_CONCEPTS, rows=concepts)
        driver.execute_query(LOAD_POLICIES, rows=policies)
        for i in range(0, len(flows), BATCH):
            driver.execute_query(LOAD_FLOWS, rows=flows[i:i + BATCH])
            print(f"  loaded flows {i + 1}-{min(i + BATCH, len(flows))} of {len(flows)}")

        records, _, _ = driver.execute_query(COUNTS)
        print("\nNodes in Neo4j:")
        for r in records:
            print(f"  {r['label']:13s} {r['n']}")


if __name__ == "__main__":
    main()
