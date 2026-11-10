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

---

## Step 7: Evaluating and fixing extraction (November 3–4)

Every fix below was found by checking real output, then measured again.

**1. The AI-training definition was too loose.**
- Spot check: 3 of 8 AI-training flows were wrong, all in the same way. Sentences about
  *using* AI tools, or sharing data with AI vendors, were tagged as AI model training.
- Cause: the scope note for PL-012 only required the sentence to *mention* AI.
- Fix: tightened the definition in the vocabulary and the matching prompt rule.
- After: purpose correct on 24 of 25 AI-training records; purpose and certainty both
  correct on 21 of 25; about 18 of 20 training sentences in the graph were found.
- Lesson: **definitions are context engineering.** The words in the vocabulary directly
  control what the LLM extracts.

**2. Two kinds of AI training were missed.**
- X shares data with recipients who may train *their own* AI models; Meta trains
  "algorithms". Both were refined into the definition.

**3. The LLM labels "may" as "does".**
- Many sentences saying the company *may* do something were labeled as definite.
- Fix: rule-based checks (`pipeline/certainty_rules.py`). A "does" label on a sentence
  saying may/might/could is corrected to "may"; the LLM's original label is kept in
  `llm_certainty`. Other mismatches are flagged for review, not changed.
- The first version of the rules also treated "can" and "no longer" as signals; reviewing
  the flags showed those caused false alarms, so they were removed.
- Result on the current baseline: 105 of 1,326 records corrected, 45 flagged for review.
  In a random sample of corrections, the rule was right in every case checked.
- Lesson: **LLM plus simple rules** is more reliable than either alone. The LLM reads the
  language; cheap deterministic rules check it.

**4. The LLM gives different results on different runs.**
- Re-running the same corpus changed results that the fix shouldn't have touched
  (LinkedIn's AI-training sentence disappeared; Uber's US notice went from 19 records to 6).
- Response: the latest extraction is kept as the **baseline**. Rule changes are applied
  with `pipeline/postprocess_flows.py` (no LLM calls, same result every time), and future
  prompt changes are tested on a few documents before the full corpus.
- Lesson: separate the expensive, variable step (the LLM) from the cheap, deterministic
  step (the rules), and measure variation instead of assuming outputs are stable.

**5. Some practices are spread across sentences.**
- CQ3 (location shared for advertising) returned nothing. Searching the original text
  (`scripts/search_policies.py`) found one sentence that should answer it: Uber shares
  approximate location "with these intermediaries".
- The sentence was in the graph, but with purpose = service provision and recipient =
  third party. The word that makes it about advertising, "Ad intermediaries", is in the
  previous sentence.
- This is the problem PoliGraph was designed to solve: earlier tools read policies one
  sentence at a time, but a practice is often described across several.
- Fix: a prompt rule telling the model to use earlier context for phrases like "these
  intermediaries". Tested on Uber's policy only; CQ3 now returns Uber sharing approximate
  location for advertising, with an opt-out.

**Recall against the source text:** of 18 sentences that clearly state an AI-training
practice, 15 were extracted (~83%); the other 3 are all flagged by the backstop rule.

**Current baseline:** 13 documents from 8 services; 1,326 accepted records (57 rejected
by the quote check); 1,202 flows in Neo4j; 38 vocabulary concepts.

**Concepts:** precision (how many extracted items are right), recall (how many true items
were found), and why a tighter rule can raise one and lower the other.

**Files:** `pipeline/certainty_rules.py`, `pipeline/postprocess_flows.py`,
`eval/run_cqs.py`, Cypher checks in Neo4j Browser

## Step 8: The agent (November 4)

**What:** an LLM agent answers plain-English questions by calling tools over both graphs
(`agent/tools.py`, `agent/agent.py`). It never queries the databases itself.

**Tools:** concept lookup, hierarchy expansion, broader terms and laws (GraphDB); services,
filtered data flows with source sentences, read-only Cypher (Neo4j). Cypher is refused if
it contains write keywords, and runs in a read-only transaction.

**Testing on three questions (Zoom AI training, location for advertising, California rights):**
- Run 1, 1 of 3: empty filter lists matched nothing (tool bug); "AI" matched inside
  "details" (substring bug); the vocabulary lacked users' words ("recordings", "audio").
- Run 2, 2 of 3: the agent kept filtering by certainty despite instructions, hiding
  Zoom's "does not" statement.
- Run 3, 3 of 3: the certainty filter was removed from the tool the agent sees.

**Lessons:**
- The agent's traces are a query log: they show the gap between users' words and the
  vocabulary (Talisman, Chapter 1, on search logs). User wording was added to PL-006.
- Instructions are requests, not guarantees. When the model repeats a wrong call, change
  the tool so the wrong call isn't possible.
- Three questions show the debugging works, not that the agent is accurate.

---

## Step 9: Evaluating the agent (November 4–9)

**What:** a gold set of 13 questions (`eval/agent_eval_set.json`): 8 normal, 2 nuance,
3 trap. Each is mapped to a competency question and has gold points and required
mentions. `eval/eval_agent.py` runs every question twice and scores each answer three ways:
- *Grounding* (automatic): every quoted sentence must exist among the policy sentences in
  Neo4j. An ungrounded quote may be a hallucination.
- *Mentions* (automatic): the answer must name the required items.
- *Judge* (LLM-as-judge): a second model compares the answer with the gold points and
  returns correct / partial / incorrect, with what is missing and what is unsupported.
- *Consistency*: whether a question gets the same verdict on both runs.

**Results on the dev set (same judge model every time):** 50% → 65% → 73% → 92% correct.

**Run 4 (November 9):**
- 24 of 26 answers correct (92%), 2 partial, 0 incorrect.
- Same verdict on both runs for 13 of 13 questions (an earlier run had 7 of 13).
- 48 of 49 quoted fragments grounded; no answer missed a required mention.
- Correct on all nuance (4/4) and trap (6/6) answers.

**What changed before run 4:**
1. *Tool fix (Q07).* With `disclosure_only` set, the tool returned only sharing and
   selling flows, so Uber's "does not sell personal information to train large language
   models" came back without Uber's "uses personal information … to train large language
   models". The tool now returns `shared_or_sold` plus
   `other_practices_not_shared_or_sold`, so the agent sees both halves.
2. *Gold correction (Q08).* The gold expected contact information and device identifiers,
   taken from an earlier Uber extraction. After Uber was re-extracted those were no longer
   collection records, so the gold was changed to match the current graph.
3. *Judge calibration.* The judge now accepts wording that means the same as a gold point,
   including the vocabulary's category names.

**Reading the two remaining issues:**
- *Q03, one ungrounded quote.* The quote was "Right to opt out of sale or sharing": the
  name of a right in the ontology, which the agent got from GraphDB. The grounding check
  only covers policy sentences in Neo4j, not ontology labels. Not a hallucination; a limit
  of the check.
- *Q08, partial on both runs.* The agent listed Uber's own data categories (account
  information, demographic data, device data, communications data, in-vehicle recordings
  and others); checked with `grep`, they are real policy text. The gold has only four
  categories because it was built from the graph's collection records, and extraction
  missed several. In Uber's policy these categories are rows of a **table**, and the
  collection verb is in the table's heading, not in each row. Likely cause (not yet
  checked row by row): the extractor has to carry the heading's meaning down to each row.
  The gold was not edited again: changing it a second time after seeing results would make
  the score less trustworthy.

**Link to PoliGraph:** PoliGraph-er cannot parse tables at all (its stated limitation), and
uses a List Annotator to pass "the following information:" down to each list item. Run 4
found the same problem in LLM extraction: it reads the table but misses rows whose meaning
depends on the heading. Table- and heading-aware extraction goes on the roadmap.

**Lessons:**
- **This is a dev-set score after iteration.** The jump to 92% combines a tool fix, a gold
  correction and judge calibration, so not all of it is the agent improving. The honest
  number will come from questions written after the freeze.
- **Read failures before fixing them.** Q08 looked like an agent error; it was a gap
  between the gold and the policy, which in turn exposed an extraction gap.
- **Gold sets need checking too.** A gold answer is only as good as the data it was built
  from.

**Files:** `eval/agent_eval_set.json`, `eval/eval_agent.py`, `agent/tools.py`,
`agent/agent.py`; full answers in `eval/results/` (git-ignored)

---

## Step 10: Freeze (November 9)

**What:** the agent is frozen at git tag `agent-v1`. Nothing in `agent/` changes until the
held-out evaluation is done.

**Concept:** a held-out test set. Every question in the dev set has been used to find and
fix problems, so the dev score is optimistic. New questions, written and committed before
the frozen agent sees them and run once, measure how it does on questions it was not
tuned for.

**Next:** write the held-out questions and commit them, then run `agent-v1` on them once.

---

## Step 11: Held-out evaluation (November 9)

**What:** 9 new questions (`eval/heldout_set.json`), built from graph evidence
(`eval/midterm_evidence.py`, GraphDB SPARQL for H08) and committed before the frozen
agent (`agent-v1`) saw them. Run once with `eval_agent.py --set eval/heldout_set.json`.
No changes afterward.

**Results:**

| | Dev set (tuned) | Held-out |
|---|---|---|
| Correct | 92% | 50% (9/18) |
| Correct or partial | 100% | 83% (15/18) |
| Quotes grounded | 48/49 | 40/43 (93%) |
| Same verdict on both runs | 13/13 | 3/9 |

By type: normal 5/8, nuance 1/6, trap 3/4.

**Reading the failures:**
- *Gold errors (mine).* H02: the gold said the graph has no LinkedIn AI-training
  statement, because my query only checked the "AI model training" purpose label. The
  agent quoted a grounded LinkedIn sentence about training AI models: the sentence is in
  the graph under a different purpose. H06: my query only looked at "may" statements, so
  the gold missed TikTok's EEA "we collect … precise location" statement; the agent
  distinguished the EEA and US policies correctly. H05 run 1: the agent cited a different
  true sentence than the gold expected.
- *Agent errors.* Incomplete lists on cross-service questions (H03 run 2 missed Uber;
  H07 run 2 listed 5 of 8 services). Missing conditions: H01 run 1 missed the "turn off
  Location Services" statement; H04 missed Zoom's "not in the conventional sense" wording
  and the note that ad and analytics services may count as a sale.
- *Judge limits.* True, grounded extra detail is marked "unsupported" when the gold does
  not list it (H04, H07). The judge sees the gold, not the graph.

The score was not changed after the run.

**Lessons:**
- **The held-out drop (92% → 50%) shows how much of the dev score came from tuning.**
- **A gold answer must be checked against the whole graph, not one filtered query.**
  A narrow query produced a wrong gold answer twice.
- **An LLM-as-judge is only as good as its gold.** It cannot tell a hallucination from a
  true detail the gold left out.
- **Nuance is still the weakest area** (1/6): conditions, opt-outs, and region
  differences.
- H02 also shows an extraction gap: LinkedIn's AI-training sentence is in the graph but
  is not labeled with the AI model training purpose.

**Next:** for cross-service questions, a tool that returns per-service counts so lists
are complete; and gold built from several queries per question.

---

## Step 12: Extraction precision and graph metrics (November 9–10)

**Precision sample:** 60 flows (`eval/compute_metrics.py sample`, seed 42, spread across
services), labeled with Claude's help and reviewed by me. Labels: correct (every field
supported), partial (core practice right, one field off), wrong (quote doesn't support it).
- Strict (correct only): 32/60 = 53.3% (95% CI 41–65%)
- Core (correct + partial): 53/60 = 88.3% (95% CI 78–94%); PoliGraph's COLLECT precision
  is 90.4%, which is inside this interval: comparable, on a different dataset.
- Error types: list-item and heading fragments (the same problem as Q08 and PoliGraph's
  List Annotator); reversed direction ("sends your information to us" stored as sharing);
  wrong purpose; one lost negation; non-practice sentences (a user right, a scope sentence).

**From the graph (`eval/midterm_evidence.py`):**
- 63% of location flows (42/67) are found only through the hierarchy.
- 50.9% of flows name only generic "Personal data".
- Certainty: does 77.6%, may 20.4%, does not 2.0%.

**Lesson:** PrivacyLens records more per flow than PoliGraph (purpose, recipient, certainty),
so a strict check is harder to pass. The fair comparison is on the core practice.
