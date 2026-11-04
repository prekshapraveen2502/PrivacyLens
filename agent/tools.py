"""
PrivacyLens agent tools.

Each tool is a plain Python function the agent (an LLM) can ask to run. The LLM never
touches the databases directly: these functions decide what it can see and do.

  GraphDB (what terms mean)
    find_concept(text)            vocabulary concepts whose label or wording matches text
    concepts_under(concept_id)    a concept and everything below it in the hierarchy
    broader_terms(concept_id)     everything a concept falls under (incl. law-specific terms)
    laws_and_rights()             laws, where they apply, and the rights they grant

  Neo4j (what policies say)
    list_services()               the services and documents in the corpus
    find_flows(...)               data practices filtered by service, action, data, purpose,
                                  recipient and certainty, each with its source sentence
    run_cypher(query)             any READ-ONLY Cypher query (for questions the others
                                  don't cover); write queries are refused
    graph_schema()                a description of the Neo4j graph, for writing Cypher

Every tool returns plain Python data (lists and dicts), so results can be passed back
to the LLM as JSON.
"""
import os
import re
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PREFIXES = """PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX pl: <https://privacylens.example.org/ontology#>
PREFIX c: <https://privacylens.example.org/concept/>
"""
MAX_ROWS = 50          # cap on rows returned to the LLM
CONCEPT_ID = re.compile(r"^PL-\d{3}$")


# ---------------------------------------------------------------- connections

def _sparql(query):
    base = os.getenv("GRAPHDB_URL", "http://localhost:7200")
    repo = os.getenv("GRAPHDB_REPO", "privacylens")
    r = requests.post(f"{base}/repositories/{repo}", data={"query": PREFIXES + query},
                      headers={"Accept": "application/sparql-results+json"}, timeout=30)
    r.raise_for_status()
    return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]


@lru_cache(maxsize=1)
def _driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                                auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))


def _cypher_read(query, **params):
    """Run a query in a read-only transaction (Neo4j refuses writes inside it)."""
    from neo4j import READ_ACCESS
    with _driver().session(default_access_mode=READ_ACCESS) as session:
        return session.execute_read(lambda tx: [r.data() for r in tx.run(query, **params)])


def _check_id(concept_id):
    if not CONCEPT_ID.match(concept_id or ""):
        raise ValueError(f"Not a concept ID: {concept_id!r} (expected e.g. PL-003)")


# ---------------------------------------------------------------- GraphDB tools

@lru_cache(maxsize=1)
def _all_concepts():
    """Every vocabulary concept with its labels and definition (cached; the vocabulary is small)."""
    rows = _sparql('''
        SELECT ?id ?label ?scheme ?definition (GROUP_CONCAT(DISTINCT ?alt; separator="; ") AS ?also_called)
        WHERE {
          ?c skos:notation ?id ; skos:prefLabel ?label .
          OPTIONAL { ?c skos:inScheme/skos:prefLabel ?scheme }
          OPTIONAL { ?c skos:definition ?definition }
          OPTIONAL { ?c skos:altLabel ?alt }
        }
        GROUP BY ?id ?label ?scheme ?definition ORDER BY ?id''')
    return tuple(rows)


def _whole_words(phrase):
    return re.compile(r"\b" + r"\s+".join(map(re.escape, phrase.lower().split())) + r"\b")


def find_concept(text):
    """Vocabulary concepts matching `text` as whole words.

    First looks for the phrase in preferred and alternative labels; if nothing matches,
    looks for all of its words in labels and definitions. "AI" therefore no longer matches
    inside words such as "details" or "email".
    """
    concepts = _all_concepts()
    phrase = _whole_words(text)
    hits = [c for c in concepts
            if phrase.search(c["label"].lower()) or phrase.search(c.get("also_called", "").lower())]
    if not hits:
        words = [_whole_words(w) for w in text.lower().split() if len(w) > 2]
        hits = [c for c in concepts if words and all(
            w.search(" ".join([c["label"], c.get("also_called", ""), c.get("definition", "")]).lower())
            for w in words)]
    return [{k: c.get(k, "") for k in ("id", "label", "scheme", "also_called")} for c in hits][:MAX_ROWS]


def concepts_under(concept_id):
    """The concept and every concept below it in the hierarchy."""
    _check_id(concept_id)
    return _sparql(f'''
        SELECT ?id ?label WHERE {{
          ?x skos:broader* c:{concept_id} ; skos:notation ?id ; skos:prefLabel ?label .
        }} ORDER BY ?id''')


def broader_terms(concept_id):
    """Everything the concept falls under, including law-specific categories."""
    _check_id(concept_id)
    return _sparql(f'''
        SELECT ?id ?label ?defined_in WHERE {{
          c:{concept_id} skos:broader+ ?b .
          ?b skos:notation ?id ; skos:prefLabel ?label .
          OPTIONAL {{ ?b pl:definedIn/rdfs:label ?defined_in }}
        }} ORDER BY ?id''')


def laws_and_rights():
    """Laws in the ontology, where they apply, and the rights they grant."""
    return _sparql('''
        SELECT ?law ?applies_in ?right ?right_id ?concerns WHERE {
          ?l a pl:Law ; rdfs:label ?law ; pl:appliesIn/rdfs:label ?applies_in .
          OPTIONAL {
            ?l pl:grantsRight ?r . ?r skos:prefLabel ?right ; skos:notation ?right_id .
            OPTIONAL { ?r pl:rightConcerns/skos:prefLabel ?concerns }
          }
        } ORDER BY ?law''')


# ---------------------------------------------------------------- Neo4j tools

def list_services():
    """Services in the corpus and the documents collected for each."""
    return _cypher_read('''
        MATCH (s:Service)-[:PUBLISHES]->(p:Policy)
        RETURN s.id AS service_id, s.name AS service,
               collect({document: p.id, type: p.documentType, region: p.region}) AS documents
        ORDER BY service''')


def find_flows(service_id=None, action_ids=None, data_ids=None, purpose_ids=None,
               recipient_ids=None, certainty=None, limit=25):
    """Data practices matching the filters, each with its exact source sentence.

    All filters are optional. Use concept IDs (e.g. data_ids=["PL-003"]); to include
    narrower concepts, expand them first with concepts_under().
    """
    # Empty lists or strings mean "no filter" (the LLM often sends [] for unused filters).
    action_ids, data_ids, purpose_ids, recipient_ids = (
        ids or None for ids in (action_ids, data_ids, purpose_ids, recipient_ids))
    service_id, certainty = service_id or None, certainty or None
    for ids in (action_ids, data_ids, purpose_ids, recipient_ids):
        for i in ids or []:
            _check_id(i)
    rows = _cypher_read('''
        MATCH (s:Service)-[:PUBLISHES]->(p:Policy)-[:HAS_FLOW]->(f:Flow)
        WHERE ($service_id IS NULL OR s.id = $service_id)
          AND ($certainty IS NULL OR f.certainty = $certainty)
        MATCH (f)-[:HAS_ACTION]->(a:Concept)
        WHERE $action_ids IS NULL OR a.id IN $action_ids
        MATCH (f)-[:OF_DATA]->(d:Concept)
        WHERE $data_ids IS NULL OR d.id IN $data_ids
        OPTIONAL MATCH (f)-[:FOR_PURPOSE]->(pu:Concept)
        OPTIONAL MATCH (f)-[:TO_RECIPIENT]->(rc:Concept)
        OPTIONAL MATCH (f)-[:TO_ORGANISATION]->(o:Organisation)
        WITH s, p, f, a, d, pu, rc, collect(o.name) AS organisations
        WHERE ($purpose_ids IS NULL OR pu.id IN $purpose_ids)
          AND ($recipient_ids IS NULL OR rc.id IN $recipient_ids)
        MATCH (f)-[:SUPPORTED_BY]->(st:Statement)
        OPTIONAL MATCH (f)-[:DESCRIBED_AS]->(t:LocalTerm)
        RETURN s.name AS service, p.id AS document, p.region AS region,
               a.label AS action, d.label AS data_type,
               collect(DISTINCT t.phrase) AS policy_wording,
               pu.label AS purpose, rc.label AS recipient, organisations,
               f.certainty AS certainty, f.optOut AS opt_out,
               f.excludedRegions AS excluded_regions, f.reviewFlags AS review_flags,
               st.text AS sentence
        LIMIT $limit''', service_id=service_id, action_ids=action_ids, data_ids=data_ids,
        purpose_ids=purpose_ids, recipient_ids=recipient_ids, certainty=certainty,
        limit=min(int(limit), MAX_ROWS))
    return rows


WRITE_WORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b|\bCALL\s+(dbms|db\.create|apoc)",
    re.I)


def run_cypher(query):
    """Run a read-only Cypher query. Write operations are refused before and during execution."""
    if WRITE_WORDS.search(query):
        raise PermissionError("Only read-only queries are allowed (no CREATE, MERGE, SET, DELETE, ...).")
    if not re.search(r"\bLIMIT\s+\d+", query, re.I):
        query = query.rstrip().rstrip(";") + f"\nLIMIT {MAX_ROWS}"
    return _cypher_read(query)[:MAX_ROWS]


def graph_schema():
    """A description of the Neo4j graph, for writing Cypher queries."""
    return """Nodes and key properties:
  (:Service {id, name})                 e.g. id 'uber', name 'Uber'
  (:Policy {id, documentType, region, url, collectedOn})   id is the file name
  (:Flow {certainty: 'does'|'may'|'does_not', optOut: 'yes'|'no'|'not_stated',
          excludedRegions, reviewFlags, llmCertainty})
  (:Concept {id, label, facet, context, iri})   vocabulary terms, e.g. PL-003 Precise location
  (:Statement {text})                   exact source sentence
  (:LocalTerm {phrase, policy})         the policy's own wording for a data type
  (:Organisation {name})                a named recipient company
Relationships:
  (Service)-[:PUBLISHES]->(Policy)-[:HAS_FLOW]->(Flow)
  (Flow)-[:HAS_ACTION]->(Concept)       actions: PL-014 Collect, PL-015 Share, PL-016 Sell, PL-025 Use
  (Flow)-[:OF_DATA]->(Concept)          data types
  (Flow)-[:FOR_PURPOSE]->(Concept)      purposes, e.g. PL-010 Advertising, PL-012 AI model training
  (Flow)-[:TO_RECIPIENT]->(Concept)     recipient categories, e.g. PL-018 Advertising partner
  (Flow)-[:TO_ORGANISATION]->(Organisation)
  (Flow)-[:SUPPORTED_BY]->(Statement)
  (Flow)-[:DESCRIBED_AS]->(LocalTerm)-[:MAPS_TO]->(Concept)
Concept hierarchy (broader/narrower) lives in GraphDB: use concepts_under() to expand IDs."""


TOOLS = {f.__name__: f for f in (find_concept, concepts_under, broader_terms, laws_and_rights,
                                 list_services, find_flows, run_cypher, graph_schema)}
