"""
PrivacyLens setup check: confirms Python can reach Neo4j, GraphDB, and the LLM API.

Reads settings from the .env file in the project root (copy .env.example to .env):
  NEO4J_PASSWORD     password for your Neo4j database
  ANTHROPIC_API_KEY  your API key
Optional:
  NEO4J_URI          default bolt://localhost:7687
  NEO4J_USER         default neo4j
  GRAPHDB_URL        default http://localhost:7200
  GRAPHDB_REPO       default privacylens
  LLM_MODEL          default claude-sonnet-5
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()


def check_neo4j():
    from neo4j import GraphDatabase
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    with GraphDatabase.driver(uri, auth=(user, os.environ["NEO4J_PASSWORD"])) as driver:
        driver.verify_connectivity()
        with driver.session() as s:
            print("  Neo4j:", s.run("RETURN 'connected' AS ok").single()["ok"])
            try:
                version = s.run("RETURN gds.version() AS v").single()["v"]
                print("  GDS plugin:", version)
            except Exception:
                print("  GDS plugin: NOT FOUND (install it from the Neo4j Desktop plugins tab)")


def check_graphdb():
    base = os.getenv("GRAPHDB_URL", "http://localhost:7200")
    repo = os.getenv("GRAPHDB_REPO", "privacylens")
    r = requests.post(
        f"{base}/repositories/{repo}",
        data={"query": "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=10,
    )
    r.raise_for_status()
    n = r.json()["results"]["bindings"][0]["n"]["value"]
    print(f"  GraphDB repo '{repo}': reachable, {n} triples")


def check_llm():
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.getenv("LLM_MODEL", "claude-sonnet-5"),
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
    )
    print("  LLM API:", msg.content[0].text.strip())


if __name__ == "__main__":
    for name, fn in [("Neo4j", check_neo4j), ("GraphDB", check_graphdb), ("LLM API", check_llm)]:
        print(f"Checking {name}...")
        try:
            fn()
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
