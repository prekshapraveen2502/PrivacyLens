# PrivacyLens

A hybrid knowledge graph and agentic platform for understanding how online services collect, share, and use personal data, including for AI training, across different jurisdictions.

Built for DAMG 6300: Knowledge Graphs with GenAI/DBs (Northeastern University, Fall 2026).

**Status:** in development.

## Idea

PrivacyLens builds on PoliGraph (Cui et al., USENIX Security 2023), which models each privacy policy's own definitions of terms (local) separately from external standards and laws (global). PrivacyLens implements that distinction across two graph paradigms:

- **Neo4j (property graph):** what each policy says: data flows between services, data types, third parties, and purposes
- **GraphDB (RDF/OWL):** what terms mean and which laws apply where: an ontology based on the W3C Data Privacy Vocabulary (DPV), extended with jurisdictions, rights, and AI-training concepts

An LLM agent queries both graphs to answer questions and run tasks such as policy audits.

## Repository structure

| Folder | Contents |
|---|---|
| `data/` | `manifest.csv`: the list of policies in the corpus, with URLs and collection dates |
| `pipeline/` | Collection, extraction, validation, and loading code |
| `ontology/` | Controlled vocabulary, ontology (Turtle), and SHACL shapes |
| `queries/` | Cypher and SPARQL queries for the competency questions |
| `agent/` | Agent and tools (later phase) |
| `eval/` | Competency-question tests and evaluation |
| `scripts/` | Utility scripts, such as the setup check |
| `docs/` | Proposal, competency questions, architecture diagram |

## Setup

Requires Python 3.11+, Neo4j Desktop (with the Graph Data Science plugin), GraphDB Free, and Protégé.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in your values
python scripts/check_setup.py
```

## Collecting the corpus

1. Add each policy's URL to `data/manifest.csv`.
2. Run `python pipeline/collect_policies.py`.
3. For any document marked `check_manually`, paste the policy text into the file the script names and set its status to `ok`.

Collected texts are saved to `data/policies/`, which is not committed to the repository because the policies are copyrighted by their publishers.

## Disclaimer

PrivacyLens summarizes what privacy policies state. It does not provide legal advice.
