"""
PrivacyLens agent: answers questions about privacy policies using the knowledge graph.

How it works:
  1. The question goes to the LLM together with descriptions of the tools in tools.py.
  2. The LLM either answers, or asks to call one or more tools.
  3. This script runs the requested tools and sends the results back.
  4. Repeat until the LLM answers (or MAX_STEPS is reached).
The LLM never queries the databases itself; it can only ask for these tools.

Usage:
    python agent/agent.py "Does Zoom use my meeting recordings to train AI?"
    python agent/agent.py "Which services share location data for advertising?" --trace
--trace prints every tool call and a preview of its result, so you can see how the
agent reached its answer.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import TOOLS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
MODEL = os.getenv("OPENAI_AGENT_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))
MAX_STEPS = 8
MAX_TOOL_OUTPUT = 12000      # characters of tool output sent back to the LLM

INSTRUCTIONS = """You answer questions about what companies' privacy policies say, using ONLY
the PrivacyLens knowledge graph through the tools provided. Never answer from your own
knowledge of these companies.

How to work:
- Turn the user's words into vocabulary concepts with find_concept (e.g. "GPS" ->
  PL-003 Precise location). Expand a concept with concepts_under to include narrower
  ones (e.g. all kinds of location data) before filtering flows.
- Use find_flows to get data practices with their exact source sentences. Use
  list_services to find service ids.
- For jurisdiction questions (e.g. "I live in California"), use laws_and_rights and
  broader_terms (which shows law-specific categories, e.g. sensitive under the CCPA).
- Use run_cypher only when the other tools can't answer; call graph_schema first.

How to answer:
- Base every claim on tool results, and quote the source sentence for each key claim,
  naming the document it comes from.
- Keep certainty exact: "does" means the policy states it; "may" means the policy says it
  may happen; "does_not" means the policy denies it. Never turn "may" into "does".
- Mention opt-outs and regional exclusions when the results include them.
- If the graph has no matching practice, say the policy text in the graph does not
  mention it. Do not conclude that the company does not do it.
- If a result has review_flags, say that this item is flagged for review.
- You describe what policies say; you do not give legal advice.
"""

ID_LIST = {"type": "array", "items": {"type": "string"},
           "description": "Concept IDs such as PL-003"}
TOOL_SPECS = [
    {"name": "find_concept",
     "description": "Find vocabulary concepts whose label or alternative wording contains the text.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]}},
    {"name": "concepts_under",
     "description": "A concept and every concept below it in the hierarchy.",
     "parameters": {"type": "object", "properties": {"concept_id": {"type": "string"}},
                    "required": ["concept_id"]}},
    {"name": "broader_terms",
     "description": "Everything a concept falls under, including law-specific categories.",
     "parameters": {"type": "object", "properties": {"concept_id": {"type": "string"}},
                    "required": ["concept_id"]}},
    {"name": "laws_and_rights",
     "description": "Laws in the ontology, where they apply, and the rights they grant.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_services",
     "description": "Services in the corpus (with their ids) and their documents.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "find_flows",
     "description": ("Data practices matching the filters, each with its exact source sentence. "
                     "All filters are optional; expand concepts with concepts_under first."),
     "parameters": {"type": "object", "properties": {
         "service_id": {"type": "string", "description": "e.g. 'zoom'"},
         "action_ids": {**ID_LIST, "description": "PL-014 Collect, PL-015 Share, PL-016 Sell, PL-025 Use"},
         "data_ids": ID_LIST,
         "purpose_ids": ID_LIST,
         "recipient_ids": ID_LIST,
         "certainty": {"type": "string", "enum": ["does", "may", "does_not"]},
         "limit": {"type": "integer", "description": "max rows (default 25)"}}}},
    {"name": "run_cypher",
     "description": "Run a read-only Cypher query on the Neo4j graph. Call graph_schema first.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"]}},
    {"name": "graph_schema",
     "description": "Description of the Neo4j graph's nodes and relationships.",
     "parameters": {"type": "object", "properties": {}}},
]
OPENAI_TOOLS = [{"type": "function", **spec} for spec in TOOL_SPECS]


def run_tool(name, arguments):
    """Run one tool; errors are returned to the LLM as text so it can correct itself."""
    try:
        args = json.loads(arguments or "{}")
        result = TOOLS[name](**args)
        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        text = json.dumps({"error": f"{type(e).__name__}: {e}"})
    if len(text) > MAX_TOOL_OUTPUT:
        text = text[:MAX_TOOL_OUTPUT] + ' ... [truncated: narrow the filters]'
    return text


def ask(question, client=None, trace=False):
    """Answer one question. Returns (answer, list of tool calls made)."""
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    history = [{"role": "user", "content": question}]
    calls = []
    for step in range(1, MAX_STEPS + 1):
        response = client.responses.create(model=MODEL, instructions=INSTRUCTIONS,
                                           input=history, tools=OPENAI_TOOLS)
        requested = [item for item in response.output if item.type == "function_call"]
        if not requested:
            return response.output_text, calls
        history += response.output
        for item in requested:
            output = run_tool(item.name, item.arguments)
            calls.append({"step": step, "tool": item.name, "arguments": item.arguments})
            if trace:
                print(f"  [step {step}] {item.name}({item.arguments})")
                print(f"           -> {output[:200]}{'...' if len(output) > 200 else ''}")
            history.append({"type": "function_call_output", "call_id": item.call_id,
                            "output": output})
    return "I could not finish within the step limit; try a narrower question.", calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--trace", action="store_true", help="show tool calls")
    args = parser.parse_args()
    if args.trace:
        print(f"Question: {args.question}\nModel: {MODEL}\n")
    answer, calls = ask(args.question, trace=args.trace)
    print(("\n" if args.trace else "") + answer)
    if args.trace:
        print(f"\n({len(calls)} tool calls)")


if __name__ == "__main__":
    main()
