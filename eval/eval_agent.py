"""
PrivacyLens: evaluate the agent on the gold question set (eval/agent_eval_set.json).

For every question, the agent answers RUNS times. Each answer gets three scores:
  * grounding  (automatic): every sentence the agent quotes must exist among the source
                sentences in Neo4j. An ungrounded quote is a hallucination.
  * mentions   (automatic): the answer must name the items in `must_mention`
                ("A|B" means either A or B).
  * judge      (LLM-as-judge): a second LLM call compares the answer with the gold points
                and returns correct / partial / incorrect, with missing points and any
                claims the gold points don't support.
Consistency: whether a question gets the same verdict on every run.

Usage (Neo4j and GraphDB running):
    python eval/eval_agent.py                    # all questions, 2 runs each
    python eval/eval_agent.py --runs 1 --only Q01,Q11
    python eval/eval_agent.py --rescore eval/results/<file>.jsonl            # re-check grounding
    python eval/eval_agent.py --rescore eval/results/<file>.jsonl --rejudge  # and re-judge
Full answers and tool calls are saved to eval/results/ (git-ignored: answers quote policies).
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_SET = ROOT / "eval" / "agent_eval_set.json"
RESULTS = ROOT / "eval" / "results"
sys.path.insert(0, str(ROOT / "agent"))

QUOTE_MAP = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                           "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def normalise(s):
    return re.sub(r"\s+", " ", s.translate(QUOTE_MAP)).strip().lower()


def grounding_key(s):
    """Letters and digits only: ignores spacing, punctuation and quote-style differences,
    including words that are merged in the stored policy text (e.g. "toimprove")."""
    s = re.sub(r"\(opens in a new window\)", "", s, flags=re.I)   # link text left in scraped pages
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------- automatic scores

def quoted_fragments(answer):
    """Text the agent presents as quotation, split at ellipses into checkable fragments."""
    quotes = re.findall(r"[\u201c\"]([^\u201c\u201d\"]{20,}?)[\u201d\"]", answer)
    quotes += [line.lstrip("> ").strip() for line in answer.splitlines()
               if line.startswith(">") and not re.match(r"^>\s*[\u2014\u2013-]", line)]   # skip "> — Source"
    fragments = []
    for q in quotes:
        for part in re.split(r"\s*(?:\u2026|\.\.\.)\s*", q):
            part = part.strip(" .,;:-'\"\u201c\u201d\u2018\u2019*").strip()
            if len(part.split()) >= 5:
                fragments.append(part)
    return list(dict.fromkeys(fragments))


def grounding(answer, corpus):
    fragments = quoted_fragments(answer)
    ungrounded = [f for f in fragments if grounding_key(f) not in corpus]
    return len(fragments), ungrounded


def mentions(answer, must_mention):
    missing = []
    for item in must_mention:
        alternatives = item.split("|")
        if not any(re.search(r"\b" + re.escape(a) + r"\b", answer, re.I) for a in alternatives):
            missing.append(item)
    return missing


# ---------------------------------------------------------------- LLM-as-judge

JUDGE_INSTRUCTIONS = """You grade answers from a privacy-policy assistant against gold points.
The gold points describe what the assistant's knowledge graph contains. Points marked
"(optional)" are not required.

Verdict:
- correct: covers every required gold point and makes no claim that contradicts them.
- partial: covers some required points, or is right but vague, with no serious error.
- incorrect: misses the main point, contradicts a gold point, states something as definite
  that the gold says only "may" happen, or (for questions about services or rights not in
  the graph) invents facts.
Accept wording that means the same as a gold point, including the vocabulary's category
names (e.g. "advertising partner" for "ad intermediary"), and accept extra detail that is
consistent with the gold points.
List any required gold points the answer misses, and any claims in the answer that the
gold points do not support. Be strict and brief."""


def build_judge(client, model):
    from typing import List, Literal
    from pydantic import BaseModel

    class Verdict(BaseModel):
        verdict: Literal["correct", "partial", "incorrect"]
        missing_points: List[str]
        unsupported_claims: List[str]
        reason: str

    def judge(item, answer):
        prompt = (f"Question: {item['question']}\n\nGold points:\n"
                  + "\n".join(f"- {p}" for p in item["gold_points"])
                  + f"\n\nAnswer to grade:\n{answer}")
        r = client.responses.parse(model=model, instructions=JUDGE_INSTRUCTIONS,
                                   input=[{"role": "user", "content": prompt}], text_format=Verdict)
        return r.output_parsed.model_dump()
    return judge


# ---------------------------------------------------------------- main

def load_agent():
    spec = importlib.util.spec_from_file_location("privacylens_agent", ROOT / "agent" / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def statement_corpus():
    import tools
    rows = tools._cypher_read("MATCH (s:Statement) RETURN s.text AS text")
    return "|".join(grounding_key(r["text"]) for r in rows)


def summarize(rows, items, runs):
    # Summary
    print("\n" + "=" * 78 + "\nSUMMARY\n" + "=" * 78)
    verdicts = Counter(r["verdict"] for r in rows)
    total = len(rows)
    print(f"Answers: {total}  ({len(items)} questions x {runs} runs)")
    for v in ("correct", "partial", "incorrect"):
        print(f"  {v:10s} {verdicts[v]:3d}  ({verdicts[v] / total:.0%})")
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r["verdict"] == "correct")
    for t, ok in sorted(by_type.items()):
        print(f"  correct on {t} questions: {sum(ok)}/{len(ok)}")
    quotes = sum(r["quotes"] for r in rows)
    bad = sum(len(r["ungrounded_quotes"]) for r in rows)
    print(f"Quoted fragments grounded in the graph: {quotes - bad}/{quotes}")
    print(f"Answers missing a required mention: {sum(1 for r in rows if r['missing_mentions'])}/{total}")
    if runs > 1:
        same = [len({r['verdict'] for r in rows if r['id'] == i['id']}) == 1 for i in items]
        print(f"Same verdict on every run: {sum(same)}/{len(items)} questions")
    print("\nPer question (verdict on each run):")
    for i in items:
        vs = [r["verdict"] for r in rows if r["id"] == i["id"]]
        print(f"  {i['id']} {i['type']:7s} {' / '.join(vs)}")


def rescore(path, rejudge):
    """Re-check grounding, and optionally re-judge, the answers saved in a results file."""
    items = {i["id"]: i for i in json.loads(EVAL_SET.read_text(encoding="utf-8"))}
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    corpus = statement_corpus()
    judge = None
    if rejudge:
        from openai import OpenAI
        model = os.getenv("OPENAI_JUDGE_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        judge = build_judge(OpenAI(), model)
        print(f"Re-judging {len(rows)} answers with {model}")
    for r in rows:
        item = items[r["id"]]
        r["quotes"], r["ungrounded_quotes"] = grounding(r["answer"], corpus)
        r["missing_mentions"] = mentions(r["answer"], item.get("must_mention", []))
        if judge:
            r["judge"] = judge(item, r["answer"])
            r["verdict"] = r["judge"]["verdict"]
    out_path = path.with_name(path.stem + f"_rescored_{datetime.now():%Y%m%d_%H%M%S}.jsonl")
    with out_path.open("w", encoding="utf-8") as out:
        for r in rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    asked = [items[i] for i in dict.fromkeys(r["id"] for r in rows)]
    summarize(rows, asked, max(r["run"] for r in rows))
    print(f"\nRe-scored results: {out_path.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", help="question file (default: eval/agent_eval_set.json)")
    parser.add_argument("--runs", type=int, default=2, help="answers per question")
    parser.add_argument("--only", help="comma-separated question ids, e.g. Q01,Q11")
    parser.add_argument("--rescore", metavar="RESULTS_FILE",
                        help="re-score saved answers instead of running the agent")
    parser.add_argument("--rejudge", action="store_true",
                        help="with --rescore: also re-run the judge (uses OPENAI_JUDGE_MODEL)")
    args = parser.parse_args()
    global EVAL_SET
    if args.set:
        EVAL_SET = Path(args.set).resolve()
    if args.rescore:
        return rescore(Path(args.rescore), args.rejudge)

    from openai import OpenAI
    agent = load_agent()
    client = OpenAI()
    judge = build_judge(client, os.getenv("OPENAI_JUDGE_MODEL", agent.MODEL))
    corpus = statement_corpus()

    items = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    if args.only:
        wanted = {q.strip().upper() for q in args.only.split(",")}
        items = [i for i in items if i["id"] in wanted]

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"agent_eval_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    rows = []
    with out_path.open("w", encoding="utf-8") as out:
        for item in items:
            for run in range(1, args.runs + 1):
                print(f"{item['id']} run {run}: {item['question']}")
                answer, calls = agent.ask(item["question"], client=client)
                n_quotes, ungrounded = grounding(answer, corpus)
                missing = mentions(answer, item.get("must_mention", []))
                verdict = judge(item, answer)
                row = {"id": item["id"], "type": item["type"], "run": run,
                       "verdict": verdict["verdict"], "quotes": n_quotes,
                       "ungrounded_quotes": ungrounded, "missing_mentions": missing,
                       "judge": verdict, "tool_calls": calls, "answer": answer}
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"    -> {verdict['verdict']}; quotes {n_quotes - len(ungrounded)}/{n_quotes} grounded; "
                      f"missing mentions: {missing or 'none'}")

    summarize(rows, items, args.runs)
    print(f"\nFull answers, tool calls and judge reasons: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
