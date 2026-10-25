"""
PrivacyLens Step 3: build the ontology from the controlled vocabulary.

Reads   ontology/controlled_vocabulary.csv
Writes  ontology/privacylens.ttl            (the ontology: open it in Protégé, load it into GraphDB)
        ontology/privacylens_with_examples.ttl  (ontology + two illustrative test flows, with --examples;
                                                 open this one in Protégé to try the reasoner)

Usage:
    python pipeline/build_ontology.py
    python pipeline/build_ontology.py --examples

Design (follows the course pipeline: vocabulary -> taxonomy -> thesaurus -> ontology):
  * Vocabulary terms become SKOS concepts with opaque IDs (concept/PL-003), so labels
    can change without breaking identifiers.
  * Taxonomy: skos:broader links from TAXONOMY below (poly-hierarchy is allowed).
  * Thesaurus: skos:altLabel for variants, skos:exactMatch/closeMatch/... for mappings
    to DPV and between general and law-specific terms.
  * Ontology: OWL classes and properties for the structure (Service, Policy, Flow,
    Statement, Law, Jurisdiction), plus defined classes a reasoner can fill in.
"""
import argparse
import csv
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS, XSD

ROOT = Path(__file__).resolve().parent.parent
VOCAB_CSV = ROOT / "ontology" / "controlled_vocabulary.csv"
OUT_TTL = ROOT / "ontology" / "privacylens.ttl"
EXAMPLES_TTL = ROOT / "ontology" / "privacylens_with_examples.ttl"

# Change BASE to a domain or GitHub Pages URL you control before publishing.
BASE = "https://privacylens.example.org/"
PL = Namespace(BASE + "ontology#")
C = Namespace(BASE + "concept/")
DPV = Namespace("https://w3id.org/dpv#")
PD = Namespace("https://w3id.org/dpv/pd#")

MAPPING_PREDICATES = {
    "exactMatch": SKOS.exactMatch,
    "closeMatch": SKOS.closeMatch,
    "broadMatch": SKOS.broadMatch,
    "narrowMatch": SKOS.narrowMatch,
}

# facet column -> concept scheme
SCHEMES = {
    "data type": ("DataTypeScheme", "Data types"),
    "purpose": ("PurposeScheme", "Purposes"),
    "action": ("ActionScheme", "Actions"),
    "recipient": ("RecipientScheme", "Recipient categories"),
    "role": ("RoleScheme", "Roles"),
    "right": ("RightScheme", "Rights"),
    "control": ("ControlScheme", "Controls"),
}

# Taxonomy stage: child -> list of broader concepts (by vocabulary ID).
TAXONOMY = {
    "PL-002": ["PL-001"],             # Location data < Personal data
    "PL-003": ["PL-002", "PL-105"],   # Precise location < Location data; < Sensitive PI (CCPA)
    "PL-004": ["PL-002"],             # Approximate location < Location data
    "PL-005": ["PL-001"],             # Contact information < Personal data
    "PL-006": ["PL-001"],             # User content < Personal data
    "PL-007": ["PL-001"],             # Device identifier < Personal data
    "PL-008": ["PL-001"],             # Usage data < Personal data
    "PL-024": ["PL-001"],             # Sensitive personal data < Personal data
    "PL-010": ["PL-011"],             # Advertising < Marketing (as in DPV)
    "PL-012": ["PL-013"],             # AI model training < Research and development
    "PL-018": ["PL-017"],             # Advertising partner < Third party
}

# Laws, where they apply, the rights they grant, and what those rights concern.
LAWS = {
    "GDPR": ("General Data Protection Regulation", "EEA", "European Economic Area"),
    "CCPA": ("California Consumer Privacy Act", "California", "California"),
    "DPDP": ("Digital Personal Data Protection Act, 2023", "India", "India"),
}
RIGHTS = {
    "PL-020": ("CCPA", ["PL-106", "PL-107"]),  # opt out of Sale (CCPA) and Sharing (CCPA)
    "PL-021": ("GDPR", ["PL-011"]),            # object, including to marketing
}


def resolve(target):
    """Turn an authority_mapping value into an IRI, or None if it isn't resolvable."""
    target = target.strip()
    if target.startswith("PL-"):
        return C[target]
    if target.startswith("dpv:"):
        return DPV[target[4:]]
    if target.startswith("pd:"):
        return PD[target[3:]]
    return None  # e.g. "GDPR Article 21": kept as a note, not a mapping


def add_schema(g):
    """Classes and properties for the structure of the graph."""
    onto = URIRef(BASE + "ontology")
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, RDFS.label, Literal("PrivacyLens ontology", lang="en")))
    g.add((onto, DCTERMS.description, Literal(
        "What privacy policies say about collecting, sharing and using personal data, "
        "and what those terms mean under GDPR, the CCPA and India's DPDP Act.", lang="en")))
    g.add((onto, OWL.versionInfo, Literal("0.1")))

    classes = {
        "Service": "An online product or company whose privacy documents are analysed.",
        "Policy": "One privacy document (policy, notice or terms) for one region, as collected.",
        "Flow": "One statement in a policy about collecting, sharing, selling or using a type of data.",
        "Statement": "The exact sentence from a policy that supports a flow.",
        "Organisation": "A named company that receives data, such as a specific advertising network.",
        "Law": "A privacy law.",
        "Jurisdiction": "A place where a law applies.",
        "Certainty": "How firmly a policy states a practice: does, may, or does not.",
    }
    for name, comment in classes.items():
        g.add((PL[name], RDF.type, OWL.Class))
        g.add((PL[name], RDFS.label, Literal(name, lang="en")))
        g.add((PL[name], RDFS.comment, Literal(comment, lang="en")))

    for name, label in [("Does", "does"), ("May", "may"), ("DoesNot", "does not")]:
        g.add((PL[name], RDF.type, OWL.NamedIndividual))
        g.add((PL[name], RDF.type, PL.Certainty))
        g.add((PL[name], RDFS.label, Literal(label, lang="en")))

    object_props = [
        ("ofService", "Policy", "Service"),
        ("hasFlow", "Policy", "Flow"),
        ("hasAction", "Flow", None),          # -> concept in ActionScheme
        ("concernsData", "Flow", None),       # -> concept in DataTypeScheme
        ("hasPurpose", "Flow", None),         # -> concept in PurposeScheme
        ("recipientCategory", "Flow", None),  # -> concept in RecipientScheme
        ("recipient", "Flow", "Organisation"),
        ("hasCertainty", "Flow", "Certainty"),
        ("excludedIn", "Flow", "Jurisdiction"),
        ("supportedBy", "Flow", "Statement"),
        ("appliesIn", "Law", "Jurisdiction"),
        ("grantsRight", "Law", None),         # -> concept in RightScheme
        ("rightConcerns", None, None),        # right concept -> concept it concerns
        ("definedIn", None, "Law"),           # law-specific concept -> the law
    ]
    for name, domain, rng in object_props:
        g.add((PL[name], RDF.type, OWL.ObjectProperty))
        if domain:
            g.add((PL[name], RDFS.domain, PL[domain]))
        if rng:
            g.add((PL[name], RDFS.range, PL[rng]))

    data_props = [
        ("region", "Policy", XSD.string),
        ("sourceURL", "Policy", XSD.anyURI),
        ("collectedOn", "Policy", XSD.date),
        ("effectiveDate", "Policy", XSD.date),
        ("documentType", "Policy", XSD.string),
        ("text", "Statement", XSD.string),
        ("optOutAvailable", "Flow", XSD.string),  # yes / no / not_stated
    ]
    for name, domain, rng in data_props:
        g.add((PL[name], RDF.type, OWL.DatatypeProperty))
        g.add((PL[name], RDFS.domain, PL[domain]))
        g.add((PL[name], RDFS.range, rng))

    g.add((PL.mappingStatus, RDF.type, OWL.AnnotationProperty))


def add_defined_class(g, name, label, comment, purpose_id, certainty):
    """name == Flow AND hasPurpose value <purpose> AND hasCertainty value <certainty>."""
    def has_value(prop, value):
        r = BNode()
        g.add((r, RDF.type, OWL.Restriction))
        g.add((r, OWL.onProperty, prop))
        g.add((r, OWL.hasValue, value))
        return r

    members = BNode()
    Collection(g, members, [PL.Flow, has_value(PL.hasPurpose, C[purpose_id]),
                            has_value(PL.hasCertainty, certainty)])
    intersection = BNode()
    g.add((intersection, RDF.type, OWL.Class))
    g.add((intersection, OWL.intersectionOf, members))

    g.add((PL[name], RDF.type, OWL.Class))
    g.add((PL[name], RDFS.label, Literal(label, lang="en")))
    g.add((PL[name], RDFS.comment, Literal(comment, lang="en")))
    g.add((PL[name], OWL.equivalentClass, intersection))


def add_vocabulary(g, rows):
    ids = {r["id"] for r in rows}
    for facet, (scheme, label) in SCHEMES.items():
        g.add((PL[scheme], RDF.type, SKOS.ConceptScheme))
        g.add((PL[scheme], SKOS.prefLabel, Literal(label, lang="en")))

    for r in rows:
        c = C[r["id"]]
        g.add((c, RDF.type, SKOS.Concept))
        g.add((c, RDF.type, OWL.NamedIndividual))
        g.add((c, SKOS.notation, Literal(r["id"])))
        g.add((c, SKOS.prefLabel, Literal(r["preferred_label"], lang="en")))
        for alt in filter(None, (a.strip() for a in r["alternative_labels"].split(";"))):
            g.add((c, SKOS.altLabel, Literal(alt, lang="en")))
        g.add((c, SKOS.definition, Literal(r["definition"], lang="en")))
        if r["scope_note"]:
            g.add((c, SKOS.scopeNote, Literal(r["scope_note"], lang="en")))
        if r["evidence"]:
            g.add((c, DCTERMS.source, Literal(r["evidence"], lang="en")))
        if r["date_added"]:
            g.add((c, DCTERMS.created, Literal(r["date_added"], datatype=XSD.date)))
        if r["mapping_status"]:
            g.add((c, PL.mappingStatus, Literal(r["mapping_status"])))

        scheme = SCHEMES.get(r["facet"])
        if scheme:
            g.add((c, SKOS.inScheme, PL[scheme[0]]))
        else:
            print(f"  warning: {r['id']} has unknown facet '{r['facet']}'")

        if r["context"] in LAWS:
            g.add((c, PL.definedIn, PL[r["context"]]))

        pred = MAPPING_PREDICATES.get(r["mapping_type"])
        target = resolve(r["authority_mapping"])
        if pred and target is not None:
            if str(target).startswith(str(C)) and r["authority_mapping"] not in ids:
                print(f"  warning: {r['id']} maps to unknown concept {r['authority_mapping']}")
            else:
                g.add((c, pred, target))
        elif r["authority_mapping"] and target is None:
            g.add((c, SKOS.note, Literal(f"Legal reference: {r['authority_mapping']}", lang="en")))

    for child, parents in TAXONOMY.items():
        for parent in parents:
            if child not in ids or parent not in ids:
                print(f"  warning: taxonomy link {child} -> {parent} uses an unknown ID")
                continue
            g.add((C[child], SKOS.broader, C[parent]))
            g.add((C[parent], SKOS.narrower, C[child]))

    for r in rows:  # top concepts: no broader term inside their own scheme
        if r["id"] not in TAXONOMY and r["facet"] in SCHEMES:
            g.add((PL[SCHEMES[r["facet"]][0]], SKOS.hasTopConcept, C[r["id"]]))


def add_laws(g, ids):
    for law, (name, jur, jur_label) in LAWS.items():
        g.add((PL[law], RDF.type, OWL.NamedIndividual))
        g.add((PL[law], RDF.type, PL.Law))
        g.add((PL[law], RDFS.label, Literal(name, lang="en")))
        g.add((PL[jur], RDF.type, OWL.NamedIndividual))
        g.add((PL[jur], RDF.type, PL.Jurisdiction))
        g.add((PL[jur], RDFS.label, Literal(jur_label, lang="en")))
        g.add((PL[law], PL.appliesIn, PL[jur]))
    for right, (law, concerns) in RIGHTS.items():
        if right not in ids:
            continue
        g.add((PL[law], PL.grantsRight, C[right]))
        for target in concerns:
            if target in ids:
                g.add((C[right], PL.rightConcerns, C[target]))


def build_examples():
    """Two illustrative flows (not from the corpus) to test the reasoner."""
    g = Graph()
    g.bind("pl", PL)
    g.bind("c", C)
    svc = PL["example_service"]
    pol = PL["example_policy"]
    g.add((svc, RDF.type, PL.Service))
    g.add((svc, RDFS.label, Literal("ExampleApp (illustrative test data)", lang="en")))
    g.add((pol, RDF.type, PL.Policy))
    g.add((pol, PL.ofService, svc))
    examples = [
        ("example_flow_1", PL.Does, "We use the content you post to train our AI models."),
        ("example_flow_2", PL.DoesNot, "We do not use your messages to train AI models."),
    ]
    for name, certainty, sentence in examples:
        flow, stmt = PL[name], PL[name + "_statement"]
        g.add((pol, PL.hasFlow, flow))
        g.add((flow, RDF.type, PL.Flow))
        g.add((flow, PL.hasAction, C["PL-014"]))
        g.add((flow, PL.concernsData, C["PL-006"]))
        g.add((flow, PL.hasPurpose, C["PL-012"]))
        g.add((flow, PL.hasCertainty, certainty))
        g.add((flow, PL.supportedBy, stmt))
        g.add((stmt, RDF.type, PL.Statement))
        g.add((stmt, PL.text, Literal(sentence, datatype=XSD.string)))
    return g


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", action="store_true",
                        help="also write privacylens_with_examples.ttl for testing the reasoner")
    args = parser.parse_args()

    with VOCAB_CSV.open(newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    ids = {r["id"] for r in rows}

    g = Graph()
    for prefix, ns in [("pl", PL), ("c", C), ("dpv", DPV), ("pd", PD),
                       ("skos", SKOS), ("owl", OWL), ("dcterms", DCTERMS)]:
        g.bind(prefix, ns)

    add_schema(g)
    add_vocabulary(g, rows)
    add_laws(g, ids)
    add_defined_class(g, "AITrainingFlow", "AI training flow",
                      "A flow stating that data IS used for AI model training.", "PL-012", PL.Does)
    add_defined_class(g, "PossibleAITrainingFlow", "Possible AI training flow",
                      "A flow stating that data MAY be used for AI model training.", "PL-012", PL.May)

    g.serialize(OUT_TTL, format="turtle")

    # Self-check: re-read the file and report what's in it.
    check = Graph().parse(OUT_TTL, format="turtle")
    concepts = set(check.subjects(RDF.type, SKOS.Concept))
    print(f"Wrote {OUT_TTL.relative_to(ROOT)}: {len(check)} triples, {len(concepts)} concepts")
    for facet, (scheme, label) in SCHEMES.items():
        n = len(set(check.subjects(SKOS.inScheme, PL[scheme])))
        print(f"  {label}: {n}")
    print(f"  broader links: {len(list(check.triples((None, SKOS.broader, None))))}")
    print(f"  mappings: {sum(len(list(check.triples((None, p, None)))) for p in MAPPING_PREDICATES.values())}")

    if args.examples:
        combined = Graph()
        for prefix, ns in g.namespaces():
            combined.bind(prefix, ns)
        combined += g
        combined += build_examples()
        combined.serialize(EXAMPLES_TTL, format="turtle")
        print(f"Wrote {EXAMPLES_TTL.relative_to(ROOT)}: ontology + 2 illustrative test flows (not corpus data)")


if __name__ == "__main__":
    main()
