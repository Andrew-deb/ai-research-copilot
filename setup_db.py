"""
setup_db.py — One-time Database Setup Script

PURPOSE:
    Connects to the remote Lakebase database and:
    1. Runs all SQL schema files (tables, pgvector, traces) in order
    2. Inserts sample seed data for development and demo purposes
    3. Verifies everything worked by querying the data back

    You only need to run this ONCE to initialize the database.
    After that, the app and Spark pipeline handle all reads/writes.

HOW IT WORKS:
    .env file → load_dotenv() → os.getenv("DATABASE_URL") → psycopg2.connect()
    Then we send SQL commands over the internet to Lakebase.

DESIGN NOTES:
    This script follows the same pattern established in Day 1's setup_db.py.
    SQL lives in separate .sql files (not inline) for readability and version
    control — this script is just the runner.

USAGE:
    python setup_db.py
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


# =============================================
# STEP 1: Load the connection string from .env
# =============================================
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL not found in .env file")
    print("   Copy .env.example to .env and fill in your Lakebase connection string.")
    sys.exit(1)

# Resolve the path to the sql/ directory relative to this script,
# not the working directory. This way setup_db.py works regardless
# of where it's invoked from.
SQL_DIR = Path(__file__).parent / "sql"


def read_sql_file(filename: str) -> str:
    """Read a SQL file from the sql/ directory."""
    filepath = SQL_DIR / filename
    if not filepath.exists():
        print(f"❌ ERROR: SQL file not found: {filepath}")
        sys.exit(1)
    return filepath.read_text(encoding="utf-8")


def run_sql(cur, sql: str, description: str) -> None:
    """Execute a SQL string and print status."""
    print(f"  ⏳ {description}...")
    cur.execute(sql)
    print(f"  ✅ {description} — done")


print("🔌 Connecting to Lakebase...")

try:
    # =============================================
    # STEP 2: Connect to Lakebase
    # =============================================
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    print("✅ Connected to Lakebase successfully!\n")

    # =============================================
    # STEP 3: Run schema SQL files in order
    # =============================================
    # Order matters: 01 creates base tables that 02 references (via FK),
    # and 03 is independent but logically comes last.
    print("📋 Creating database schema...")
    print("=" * 60)

    schema_files = [
        ("01_create_tables.sql", "Creating core relational tables (10 tables)"),
        ("02_create_embedding_tables.sql", "Creating pgvector embedding tables + HNSW indexes"),
        ("03_create_trace_table.sql", "Creating MCP trace table"),
    ]

    for filename, description in schema_files:
        sql = read_sql_file(filename)
        run_sql(cur, sql, description)
        print()

    print("✅ All schema files executed successfully!\n")

    # =============================================
    # STEP 4: Check if sample data already exists
    # =============================================
    cur.execute("SELECT COUNT(*) FROM users;")
    user_count = cur.fetchone()[0]

    if user_count > 0:
        print(f"⚠️  Found {user_count} existing user(s). Skipping sample data insertion.")
        print("   (Delete existing data first if you want to re-seed.)\n")
    else:
        # =============================================
        # STEP 5: Insert sample seed data
        # =============================================
        print("📝 Inserting sample seed data...")
        print("-" * 60)

        # --- Seed user ---
        cur.execute("""
            INSERT INTO users (email, display_name)
            VALUES ('demo@research-copilot.dev', 'Demo Researcher')
            RETURNING user_id;
        """)
        demo_user_id = cur.fetchone()[0]
        print(f"  ✅ Created demo user: demo@research-copilot.dev (ID: {demo_user_id})")

        # --- Seed learning goals ---
        goals = [
            ("Understand Transformer Architectures",
             "Learn the fundamentals of transformer models, starting from "
             "the original 'Attention Is All You Need' paper through modern "
             "variants like BERT, GPT, and Vision Transformers."),
            ("Explore Reinforcement Learning from Scratch",
             "Build intuition for RL from Markov Decision Processes through "
             "policy gradient methods and modern deep RL algorithms."),
            ("Survey Retrieval-Augmented Generation (RAG)",
             "Understand how RAG systems combine retrieval with generation, "
             "including vector databases, embedding models, and evaluation."),
        ]

        goal_ids = []
        for title, description in goals:
            cur.execute(
                "INSERT INTO learning_goals (user_id, title, description) "
                "VALUES (%s, %s, %s) RETURNING goal_id;",
                (demo_user_id, title, description)
            )
            goal_id = cur.fetchone()[0]
            goal_ids.append(goal_id)
            print(f"  ✅ Created goal: '{title[:50]}...'")

        # --- Seed sample papers ---
        papers = [
            {
                "title": "Attention Is All You Need",
                "abstract": "The dominant sequence transduction models are based on complex "
                            "recurrent or convolutional neural networks that include an encoder "
                            "and a decoder. The best performing models also connect the encoder "
                            "and decoder through an attention mechanism. We propose a new simple "
                            "network architecture, the Transformer, based solely on attention "
                            "mechanisms, dispensing with recurrence and convolutions entirely.",
                "doi": "10.48550/arXiv.1706.03762",
                "publication_year": 2017,
                "venue": "NeurIPS 2017",
                "citation_count": 120000,
                "source_api": "openalex",
            },
            {
                "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
                "abstract": "We introduce a new language representation model called BERT, which "
                            "stands for Bidirectional Encoder Representations from Transformers. "
                            "BERT is designed to pre-train deep bidirectional representations from "
                            "unlabeled text by jointly conditioning on both left and right context.",
                "doi": "10.48550/arXiv.1810.04805",
                "publication_year": 2019,
                "venue": "NAACL 2019",
                "citation_count": 85000,
                "source_api": "openalex",
            },
            {
                "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                "abstract": "Large pre-trained language models have been shown to store factual "
                            "knowledge in their parameters, and achieve state-of-the-art results "
                            "when fine-tuned on downstream NLP tasks. However, their ability to "
                            "access and precisely manipulate knowledge is still limited. We explore "
                            "a general-purpose fine-tuning recipe for retrieval-augmented generation.",
                "doi": "10.48550/arXiv.2005.11401",
                "publication_year": 2020,
                "venue": "NeurIPS 2020",
                "citation_count": 4500,
                "source_api": "openalex",
            },
        ]

        paper_ids = []
        for p in papers:
            cur.execute(
                """
                INSERT INTO papers (title, abstract, doi, publication_year, venue,
                                    citation_count, source_api)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING paper_id;
                """,
                (p["title"], p["abstract"], p["doi"], p["publication_year"],
                 p["venue"], p["citation_count"], p["source_api"])
            )
            paper_id = cur.fetchone()[0]
            paper_ids.append(paper_id)
            print(f"  ✅ Created paper: '{p['title'][:50]}...'")

        # --- Seed a collection ---
        cur.execute(
            "INSERT INTO collections (user_id, name, description) "
            "VALUES (%s, %s, %s) RETURNING collection_id;",
            (demo_user_id, "Transformer Foundations",
             "Core papers for understanding the transformer architecture and its variants.")
        )
        collection_id = cur.fetchone()[0]
        print(f"  ✅ Created collection: 'Transformer Foundations'")

        # Add papers to the collection with sequence order
        for seq, pid in enumerate(paper_ids[:2]):
            cur.execute(
                "INSERT INTO collection_papers (collection_id, paper_id, sequence_order) "
                "VALUES (%s, %s, %s);",
                (collection_id, pid, seq)
            )
        print(f"  ✅ Added 2 papers to collection")

        # --- Seed reading progress ---
        cur.execute(
            "INSERT INTO reading_progress (user_id, paper_id, status) VALUES (%s, %s, %s);",
            (demo_user_id, paper_ids[0], "completed")
        )
        cur.execute(
            "INSERT INTO reading_progress (user_id, paper_id, status) VALUES (%s, %s, %s);",
            (demo_user_id, paper_ids[1], "reading")
        )
        print(f"  ✅ Set reading progress: 1 completed, 1 reading")

        # --- Seed a note ---
        cur.execute(
            "INSERT INTO notes (user_id, paper_id, note_text) VALUES (%s, %s, %s);",
            (demo_user_id, paper_ids[0],
             "The key insight is the self-attention mechanism which allows every position "
             "in a sequence to attend to every other position. The multi-head variant lets "
             "the model jointly attend to information from different representation subspaces.")
        )
        print(f"  ✅ Created 1 sample note")

        # --- Seed topic context ---
        cur.execute(
            """
            INSERT INTO topic_context (topic_name, wikipedia_summary, wiki_url)
            VALUES (%s, %s, %s);
            """,
            ("Transformer (deep learning architecture)",
             "A transformer is a deep learning architecture that relies on the parallel "
             "multi-head attention mechanism. It has been the dominant architecture for "
             "natural language processing and computer vision since 2017.",
             "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)")
        )
        print(f"  ✅ Created 1 topic context entry")

        print()

    # =============================================
    # STEP 6: Verify — Query the data back
    # =============================================
    print("=" * 60)
    print("🔍 VERIFICATION — Querying data from Lakebase")
    print("=" * 60)

    verification_queries = [
        ("users", "SELECT COUNT(*) FROM users"),
        ("learning_goals", "SELECT COUNT(*) FROM learning_goals"),
        ("papers", "SELECT COUNT(*) FROM papers"),
        ("authors", "SELECT COUNT(*) FROM authors"),
        ("collections", "SELECT COUNT(*) FROM collections"),
        ("collection_papers", "SELECT COUNT(*) FROM collection_papers"),
        ("reading_progress", "SELECT COUNT(*) FROM reading_progress"),
        ("notes", "SELECT COUNT(*) FROM notes"),
        ("topic_context", "SELECT COUNT(*) FROM topic_context"),
        ("paper_embeddings", "SELECT COUNT(*) FROM paper_embeddings"),
        ("note_embeddings", "SELECT COUNT(*) FROM note_embeddings"),
        ("mcp_traces", "SELECT COUNT(*) FROM mcp_traces"),
    ]

    print(f"\n{'Table':<25} {'Row Count':>10}")
    print("-" * 37)
    for table_name, query in verification_queries:
        cur.execute(query)
        count = cur.fetchone()[0]
        print(f"  {table_name:<23} {count:>10}")

    # =============================================
    # STEP 7: Verify pgvector extension
    # =============================================
    print(f"\n🔍 Checking pgvector extension...")
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    result = cur.fetchone()
    if result:
        print(f"  ✅ pgvector version: {result[0]}")
    else:
        print("  ⚠️  pgvector extension not found (embeddings will fail)")

    # =============================================
    # STEP 8: Clean up
    # =============================================
    cur.close()
    conn.close()

    print()
    print("=" * 60)
    print("🎉 DATABASE SETUP COMPLETE!")
    print("=" * 60)
    print("  ✅ All 12 tables created")
    print("  ✅ HNSW vector indexes created")
    print("  ✅ Sample data seeded")
    print("  ✅ pgvector extension verified")
    print("  ✅ Connection closed")
    print("\n  You're ready to build the app! 🚀")

except psycopg2.OperationalError as e:
    print(f"❌ CONNECTION ERROR: Could not connect to Lakebase.")
    print(f"   Details: {e}")
    print("\n   Check that:")
    print("   1. Your DATABASE_URL in .env is correct")
    print("   2. You have internet access")
    print("   3. The Lakebase server is running")

except psycopg2.Error as e:
    print(f"❌ DATABASE ERROR: {e}")
    print("\n   The SQL command failed. Check the error above for details.")
