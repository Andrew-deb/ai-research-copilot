"""
setup_db.py — One-time Lakebase schema initializer and data seeder.

Runs all SQL files in sql/ in order, seeds demo data, and verifies the result.
Run once before starting the application. Safe to re-run — seeding is skipped
if data already exists.
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ DATABASE_URL not found. Copy .env.example → .env and fill it in.")
    sys.exit(1)

SQL_DIR = Path(__file__).parent / "sql"


def read_sql(filename: str) -> str:
    filepath = SQL_DIR / filename
    if not filepath.exists():
        print(f"❌ SQL file not found: {filepath}")
        sys.exit(1)
    return filepath.read_text(encoding="utf-8")


print("🔌 Connecting to Lakebase...")

try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    print("✅ Connected\n")

    # --- Schema ---
    print("📋 Creating schema...")
    for filename, label in [
        ("01_create_tables.sql", "Core tables"),
        ("02_create_embedding_tables.sql", "Embedding tables + HNSW indexes"),
        ("03_create_trace_table.sql", "Trace table"),
    ]:
        cur.execute(read_sql(filename))
        print(f"  ✅ {label}")

    # --- Seed (idempotent) ---
    cur.execute("SELECT COUNT(*) FROM users;")
    if cur.fetchone()[0] > 0:
        print("\n⚠️  Existing data found — skipping seed.")
    else:
        print("\n📝 Seeding demo data...")

        cur.execute(
            "INSERT INTO users (email, display_name) VALUES (%s, %s) RETURNING user_id;",
            ("demo@research-copilot.dev", "Demo Researcher"),
        )
        user_id = cur.fetchone()[0]

        goals = [
            ("Understand Transformer Architectures",
             "Learn transformer fundamentals from the original paper through BERT and GPT."),
            ("Explore Reinforcement Learning",
             "Build intuition for RL from MDPs through deep RL algorithms."),
            ("Survey Retrieval-Augmented Generation",
             "Understand how RAG systems combine retrieval with generation."),
        ]
        for title, desc in goals:
            cur.execute(
                "INSERT INTO learning_goals (user_id, title, description) VALUES (%s, %s, %s);",
                (user_id, title, desc),
            )

        papers = [
            ("Attention Is All You Need", "10.48550/arXiv.1706.03762", 2017, "NeurIPS 2017", 120000,
             "The dominant sequence transduction models are based on complex recurrent or "
             "convolutional neural networks that include an encoder and a decoder. We propose "
             "a new simple network architecture, the Transformer, based solely on attention mechanisms."),
            ("BERT: Pre-training of Deep Bidirectional Transformers", "10.48550/arXiv.1810.04805", 2019, "NAACL 2019", 85000,
             "We introduce BERT, which stands for Bidirectional Encoder Representations from "
             "Transformers, designed to pre-train deep bidirectional representations from "
             "unlabeled text by jointly conditioning on both left and right context."),
            ("Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", "10.48550/arXiv.2005.11401", 2020, "NeurIPS 2020", 4500,
             "We explore a general-purpose fine-tuning recipe for retrieval-augmented generation, "
             "combining parametric and non-parametric memory for language generation tasks."),
        ]
        paper_ids = []
        for title, doi, year, venue, cites, abstract in papers:
            cur.execute(
                "INSERT INTO papers (title, doi, publication_year, venue, citation_count, abstract, source_api) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'openalex') RETURNING paper_id;",
                (title, doi, year, venue, cites, abstract),
            )
            paper_ids.append(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO collections (user_id, name, description) VALUES (%s, %s, %s) RETURNING collection_id;",
            (user_id, "Transformer Foundations", "Core transformer papers in reading order."),
        )
        col_id = cur.fetchone()[0]
        for seq, pid in enumerate(paper_ids[:2]):
            cur.execute(
                "INSERT INTO collection_papers (collection_id, paper_id, sequence_order) VALUES (%s, %s, %s);",
                (col_id, pid, seq),
            )

        cur.execute(
            "INSERT INTO reading_progress (user_id, paper_id, status) VALUES (%s, %s, 'completed'), (%s, %s, 'reading');",
            (user_id, paper_ids[0], user_id, paper_ids[1]),
        )

        cur.execute(
            "INSERT INTO notes (user_id, paper_id, note_text) VALUES (%s, %s, %s);",
            (user_id, paper_ids[0],
             "Key insight: self-attention lets every position attend to every other. "
             "Multi-head variant allows attending to different representation subspaces simultaneously."),
        )

        cur.execute(
            "INSERT INTO topic_context (topic_name, wikipedia_summary, wiki_url) VALUES (%s, %s, %s);",
            ("Transformer (deep learning architecture)",
             "A transformer is a deep learning architecture relying on the parallel multi-head "
             "attention mechanism, dominant in NLP and computer vision since 2017.",
             "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)"),
        )
        print("  ✅ Demo user, 3 goals, 3 papers, 1 collection, progress, 1 note, 1 topic")

    # --- Verify ---
    print("\n📊 Row counts:")
    for table in ["users", "learning_goals", "papers", "collections",
                  "reading_progress", "notes", "topic_context",
                  "paper_embeddings", "note_embeddings", "mcp_traces"]:
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        print(f"  {table:<25} {cur.fetchone()[0]:>6}")

    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    row = cur.fetchone()
    print(f"\n  pgvector: {'✅ ' + row[0] if row else '⚠️  not found'}")

    cur.close()
    conn.close()
    print("\n🎉 Setup complete!")

except psycopg2.OperationalError as e:
    print(f"❌ Connection failed: {e}")
except psycopg2.Error as e:
    print(f"❌ Database error: {e}")
