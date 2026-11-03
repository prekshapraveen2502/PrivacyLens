"""
PrivacyLens: run competency questions against the knowledge graph.

Each competency question (CQ) is a question a user or agent would ask. Here each one is
answered by real queries:
  * Neo4j (Cypher) holds what policies SAY: services, policies, flows, sentences.
  * GraphDB (SPARQL) holds what terms MEAN: the vocabulary hierarchy and laws.
  * Hybrid CQs ask GraphDB first (e.g. "which data types count as location data?"),
    then pass those concept IDs to Neo4j ("which flows involve any of them?").

Usage:
    python eval/run_cqs.py                     # run all CQs
    python eval/run_cqs.py --cq CQ3            # run one
    python eval/run_cqs.py --cq CQ4 --service meta
    python eval/run_cqs.py --explain           # also print the queries used
"""
import argparse
import os
import textwrap
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PREFIXES = """PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX c: <https://privacylens.example.org/concept/>
"""


# ---------------------------------------------------------------- store helpers

def sparql(query):
    base = os.getenv("GRAPHDB_URL", "http://localhost:7200")
    repo = os.getenv("GRAPHDB_REPO", "privacylens")
    r = requests.post(f"{base}/repositories/{repo}", data={"query": PREFIXES + query},
                      headers={"Accept": "application/sparql-results+json"}, timeout=30)
    r.raise_for_status()
    return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]


def concepts_under(concept_id, include_self=True):
    """IDs of a concept and everything below it in the hierarchy (asked of GraphDB)."""
    path = "skos:broader*" if include_self else "skos:broader+"
    rows = sparql(f"SELECT ?id ?label WHERE {{ ?x {path} c:{concept_id} ; "
                  f"skos:notation ?id ; skos:prefLabel ?label . }}")
    return rows


def show(rows, columns):
    if not rows:
        print("    (no results)")
        return
    widths = {c: min(max(len(c), *(len(str(r[c])) for r in rows)), 60) for c in columns}
    print("    " + "  ".join(c.ljust(widths[c]) for c in columns))
    print("    " + "  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("    " + "  ".join(str(r[c])[:60].ljust(widths[c]) for c in columns))


def explain(label, query, enabled):
    if enabled:
        print(f"\n  {label}:")
        print(textwrap.indent(textwrap.dedent(query).strip(), "      "))


# ---------------------------------------------------------------- competency questions

def cq1(db, args):
    """Which services say they use data to train AI models, and can users opt out?"""
    q = """
    MATCH (s:Service)-[:PUBLISHES]->(p:Policy)-[:HAS_FLOW]->(f:Flow)
          -[:FOR_PURPOSE]->(:Concept {id: 'PL-012'})
    MATCH (f)-[:OF_DATA]->(d:Concept)
    RETURN s.name AS service, p.region AS region, f.certainty AS certainty,
           f.optOut AS opt_out, count(DISTINCT f) AS flows,
           collect(DISTINCT d.label)[0..3] AS data_types
    ORDER BY service, region, certainty
    """
    explain("Cypher (Neo4j)", q, args.explain)
    rows, _, _ = db.execute_query(q)
    show([r.data() for r in rows], ["service", "region", "certainty", "opt_out", "flows", "data_types"])


def cq3(db, args):
    """Which services share any kind of location data for advertising or with ad partners?"""
    location = concepts_under("PL-002")
    explain("SPARQL (GraphDB): every concept under Location data",
            "SELECT ?id ?label WHERE { ?x skos:broader* c:PL-002 ; skos:notation ?id ; skos:prefLabel ?label . }",
            args.explain)
    print(f"  GraphDB: location data includes {', '.join(r['label'] for r in location)}")
    q = """
    MATCH (s:Service)-[:PUBLISHES]->(p:Policy)-[:HAS_FLOW]->(f:Flow)-[:HAS_ACTION]->(a:Concept)
    WHERE a.id IN ['PL-015', 'PL-016'] AND f.certainty <> 'does_not'
    MATCH (f)-[:OF_DATA]->(d:Concept) WHERE d.id IN $data
    OPTIONAL MATCH (f)-[:FOR_PURPOSE]->(pu:Concept)
    OPTIONAL MATCH (f)-[:TO_RECIPIENT]->(rc:Concept)
    WITH s, p, d, a, f, pu, rc
    WHERE pu.id = 'PL-010' OR rc.id = 'PL-018'
    RETURN s.name AS service, p.region AS region, a.label AS action,
           d.label AS data_type, f.optOut AS opt_out, count(DISTINCT f) AS flows
    ORDER BY service, region
    """
    explain("Cypher (Neo4j), with those IDs passed in as $data", q, args.explain)
    rows, _, _ = db.execute_query(q, data=[r["id"] for r in location])
    show([r.data() for r in rows], ["service", "region", "action", "data_type", "opt_out", "flows"])


def cq4(db, args):
    """Which personal data does a service collect, including data its policy doesn't call personal?"""
    personal = concepts_under("PL-001", include_self=False)
    explain("SPARQL (GraphDB): every concept below Personal data",
            "SELECT ?id ?label WHERE { ?x skos:broader+ c:PL-001 ; skos:notation ?id ; skos:prefLabel ?label . }",
            args.explain)
    print(f"  GraphDB: {len(personal)} data types count as personal data")
    q = """
    MATCH (s:Service {id: $service})-[:PUBLISHES]->(:Policy)-[:HAS_FLOW]->(f:Flow)
          -[:HAS_ACTION]->(:Concept {id: 'PL-014'})
    MATCH (f)-[:OF_DATA]->(d:Concept) WHERE d.id IN $personal
    MATCH (f)-[:DESCRIBED_AS]->(t:LocalTerm)
    RETURN d.label AS shared_concept, collect(DISTINCT t.phrase)[0..4] AS policy_wording
    ORDER BY shared_concept
    """
    explain("Cypher (Neo4j), with those IDs passed in as $personal", q, args.explain)
    print(f"  Service: {args.service}")
    rows, _, _ = db.execute_query(q, service=args.service, personal=[r["id"] for r in personal])
    show([r.data() for r in rows], ["shared_concept", "policy_wording"])


def cq12(db, args):
    """Which named companies receive data from the most services?"""
    q = """
    MATCH (s:Service)-[:PUBLISHES]->(:Policy)-[:HAS_FLOW]->(:Flow)-[:TO_ORGANISATION]->(o:Organisation)
    RETURN o.name AS organisation, count(DISTINCT s) AS services,
           collect(DISTINCT s.name) AS from_services
    ORDER BY services DESC, organisation LIMIT 10
    """
    explain("Cypher (Neo4j)", q, args.explain)
    rows, _, _ = db.execute_query(q)
    show([r.data() for r in rows], ["organisation", "services", "from_services"])


CQS = {
    "CQ1": ("Which services say they use data to train AI models, and can users opt out?", "Neo4j", cq1),
    "CQ3": ("Which services share any kind of location data for advertising?", "GraphDB + Neo4j", cq3),
    "CQ4": ("Which personal data does a service collect, including data it doesn't call personal?",
            "GraphDB + Neo4j", cq4),
    "CQ12": ("Which named companies receive data from the most services?", "Neo4j", cq12),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cq", help="run one CQ, e.g. CQ3")
    parser.add_argument("--service", default="uber", help="service id for CQ4 (e.g. uber, meta, zoom)")
    parser.add_argument("--explain", action="store_true", help="print the queries used")
    args = parser.parse_args()

    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv(ROOT / ".env")
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])

    selected = [args.cq.upper()] if args.cq else list(CQS)
    with GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"), auth=auth) as db:
        for cq_id in selected:
            if cq_id not in CQS:
                print(f"Unknown CQ: {cq_id}. Available: {', '.join(CQS)}")
                continue
            question, stores, fn = CQS[cq_id]
            print("\n" + "=" * 78)
            print(f"{cq_id}: {question}")
            print(f"Answered from: {stores}")
            print("=" * 78)
            fn(db, args)


if __name__ == "__main__":
    main()
