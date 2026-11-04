"""
Rule-based checks of the extracted records, applied after the LLM.

Used by extract_flows.py (on new records) and postprocess_flows.py (on existing records).

Certainty (does / may / does_not)
  * A record labeled "does" whose sentence says the company may/might/could do something
    is corrected to "may". The LLM's original label is kept in `llm_certainty`.
  * Not corrected, only flagged, when the hedge belongs to the user ("you may contact us").
  * Hedge words are matched in lower case only, so a person's name such as "May" in an
    example does not count.
  * Other mismatches (a negation in the sentence but no "does_not", or the reverse) are
    flagged for review, not changed.

Revision history (from manual reviews of the flags)
  v1: "can" and "no longer" caused false alarms; removed. Curly-apostrophe negations added.
  v2: "you may ..." and the name "May" were wrongly corrected; now excluded.

Missed AI training (recall backstop, see flag_missed_ai_training)
  * A sentence that mentions training models, algorithms, machine learning or AI, but has
    no AI-training record, is flagged on all its records so it can be reviewed.
"""
import re

HEDGE = re.compile(r"\b(may|might|could)\b")                 # lower case only
USER_WORDS = {"you", "users", "user", "customers", "customer", "members", "member", "people"}
NEGATION = re.compile(r"\b(not|never|cannot)\b|n['\u2019]t\b", re.I)
TRAIN = re.compile(r"\btrain(s|ed|ing)?\b", re.I)
AI_TERMS = re.compile(r"\b(models?|algorithms?|machine learning|artificial intelligence)\b|\bAI\b", re.I)
AI_TRAINING = "PL-012"


def company_hedge(sentence):
    """True if some may/might/could in the sentence is not said about the user."""
    for m in HEDGE.finditer(sentence):
        before = sentence[:m.start()].split()
        if not before or before[-1].lower().strip(",;:") not in USER_WORDS:
            return True
    return False


def check(sentence, certainty):
    """Return (corrected_certainty, flags) for one record."""
    flags = []
    negated = bool(NEGATION.search(sentence))

    if certainty == "does" and HEDGE.search(sentence):
        if company_hedge(sentence):
            flags.append("corrected from does to may: the sentence hedges (may/might/could)")
            certainty = "may"
        else:
            flags.append("hedge refers to the user (e.g. 'you may'); check the label")
    if certainty != "does_not" and negated:
        flags.append("sentence contains a negation, but not labeled does_not")
    if certainty == "does_not" and not negated:
        flags.append("labeled does_not, but no negation found")
    return certainty, flags


def apply(record):
    """Apply the certainty rules to a record in place. Safe to run more than once."""
    original = record.get("llm_certainty", record["certainty"])
    record["llm_certainty"] = original
    record["certainty"], record["review_flags"] = check(record["source_sentence"], original)
    return record


def flag_missed_ai_training(records):
    """Flag records whose sentence looks like AI training but has no AI-training record."""
    by_sentence = {}
    for r in records:
        by_sentence.setdefault(r["source_sentence"], []).append(r)
    flagged = 0
    for sentence, group in by_sentence.items():
        looks_like_training = TRAIN.search(sentence) and AI_TERMS.search(sentence)
        if looks_like_training and not any(r.get("purpose") == AI_TRAINING for r in group):
            for r in group:
                r["review_flags"].append("possible missed AI-training purpose")
            flagged += 1
    return flagged
