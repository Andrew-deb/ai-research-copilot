"""
seed_demo_collections.py — the curated collections an anonymous visitor sees.

Run once after sql/10_capabilities_and_quotas.sql:

    python seed_demo_collections.py            # create what is missing
    python seed_demo_collections.py --replace  # rebuild from scratch
    python seed_demo_collections.py --dry-run  # show the picks, change nothing

Why these three themes
----------------------
Chosen from what the corpus actually holds after the Phase 2.8 relevance pass,
not from what would sound impressive. Measured 2026-09-20 over accepted papers:

    large language model agents               24 papers,  5 with sections
    reinforcement learning from human feedback 19 papers,  7 with sections
    retrieval augmented generation            18 papers,  7 with sections
    transformer neural network                13 papers,  3 with sections
    vector database indexing                   6 papers,  1 with section

The bottom two are excluded. Six papers is not a collection, and "transformer
neural network" is the seed topic that historically dragged in flood forecasting
and protein chemistry — the relevance gate cleans new arrivals, but its older
rows are the least trustworthy in the corpus.

Why papers with sections are preferred
--------------------------------------
A paper with extracted sections demonstrates the product; one with only an
abstract demonstrates a search box. Section text is what makes RAG answers cite
findings rather than restate titles, and what puts a "from the conclusion" badge
on a result. Ordering by sections first, then citations, puts the papers that
show the most capability at the top of each collection.
"""

import argparse
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SYSTEM_EMAIL = "system@research-copilot.dev"
PAPERS_PER_COLLECTION = 8

COLLECTIONS = [
    {
        "name": "Retrieval-Augmented Generation",
        "description": "How language models ground their answers in retrieved documents — "
                       "the architecture this copilot is built on.",
        "topic": "retrieval augmented generation",
    },
    {
        "name": "Learning from Human Feedback",
        "description": "Aligning model behaviour with human preference, from reward "
                       "modelling to the critiques of it.",
        "topic": "reinforcement learning from human feedback",
    },
    {
        "name": "LLM Agents & Tool Use",
        "description": "Models that plan, call tools and act — the line of work behind "
                       "research agents like this one.",
        "topic": "large language model agents",
    },
]

# Sections first, then citations. NULLS LAST so a paper with unknown citations
# never outranks one with a known count.
SELECT_PAPERS = """
    SELECT paper_id, title, citation_count,
           (fulltext_status = 'ok') AS has_sections
      FROM papers
     WHERE relevance_topic = %(topic)s
       AND relevance_status = 'accepted'
     ORDER BY (fulltext_status = 'ok') DESC,
              citation_count DESC NULLS LAST,
              title ASC
     LIMIT %(limit)s;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true",
                        help="rebuild collections that already exist")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the selection without writing")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT user_id FROM users WHERE email = %s;", (SYSTEM_EMAIL,))
    row = cur.fetchone()
    if not row:
        print(f"No system account ({SYSTEM_EMAIL}). Run "
              f"sql/10_capabilities_and_quotas.sql first.", file=sys.stderr)
        return 1
    system_user_id = row["user_id"]

    created = skipped = 0

    for spec in COLLECTIONS:
        cur.execute(
            "SELECT collection_id FROM collections WHERE user_id = %s AND name = %s;",
            (system_user_id, spec["name"]),
        )
        existing = cur.fetchone()

        if existing and not args.replace:
            print(f"- {spec['name']}: already exists, skipping (use --replace to rebuild)")
            skipped += 1
            continue

        cur.execute(SELECT_PAPERS, {"topic": spec["topic"], "limit": PAPERS_PER_COLLECTION})
        papers = cur.fetchall()

        if not papers:
            print(f"! {spec['name']}: no accepted papers for topic "
                  f"{spec['topic']!r} — skipping rather than creating an empty collection")
            continue

        with_sections = sum(1 for p in papers if p["has_sections"])
        print(f"\n{spec['name']}  ({len(papers)} papers, {with_sections} with sections)")
        for i, paper in enumerate(papers, start=1):
            mark = "§" if paper["has_sections"] else " "
            print(f"   {i}. {mark} [{paper['citation_count'] or 0:>6}] {str(paper['title'])[:66]}")

        if args.dry_run:
            continue

        if existing:
            # Replace means replace: drop the membership rows too, or a rebuilt
            # collection keeps papers the new selection did not choose.
            cur.execute("DELETE FROM collection_papers WHERE collection_id = %s;",
                        (existing["collection_id"],))
            cur.execute("DELETE FROM collections WHERE collection_id = %s;",
                        (existing["collection_id"],))

        cur.execute(
            """
            INSERT INTO collections (user_id, name, description, is_curated)
            VALUES (%s, %s, %s, true)
            RETURNING collection_id;
            """,
            (system_user_id, spec["name"], spec["description"]),
        )
        collection_id = cur.fetchone()["collection_id"]

        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO collection_papers (collection_id, paper_id, sequence_order)
            VALUES (%s, %s, %s)
            ON CONFLICT (collection_id, paper_id) DO NOTHING;
            """,
            [(collection_id, p["paper_id"], i) for i, p in enumerate(papers, start=1)],
        )
        created += 1

    if args.dry_run:
        conn.rollback()
        print("\n[dry run] nothing written.")
    else:
        conn.commit()
        print(f"\nDone: {created} collection(s) written, {skipped} skipped.")
        print("They are read-only for every visitor, signed in or not.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
