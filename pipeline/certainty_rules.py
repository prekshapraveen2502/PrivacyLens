"""
Rule-based checks of the LLM's certainty label (does / may / does_not).

Used by extract_flows.py (on new records) and postprocess_flows.py (on existing records).

Revised after reviewing the first round of flags:
  * "can" is no longer treated as hedging: it usually describes the user's choices
    ("you can opt out"), not uncertainty about the company's practice.
  * "no longer" is no longer treated as negation: "so that it no longer identifies you"
    does not deny the practice.
  * Negations written with a curly apostrophe (don’t, won’t) are now recognised.

The one automatic correction: a record labeled "does" whose sentence says may, might or
could is changed to "may". In the review every such flag was a real error. The original
LLM label is kept in `llm_certainty`, so every correction is visible and reversible.
"""
import re

HEDGE = re.compile(r"\b(may|might|could)\b", re.I)
NEGATION = re.compile(r"\b(not|never|cannot)\b|n['\u2019]t\b", re.I)


def check(sentence, certainty):
    """Return (corrected_certainty, flags) for one record."""
    flags = []
    hedged = bool(HEDGE.search(sentence))
    negated = bool(NEGATION.search(sentence))

    if certainty == "does" and hedged:
        flags.append("corrected from does to may: the sentence hedges (may/might/could)")
        certainty = "may"
    if certainty != "does_not" and negated:
        flags.append("sentence contains a negation, but not labeled does_not")
    if certainty == "does_not" and not negated:
        flags.append("labeled does_not, but no negation found")
    return certainty, flags


def apply(record):
    """Apply the rules to a record in place. Safe to run more than once."""
    original = record.get("llm_certainty", record["certainty"])
    record["llm_certainty"] = original
    record["certainty"], record["review_flags"] = check(record["source_sentence"], original)
    return record
