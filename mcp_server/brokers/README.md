# mcp_server/brokers/ — External API Clients

Three broker modules, each owning exactly one external API. No broker may touch the database, call another broker, or import Flask or MCP.

## API Selection Rationale

We integrate three APIs serving distinct, complementary roles. This is a deliberate multi-source strategy — no single API provides everything we need.

### OpenAlex — Primary Discovery Layer

**Why OpenAlex?**

| Criterion | OpenAlex | PubMed | Crossref | Lens.org |
|-----------|----------|--------|----------|---------|
| Coverage | 250M+ works, all disciplines | Biomedical only | 150M+, metadata only | 225M+ works |
| Abstracts | ✅ (inverted index) | ✅ | ❌ | ✅ |
| Open access info | ✅ | ❌ | ❌ | Partial |
| Institutional data | ✅ (ROR) | ❌ | ❌ | ❌ |
| API key required | ❌ (email only) | Optional | ❌ | ❌ |
| Rate limit | 10 req/sec (polite pool) | 10 req/sec | 50 req/sec | 10 req/sec |
| Citation graph | ✅ | Limited | Limited | ✅ |
| Cost | Free forever | Free | Free | Free (basic) |

OpenAlex is the clear choice: broadest cross-disciplinary coverage, has abstracts (critical for our embedding pipeline), provides institutional affiliation and open-access URLs, requires no API key registration, and is backed by a non-profit (no risk of paywalling).

**Role in our system:** Search, browse, citation lookup, author profiles.

---

### Semantic Scholar — Enrichment Layer

**Why Semantic Scholar?**

S2 is built by the Allen Institute for AI specifically for AI-powered academic search. Features unique to S2 that no other free API provides:

- **AI-generated TLDRs** — One-sentence summaries produced by a fine-tuned model. Irreplaceable for rapid paper scanning in our agent's reading plan tools.
- **Influential citation count** — Weighted citation metric (citations from high-impact papers count more). Lets the agent prioritise foundational papers over papers with inflated citation counts from survey papers.
- **Recommendations engine** — Uses S2's own learned paper embeddings (separate from our pgvector embeddings) to surface "more like this" papers. No other free API provides a comparable recommendation endpoint.
- **Multiple ID formats** — Accepts DOI, ArXiv, MAG, and S2 IDs interchangeably, making cross-source ID resolution trivial.

**Why not use S2 for discovery too?**
S2's search is good, but its rate limit is 1 req/sec authenticated (vs OpenAlex's 10 req/sec). For initial broad discovery, OpenAlex is 10x faster. We use S2 only for targeted enrichment after OpenAlex has found the papers.

**Role in our system:** TLDRs, influence scores, "more like this" recommendations, citation context.

---

### Wikipedia — Prerequisite Context Layer

**Why Wikipedia REST API (and not an LLM or search engine)?**

| Approach | Pros | Cons |
|----------|------|------|
| LLM knowledge (GPT, etc.) | Always available, no API call | Not citable, may hallucinate, changes between model versions |
| Google/Bing Search API | Comprehensive, current | Paid, requires key, results are inconsistent |
| Wikipedia REST API | Free, stable, citable, consistent structure | English only, only covers notable topics |

Wikipedia's REST summary endpoint returns structured JSON with a clean `extract` field — the first plain-language paragraph of the article. This gives us stable, citable prerequisite definitions that don't change month-to-month (unlike LLM knowledge) and require no API key or budget.

**Why REST API v1, not the Action API?**
The REST API (`/api/rest_v1/page/summary/{title}`) returns structured JSON with a clean `extract` field in a single request. The Action API (`/w/api.php?action=query&prop=extracts`) requires additional parameter configuration, returns raw HTML that needs parsing, and is generally intended for more complex wiki operations. For simple summary fetches, REST v1 is simpler and more predictable.

**Role in our system:** One-time topic explanation cache (stored in `topic_context` table), sourced on first `explain_topic` call and reused thereafter.

---

## Key Design Decisions

**`select=` field filtering (OpenAlex)** — Every OpenAlex request includes a `select=` parameter listing only the fields we actually use. OpenAlex Work objects can be very large (nested topics, concepts, counts by year, etc.). By selecting only what we need, we reduce payload size by ~70%, keeping the broker fast and reducing bandwidth costs on Databricks.

**Rate limit delay per broker** — Each broker sleeps before every request (`time.sleep(delay)`). The delay is configurable via `.env` so it can be adjusted without code changes (e.g., if S2 key is missing, set `S2_RATE_LIMIT_DELAY=3.1`). The delay is placed at the start of each `_get()` call rather than the end, ensuring the gap between consecutive calls is always respected regardless of how long the request itself takes.

**All HTTP errors bubble up unhandled** — Brokers call `resp.raise_for_status()` and only catch 404s (returning `None`). All other HTTP errors (`429 Too Many Requests`, `500 Server Error`, etc.) are re-raised so the service layer and middleware can decide how to respond. This keeps error-handling policy out of the broker and in the right layer.

**`payload` field in standardized dict** — Every standardized dict includes `"payload": raw_api_response`. This raw response is stored in the `papers.payload` JSONB column for auditability. If we need a new field from the API that we didn't originally promote to a top-level column, we can query it from `payload` without re-fetching from the API.
