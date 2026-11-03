"""
PrivacyLens Step 4: extract data-flow records from policy texts with an LLM.

For each policy, the LLM reads the text in chunks and returns structured records
(who collects/shares/sells/uses what data, for what purpose, with whom). It may only
use concept IDs from the controlled vocabulary. Every record must quote its source
sentence word for word; records whose sentence is not in the policy are rejected.

Setup:   pip install openai pydantic python-dotenv
         .env needs OPENAI_API_KEY (and optionally OPENAI_MODEL)

Usage:
    python pipeline/extract_flows.py --doc uber_supplemental_notice_us.txt --max-chunks 1   # quick test
    python pipeline/extract_flows.py --doc uber_supplemental_notice_us.txt                  # one document
    python pipeline/extract_flows.py --all                                                  # whole corpus
    add --force to re-extract a document that already has output

Output (in data/extracted/):
    <doc>.jsonl            accepted records
    <doc>.rejected.jsonl   records that failed the quote check, with the reason
    runs.csv               one line per run: model, tokens, record counts
"""
import argparse
import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "data" / "policies"
OUT_DIR = ROOT / "data" / "extracted"
VOCAB_CSV = ROOT / "ontology" / "controlled_vocabulary.csv"
HEADER_DIVIDER = "=" * 60
CHUNK_WORDS = 900          # policy text per LLM call
DEFAULT_MODEL = "gpt-5.4-mini"

# Which vocabulary facets the extractor may use, and for which field.
FIELD_FACETS = {"action": "action", "data_type": "data type",
                "purpose": "purpose", "recipient_category": "recipient"}


# ---------------------------------------------------------------- vocabulary

def load_vocabulary():
    """General-context concepts per facet: {facet: [(id, label, definition, scope, alts)]}."""
    with VOCAB_CSV.open(newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    vocab = {facet: [] for facet in FIELD_FACETS.values()}
    for r in rows:
        if r.get("context", "general") == "general" and r["facet"] in vocab and r["status"] == "active":
            vocab[r["facet"]].append((r["id"], r["preferred_label"], r["definition"],
                                      r["scope_note"], r["alternative_labels"]))
    return vocab


def vocabulary_prompt(vocab):
    lines = []
    for field, facet in FIELD_FACETS.items():
        lines.append(f"\n## Allowed values for {field} ({facet})")
        for cid, label, definition, scope, alts in vocab[facet]:
            line = f"- {cid} = {label}: {definition}"
            if alts:
                line += f" Also called: {alts}."
            if scope:
                line += f" Note: {scope}"
            lines.append(line)
    return "\n".join(lines)


SYSTEM_PROMPT = """You extract data practices from privacy policies into structured records.

A record describes ONE statement about what the company does with personal data:
an action (collect, share, sell, use), a data type, and where stated a purpose and a recipient.
Create one record per combination (for example, a sentence saying location is used for
advertising and for research gives two records).

Rules:
1. source_sentence must be copied EXACTLY from the text, character for character, as one
   complete sentence (or one complete bullet point). Do not shorten, fix, or paraphrase it.
2. Use ONLY the concept IDs listed below. If the data type is personal but fits no narrower
   concept, use the general personal data concept. If nothing fits, skip the statement.
3. data_phrase is the policy's own wording for the data (for example "GPS coordinates").
4. certainty: "does" for definite statements, "may" for possible ones ("may", "can",
   "from time to time"), "does_not" for denials ("we do not sell").
5. Purpose = AI model training ONLY when the sentence mentions models, machine learning or
   artificial intelligence. "Improve our services" alone is not AI training.
6. opt_out: "yes" if the text says the person can opt out of or turn off this specific
   practice, "no" if it says they cannot, otherwise "not_stated".
7. excluded_regions: regions where the text says this practice does NOT happen (for example
   a note that marked practices are not performed in the EEA, UK or Switzerland). Else [].
8. recipient_name: a named company if the text names one, else null.
9. Ignore text that is not about the company's own data practices (headings, contact
   details, definitions with no practice). Returning no records is fine.
10. Do not create records for descriptions of the person's rights or how to exercise them
   ("you may request...", "you have the right to..."). Only extract what the company itself
   does with data.
"""


# ---------------------------------------------------------------- text handling

def read_policy(path):
    """Return the policy text without the metadata header added at collection time."""
    text = path.read_text(encoding="utf-8")
    if HEADER_DIVIDER in text:
        text = text.split(HEADER_DIVIDER, 1)[1]
    return text.strip()


def chunk_text(text, max_words=CHUNK_WORDS):
    """Split policy text into chunks of about max_words.

    Works whether the text uses blank lines or single line breaks between
    paragraphs; a paragraph longer than max_words is split at sentence ends.
    """
    pieces = []
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        if len(line.split()) <= max_words:
            pieces.append(line)
        else:
            pieces.extend(s for s in re.split(r"(?<=[.!?;])\s+", line) if s)
    chunks, current, count = [], [], 0
    for p in pieces:
        words = len(p.split())
        if current and count + words > max_words:
            chunks.append("\n".join(current))
            current, count = [], 0
        current.append(p)
        count += words
    if current:
        chunks.append("\n".join(current))
    return chunks


QUOTE_MAP = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                           "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def normalise(s):
    """Same text, ignoring differences in whitespace, curly quotes and dash style."""
    return re.sub(r"\s+", " ", s.translate(QUOTE_MAP)).strip()


def quote_check(sentence, source_text):
    """Return None if the sentence is in the source, otherwise the reason it failed.

    Ignores differences in whitespace, curly quotes, dash style, a leading bullet
    marker and trailing punctuation; every word must still match exactly.
    """
    if not sentence or len(sentence.split()) < 3:
        return "source sentence missing or too short"
    core = normalise(sentence).lstrip("-*\u2022\u00b7\u25aa ").rstrip(" .;:,")
    if core not in normalise(source_text):
        return "source sentence not found in policy text"
    return None


# ---------------------------------------------------------------- LLM call

def build_schema(vocab):
    from enum import Enum
    from typing import List, Literal, Optional
    from pydantic import BaseModel

    def enum_for(name, facet):
        return Enum(name, {cid.replace("-", "_"): cid for cid, *_ in vocab[facet]}, type=str)

    ActionId = enum_for("ActionId", "action")
    DataTypeId = enum_for("DataTypeId", "data type")
    PurposeId = enum_for("PurposeId", "purpose")
    RecipientId = enum_for("RecipientId", "recipient")

    class FlowRecord(BaseModel):
        action: ActionId
        data_type: DataTypeId
        data_phrase: str
        purpose: Optional[PurposeId]
        recipient_category: Optional[RecipientId]
        recipient_name: Optional[str]
        certainty: Literal["does", "may", "does_not"]
        opt_out: Literal["yes", "no", "not_stated"]
        excluded_regions: List[str]
        source_sentence: str

    class ExtractionResult(BaseModel):
        flows: List[FlowRecord]

    return ExtractionResult


def call_llm(client, model, schema, system, chunk, retries=2):
    for attempt in range(retries + 1):
        try:
            response = client.responses.parse(
                model=model,
                input=[{"role": "system", "content": system},
                       {"role": "user", "content": f"Policy text:\n\n{chunk}"}],
                text_format=schema,
            )
            usage = response.usage
            return response.output_parsed, usage.input_tokens, usage.output_tokens
        except Exception as e:
            if attempt == retries:
                raise
            wait = 5 * (attempt + 1)
            print(f"    API error ({type(e).__name__}: {e}); retrying in {wait}s")
            time.sleep(wait)


# ---------------------------------------------------------------- main

def extract_document(client, model, schema, system, path, max_chunks):
    text = read_policy(path)
    chunks = chunk_text(text)
    if max_chunks:
        chunks = chunks[:max_chunks]

    accepted, rejected = [], []
    tokens_in = tokens_out = 0
    for i, chunk in enumerate(chunks, 1):
        print(f"  chunk {i}/{len(chunks)} ({len(chunk.split())} words)")
        result, t_in, t_out = call_llm(client, model, schema, system, chunk)
        tokens_in += t_in
        tokens_out += t_out
        for flow in (result.flows if result else []):
            record = flow.model_dump(mode="json")
            record.update({"document": path.name, "chunk": i})
            reason = quote_check(record["source_sentence"], chunk)
            if reason:
                record["rejected_because"] = reason
                rejected.append(record)
            else:
                accepted.append(record)
    return accepted, rejected, tokens_in, tokens_out, len(chunks)


def write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def log_run(row):
    log = OUT_DIR / "runs.csv"
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doc", help="file name in data/policies/")
    group.add_argument("--all", action="store_true", help="every policy in data/policies/")
    parser.add_argument("--max-chunks", type=int, default=0, help="limit chunks per document (testing)")
    parser.add_argument("--force", action="store_true", help="re-extract documents that already have output")
    args = parser.parse_args()

    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    client = OpenAI()

    vocab = load_vocabulary()
    schema = build_schema(vocab)
    system = SYSTEM_PROMPT + vocabulary_prompt(vocab)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    paths = [POLICY_DIR / args.doc] if args.doc else sorted(POLICY_DIR.glob("*.txt"))
    for path in paths:
        if not path.exists():
            print(f"Not found: {path}")
            continue
        out = OUT_DIR / f"{path.stem}.jsonl"
        if out.exists() and not args.force and not args.max_chunks:
            print(f"Skipping {path.name} (already extracted; use --force to redo)")
            continue

        print(f"Extracting {path.name} with {model} ...")
        accepted, rejected, t_in, t_out, n_chunks = extract_document(
            client, model, schema, system, path, args.max_chunks)
        write_jsonl(out, accepted)
        write_jsonl(OUT_DIR / f"{path.stem}.rejected.jsonl", rejected)
        log_run({"timestamp": datetime.now().isoformat(timespec="seconds"), "document": path.name,
                 "model": model, "chunks": n_chunks, "accepted": len(accepted),
                 "rejected": len(rejected), "input_tokens": t_in, "output_tokens": t_out})
        print(f"  {len(accepted)} accepted, {len(rejected)} rejected by quote check; "
              f"tokens in/out: {t_in}/{t_out}")


if __name__ == "__main__":
    main()
