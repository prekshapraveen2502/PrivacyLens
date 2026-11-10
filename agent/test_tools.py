"""
Try each agent tool by hand before the agent uses it.

Usage (Neo4j and GraphDB must be running):
    python agent/test_tools.py
"""
import json

from tools import (broader_terms, concepts_under, find_concept, find_flows, laws_and_rights,
                   list_services, run_cypher)


def show(title, result, limit=3):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    rows = result if isinstance(result, list) else [result]
    print(f"{len(rows)} result(s); first {min(limit, len(rows))}:")
    for r in rows[:limit]:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))


show('find_concept("location")', find_concept("location"))
show('find_concept("GPS")  (matches an alternative label)', find_concept("GPS"))
show('concepts_under("PL-002")  (everything under Location data)', concepts_under("PL-002"), limit=5)
show('broader_terms("PL-003")  (what Precise location falls under)', broader_terms("PL-003"), limit=5)
show("laws_and_rights()", laws_and_rights(), limit=5)
show("list_services()", list_services(), limit=2)
show('find_flows(service_id="zoom", purpose_ids=["PL-012"])  (Zoom and AI training)',
     find_flows(service_id="zoom", purpose_ids=["PL-012"]), limit=2)
show('run_cypher(count flows per service)',
     run_cypher("MATCH (s:Service)-[:PUBLISHES]->(:Policy)-[:HAS_FLOW]->(f:Flow) "
                "RETURN s.name AS service, count(f) AS flows ORDER BY flows DESC"), limit=8)

print("\n" + "=" * 78)
print("run_cypher with a write query (should be refused)")
print("=" * 78)
try:
    run_cypher("MATCH (n) DETACH DELETE n")
    print("NOT refused: something is wrong!")
except PermissionError as e:
    print(f"Refused, as expected: {e}")
