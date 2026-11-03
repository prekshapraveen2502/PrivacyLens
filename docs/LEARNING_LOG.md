# PrivacyLens learning log

What each step does, the concept behind it, and where it lives in the code.
Add a new entry each time a step is finished.

---

## The project in one paragraph

PrivacyLens reads company privacy policies and records what each company says it does
with people's data. It keeps two kinds of knowledge apart:

- **What policies say** (facts): "Uber shares location data with advertising partners."
  Stored in **Neo4j**, a property graph.
- **What words mean** (meaning): "precise location is a kind of location data, which is a
  kind of personal data." Stored in **GraphDB**, an RDF/OWL store.

The two are joined through shared IDs, so a question can use both. This split comes from
the PoliGraph paper: each policy's own wording is the **local** ontology; shared
definitions from standards and laws are the **global** ontology.

---

## Step 1: Collect the corpus

**What:** 13 documents from 8 services, saved as text with their URL, region and date.

**Concept:** provenance. Every fact must be traceable to a source document.

**Files:** `data/manifest.csv`, `pipeline/collect_policies.py`

---

## Step 2: Controlled vocabulary

**What:** a fixed list of terms (one official label per idea, plus the other wordings
policies use), with definitions and sources. General terms plus law-specific terms
(GDPR, CCPA, DPDP) linked to them.

**Concept:** a controlled vocabulary is the first stage of the ontology pipeline
(Talisman, Chapter 1). Companies and laws use different words for the same idea; the
vocabulary fixes one ID per idea so everything else can refer to it.

**Files:** `ontology/controlled_vocabulary.csv`

---

## Step 3: Ontology

**What:** a script turns the vocabulary into an ontology:
1. each term becomes a SKOS concept with a stable ID
2. a taxonomy adds broader/narrower links (precise location → location data → personal data)
3. OWL adds structure: classes (Service, Policy, Flow, Statement, Law, Jurisdiction), the
   properties between them, and defined classes such as `AITrainingFlow`

Checked in Protégé with a reasoner, then loaded into GraphDB.

**Concepts:**
- *Triples*: everything is stored as subject, relationship, object
  (`PL-003 broader PL-002`).
- *SKOS* is for vocabularies and hierarchies; *OWL* is for classes and logical rules.
- *Reasoner*: applies the rules to work out new facts (it put the "we train AI" flow in
  `AITrainingFlow` and kept the "we do not" flow out).
- *SPARQL*: the query language for RDF. `skos:broader+` follows the hierarchy any number
  of levels.

**Files:** `pipeline/build_ontology.py` → `ontology/privacylens.ttl`

---

## Step 4: LLM extraction

**What:** an LLM reads each policy in ~900-word chunks and writes one record per data
practice: action, data type, purpose, recipient, certainty (does / may / does not),
opt-out, and the exact source sentence.

**Concepts:**
- *Grounding the LLM in the vocabulary*: the fields only accept vocabulary IDs, so the
  model cannot invent labels.
- *Local vs global*: each record keeps the policy's own wording (`data_phrase`) next to
  the shared concept ID.
- *Quote check*: a record is rejected if its sentence is not really in the policy.

**Findings:** chunk size matters (records rose from 826 to 1,309 on the same corpus after
fixing chunking); about 28% of records refer to data only generically ("your
information").

**Files:** `pipeline/extract_flows.py` → `data/extracted/`

---

## Step 5: Load Neo4j

**What:** the extracted records become a property graph:

```
(Service)-[:PUBLISHES]->(Policy)-[:HAS_FLOW]->(Flow)
(Flow)-[:HAS_ACTION]->(Concept)   (Flow)-[:OF_DATA]->(Concept)
(Flow)-[:FOR_PURPOSE]->(Concept)  (Flow)-[:TO_RECIPIENT]->(Concept)
(Flow)-[:SUPPORTED_BY]->(Statement)
(Flow)-[:DESCRIBED_AS]->(LocalTerm)-[:MAPS_TO]->(Concept)
```

**Concepts:**
- *Why a Flow node:* one practice involves several things at once (who, what data, why,
  to whom, which sentence). A single relationship can't hold all of that, so the practice
  itself becomes a node.
- *LocalTerm → Concept* is PoliGraph's local vs global idea, as graph structure.
- *Concept nodes carry the same IRI as in GraphDB*: that is the link between the stores.
- *Cypher*: Neo4j's query language; you draw the pattern you want with arrows.

**Files:** `pipeline/load_neo4j.py`

---

## Step 6: Competency questions as queries

**What:** each competency question is answered by real queries.

| CQ | Question | Answered from |
|---|---|---|
| CQ1 | Which services use data to train AI, and can users opt out? | Neo4j |
| CQ3 | Which services share any kind of location data for advertising? | GraphDB, then Neo4j |
| CQ4 | Which personal data does a service collect, including data it doesn't call personal? | GraphDB, then Neo4j |
| CQ12 | Which named companies receive data from the most services? | Neo4j |

**How a hybrid CQ works (CQ3):**
1. GraphDB answers "which concepts count as location data?" → precise location,
   approximate location, location data.
2. Neo4j answers "which flows share any of those for advertising?"
3. The policy never has to say "location data": a flow about GPS coordinates is found
   because the hierarchy knows it is location data.

**Concept:** competency questions define what the knowledge graph must be able to answer.
They drive the ontology design, and once the graph exists they become its test suite.

**Files:** `eval/run_cqs.py` (run with `--explain` to see the queries)
