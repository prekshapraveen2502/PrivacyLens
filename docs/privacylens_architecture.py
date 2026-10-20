"""
PrivacyLens architecture diagram (mingrammer/diagrams).

Run:     python privacylens_architecture.py
Output:  privacylens_architecture.png (same folder)

Optional logos: create an ./icons folder next to this script and drop in any of
  graphdb.png, protege.png, llm.png, mcp.png, streamlit.png
They are picked up automatically; otherwise generic icons are used.
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
    """Use a custom logo from ./icons if present, otherwise a generic icon."""
    if icon and (ICONS / icon).exists():
        return Custom(label, str(ICONS / icon))
    return fallback(label)


graph_attr = {
    "fontsize": "22",
    "fontname": "Helvetica",
    "pad": "0.6",
    "nodesep": "0.7",
    "ranksep": "1.0",
    "splines": "spline",
}
node_attr = {"fontsize": "12", "fontname": "Helvetica"}
edge_attr = {"fontsize": "11", "fontname": "Helvetica", "color": "#555555"}


def cluster_style(bg):
    return {"bgcolor": bg, "pencolor": "#BBBBBB", "fontsize": "14",
            "fontname": "Helvetica", "margin": "16"}


with Diagram(
    "PrivacyLens: Hybrid Knowledge Graph and Agentic Architecture",
    filename="privacylens_architecture",
    outformat="png",
    direction="TB",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with Cluster("Inputs", graph_attr=cluster_style("#F4F4F4")):
        policies = MultipleDocuments("Privacy policies\n(15–20 services)")
        laws = Document("Regulation texts\n(GDPR, CCPA, DPDP)")
        dpv = Document("W3C DPV\nvocabulary")

    ontology = node("Protégé ontology\n(DPV + laws +\nAI-training terms)", Document, "protege.png")

    with Cluster("Construction pipeline", graph_attr=cluster_style("#EEEDFE")):
        extract = node("LLM extraction\n(structured records)", Python, "llm.png")
        quote = Python("Quote check\n(reject unsupported)")
        shacl = Python("SHACL validation\n(pyshacl)")

    with Cluster("Hybrid knowledge graph", graph_attr=cluster_style("#E1F5EE")):
        neo4j = Neo4J("Neo4j\nwhat policies say\n(flows, local terms, PageRank)")
        graphdb = node("GraphDB\nwhat terms mean\n(ontology, laws, reasoning)", Storage, "graphdb.png")

    with Cluster("Agentic layer", graph_attr=cluster_style("#FAEEDA")):
        agent = Python("Agent (LLM + tools)\nCypher · SPARQL ·\nextract · validate")
        mcp = node("MCP server", Server, "mcp.png")
        ui = node("Streamlit UI\n(user picks jurisdiction)", Client, "streamlit.png")

    users = Users("Consumers · Developers\nBusinesses · Researchers")
    evaluation = Python("Evaluation harness\n(competency questions\nvs vector baseline)")

    # Construction flow
    policies >> extract >> quote >> shacl
    shacl >> Edge(label="flows + provenance") >> neo4j
    shacl >> Edge(label="instances") >> graphdb

    # Ontology build
    [laws, dpv] >> ontology >> Edge(label="load") >> graphdb

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
