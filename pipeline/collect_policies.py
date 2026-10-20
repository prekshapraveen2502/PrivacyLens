"""
PrivacyLens Step 1: collect privacy policies listed in data/manifest.csv.

Setup:   pip install trafilatura
Usage:   1. Paste each policy's URL into the `url` column of data/manifest.csv
         2. python pipeline/collect_policies.py

For every row with a URL that hasn't been collected yet, the script downloads the
page, extracts the main text, and saves it to data/policies/ with a metadata header.
It then fills in collected_on, word_count and status in the manifest.

Rows marked status = check_manually returned too little text (usually pages that
load their content with JavaScript). For those, open the page in a browser, copy
the policy text, paste it into the file the script names, and set status to ok.
"""
import csv
from datetime import date
from pathlib import Path

import trafilatura

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "manifest.csv"
OUT_DIR = ROOT / "data" / "policies"
MIN_WORDS = 1500  # real privacy policies are almost always longer than this


def file_name(row):
    return f"{row['service_id']}_{row['document_type']}_{row['region'].lower()}.txt"


def collect(row):
    downloaded = trafilatura.fetch_url(row["url"])
    if not downloaded:
        return None
    return trafilatura.extract(downloaded, include_comments=False, include_tables=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    today = date.today().isoformat()
    for row in rows:
        if not row["url"] or row["status"] == "ok":
            continue
        path = OUT_DIR / file_name(row)
        print(f"Collecting {row['service']} ({row['region']}) ...")
        try:
            text = collect(row)
        except Exception as e:
            text = None
            print(f"  error: {e}")

        words = len(text.split()) if text else 0
        header = (
            f"SERVICE: {row['service']}\n"
            f"DOCUMENT: {row['document_type']}\n"
            f"REGION: {row['region']}\n"
            f"URL: {row['url']}\n"
            f"COLLECTED: {today}\n"
            f"{'=' * 60}\n\n"
        )
        path.write_text(header + (text or ""), encoding="utf-8")

        row["collected_on"] = today
        row["word_count"] = str(words)
        row["status"] = "ok" if words >= MIN_WORDS else "check_manually"
        print(f"  {words} words -> {path.name} [{row['status']}]")

    with MANIFEST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    pending = [r for r in rows if r["status"] != "ok"]
    print(f"\nDone. {len(rows) - len(pending)} of {len(rows)} documents ready.")
    for r in pending:
        reason = "no URL yet" if not r["url"] else "paste the text manually"
        print(f"  - {r['service']} ({r['region']}): {reason}")


if __name__ == "__main__":
    main()
