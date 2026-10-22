"""
PrivacyLens architecture diagram (mingrammer/diagrams), version 2.

Run:     python docs/privacylens_architecture.py
Output:  privacylens_architecture.png in the folder you run it from

Optional logos: create an icons/ folder next to this script and add any of
  llm.png, protege.png, graphdb.png, mcp.png, streamlit.png, agent.png
They are used automatically; otherwise generic icons are shown.
"""
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.custom import Custom
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Client, Users
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Neo4J
from diagrams.programming.flowchart import Document, MultipleDocuments
from diagrams.programming.language import Python

ICONS = Path(__file__).parent / "icons"


def node(label, fallback, icon=None):
    """Use a custom logo from icons/ if present, otherwise a generic icon."""
    if icon and (ICONS / icon).exists():
        return Custom(label, str(ICONS / icon))
    return fallback(label)


def cluster_style(bg):
    return {"bgcolor": bg, "pencolor": "#BBBBBB", "fontsize": "14",
            "fontname": "Helvetica", "margin": "16"}


graph_attr = {
    "fontsize": "22",
    "fontname": "Helvetica",
    "pad": "0.6",
    "nodesep": "0.8",
    "ranksep": "1.1",
    "splines": "spline",
}
node_attr = {"fontsize": "12", "fontname": "Helvetica"}
edge_attr = {"fontsize": "11", "fontname": "Helvetica", "color": "#555555"}

with Diagram(
    "PrivacyLens: Hybrid Knowledge Graph and Agentic Architecture",
    filename="privacylens_architecture",
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    # ---------- Corpus collection ----------
    with Cluster("Corpus collection", graph_attr=cluster_style("#F4F4F4")):
        manifest = Document("manifest.csv\n(URL, region,\ncollection date)")
        collector = Python("collect_policies.py\n(download or\nmanual paste)")
        policies = MultipleDocuments("Policy texts\n13 documents,\n8 services")

    # ---------- Vocabulary and ontology ----------
    with Cluster("Vocabulary and ontology", graph_attr=cluster_style("#EAF3DE")):
        laws = Document("Law texts\n(GDPR, CCPA, DPDP)")
        dpv = Document("W3C DPV\nvocabulary")
        vocab = Document("Controlled vocabulary\n(general + law-specific\nterms, SKOS mappings)")
        ontology = node("Protégé ontology\n(classes, jurisdictions,\nrights, AI training)", Document, "protege.png")

    # ---------- Construction pipeline ----------
    with Cluster("Construction pipeline", graph_attr=cluster_style("#EEEDFE")):
        extract = node("LLM extraction\n(records use\nvocabulary terms)", Python, "llm.png")
        quote = Python("Quote check\n(reject unsupported)")
        shacl = Python("SHACL validation\n(pyshacl)")

    # ---------- Hybrid knowledge graph ----------
    with Cluster("Hybrid knowledge graph", graph_attr=cluster_style("#E1F5EE")):
        neo4j = Neo4J("Neo4j\nwhat policies say\n(flows, PageRank)")
        graphdb = node("GraphDB\nwhat terms mean\n(laws, reasoning)", Storage, "graphdb.png")

    # ---------- Agentic layer ----------
    with Cluster("Agentic layer", graph_attr=cluster_style("#FAEEDA")):
        agent = node("Agent (LLM + tools)\nCypher · SPARQL ·\nextract · validate", Python, "agent.png")
        mcp = node("MCP server", Server, "mcp.png")
        ui = node("Streamlit UI\n(user picks\njurisdiction)", Client, "streamlit.png")

    # ---------- Evaluation ----------
    with Cluster("Evaluation", graph_attr=cluster_style("#F4F4F4")):
        evaluation = Python("Evaluation harness\n(16 competency questions\nvs vector baseline)")

    users = Users("Consumers · Developers\nBusinesses · Researchers")

    # Corpus collection
    manifest >> collector >> policies >> extract

    # Vocabulary and ontology build
    [laws, dpv] >> vocab >> ontology
    vocab >> Edge(label="allowed terms", style="dashed") >> extract
    ontology >> Edge(label="load") >> graphdb

    # Construction pipeline
    extract >> quote >> shacl
    shacl >> Edge(label="flows + provenance") >> neo4j
    shacl >> Edge(label="instances") >> graphdb

    # Link between the two stores
    neo4j - Edge(label="shared IRIs", style="dashed", color="#1D9E75", constraint="false") - graphdb

    # Agent queries both stores
    neo4j << Edge(label="Cypher") << agent
    graphdb << Edge(label="SPARQL") << agent

    # Interfaces
    agent >> Edge(label="tools exposed") >> mcp
    agent >> ui
    ui << Edge(label="question + jurisdiction") << users

    # Evaluation (outside the runtime flow)
    agent << Edge(label="tests", style="dashed", color="#888888") << evaluation
