# Incident Retrieval — pgvector + LangChain PGVector

How the RDS agent finds similar past incidents before diagnosing a new alarm
(Phase 3 of the README roadmap), and how the two `langchain_pg_*` tables this
introduces actually work. Read this if you're touching
`app/agents/domains/rds/nodes/retrieve_similar_incidents.py`,
`app/services/incident_service.py`, or `config/vectorstore.py`, or if you're
just wondering where those two extra tables in the database came from.

## Why LangChain's `PGVector`, not a hand-rolled query

The first version of this (`find_similar_incidents` in
`app/services/incident_service.py`) was a plain SQLAlchemy query ordering by
`Incident.summary_embedding.cosine_distance(...)` — top-k nearest neighbors,
nothing else. That has a real failure mode: if 3 past incidents are all
near-duplicates of the same recurring alert, plain top-k happily returns all
3 — redundant context, no diversity.

We switched to LangChain's `PGVector` vectorstore with
`as_retriever(search_type="mmr", ...)`, which implements **Maximal Marginal
Relevance** directly (`max_marginal_relevance_search_by_vector` in
`langchain_postgres`, real candidate embeddings, not a naive approximation) —
so results stay relevant to the new alarm but diverse from each other,
without us having to implement MMR by hand.

## Two data stores, not one

**`incidents`** (this project's own table, unchanged) stays the source of
truth for incident records — `title`, `description`, `risk_tier`, `status`,
timestamps, FK to `raw_events`. Nothing about this changed.

**`langchain_pg_collection` + `langchain_pg_embedding`** are a *second*,
LangChain-owned index that exists purely to make similarity/MMR search fast.
`PGVector` always manages its own tables with a fixed schema — it can't be
pointed at an arbitrary existing table/column like `incidents.summary_embedding`
was. (That column still exists on `Incident` but is **no longer written** —
it's legacy/unpopulated going forward, not dropped, since dropping it is a
separate, more reversible decision than swapping the query mechanism.)

Both tables are created automatically by `PGVector` itself on first use
(idempotent `CREATE TABLE IF NOT EXISTS`) — **not** through an Alembic
migration. That's a deliberate departure from this project's usual
migration-controlled schema; it's simply how this library manages its own
tables.

## The two tables

### `langchain_pg_collection`

One row per named collection — a logical namespace, in case this schema ever
hosts more than one independent vector index. This project uses exactly one:
`name = "incidents"` (set via `collection_name="incidents"` in
`config/vectorstore.py`). Effectively inert after first creation.

```
 Column   |       Type        | Nullable
----------+--------------------+----------
 uuid     | uuid               | not null   (PK)
 name     | character varying  | not null   (unique)
 cmetadata| json               |
```

### `langchain_pg_embedding`

The actual data — one row per embedded incident.

```
 Column       |       Type        | Nullable
--------------+--------------------+----------
 id           | character varying | not null   (PK)
 collection_id| uuid               |            (FK -> langchain_pg_collection.uuid, ON DELETE CASCADE)
 embedding    | vector(1536)       |
 document     | character varying |
 cmetadata    | jsonb              |            (GIN index, jsonb_path_ops)
```

| column | what it holds here | set by |
|---|---|---|
| `id` | `str(incident.id)` — matches `incidents.id` as text | `ids=[...]` passed to `add_texts` |
| `collection_id` | always the "incidents" collection's uuid | `PGVector` internally, never set directly |
| `embedding` | the 1536-dim vector of `document` | computed internally from `page_content` via `OpenAIEmbeddings` |
| `document` | `f"{title}\n{description}"` — what actually gets embedded | the text list passed to `add_texts` |
| `cmetadata` | `{"title", "description", "risk_tier"}` — what retrieval reads back | the `metadatas=[...]` dict |

**Important caveat:** `id` matching `incidents.id` is a convention this code
maintains by discipline, not a real foreign key — there's no DB constraint
tying `langchain_pg_embedding.id` back to `incidents.id`. If an `Incident` row
is ever deleted, its embedding row won't cascade-delete automatically; nothing
currently deletes incidents, so this is dormant risk, not an active bug.

## Write path — `persist_incident()`

`app/services/incident_service.py`, called from `persist_incident_node` right
after a diagnosis is produced. The embedding write itself is factored into
its own helper, `_embed_incident()`:

```python
def _embed_incident(incident: Incident) -> None:
    risk_tier = incident.risk_tier.value if hasattr(incident.risk_tier, "value") else incident.risk_tier
    get_vectorstore().add_texts(
        [f"{incident.title}\n{incident.description}"],
        metadatas=[{"title": incident.title, "description": incident.description, "risk_tier": risk_tier}],
        ids=[str(incident.id)],
    )
```

The `.value` unwrap matters: `incident.risk_tier` loads as a `RiskTier` enum
instance (from `app/models/incidents.py`), not a plain string. Skipping this
would store `"RiskTier.high"` in `cmetadata` instead of `"high"`.

`persist_incident()` calls `_embed_incident()` **unconditionally** — whether
it just created a new `Incident` row or found an existing one:

```python
incident = db.execute(select(Incident).filter_by(raw_event_id=raw_event_id)).scalar_one_or_none()
if incident is None:
    incident = Incident(...)
    db.add(incident)
    ...
    db.commit()
_embed_incident(incident)  # runs either way
```

This is still a **separate write from the `incidents` insert** — its own
transaction, via `PGVector`'s own session, against the same Postgres
database — so it's still not atomic with the `incidents` row within a single
attempt: if `_embed_incident()` raises right after the `incidents` row
committed, that incident momentarily has no retrievable embedding.

Unlike when this doc was first written, though, that's no longer a permanent
dead end. `persist_incident()`'s idempotency check (`raw_event_id` lookup) is
what makes this function safe to call again on a Celery retry (see
`jobs/webhooks_job.py`'s `self.retry()` and
`documentation/rds-agent/pipeline-end-to-end.md` for the full retry story) —
and because `_embed_incident()` still runs even when the incident already
exists, a retry that reaches this function again *will* re-attempt the
embedding write. So there's real (if incidental, not purpose-built)
reconciliation today, gated entirely on something else triggering a retry —
there's still no dedicated backfill job that goes looking for incidents
missing an embedding on its own.

## Read path — `retrieve_similar_incidents`

`app/agents/domains/rds/nodes/retrieve_similar_incidents.py`, the graph node
that runs between `gather_context` and `diagnose`:

```python
query_text = _build_query_text(state["raw_event"]["payload"])
retriever = get_vectorstore().as_retriever(
    search_type="mmr",
    search_kwargs={"k": 3, "fetch_k": 20, "lambda_mult": 0.7},
)
docs = retriever.invoke(query_text)
return {"similar_incidents": [doc.metadata for doc in docs]}
```

- `_build_query_text` builds a plain string from the *raw alarm payload*
  (`AlarmName`, `Namespace`, `MetricName`, dimensions, `NewStateReason`) —
  there's no diagnosis yet at this point in the graph, so retrieval has to
  work off the alarm itself, not a summary of it.
- `fetch_k: 20` — candidates pulled by plain vector similarity first.
- `k: 3` — how many of those 20 the MMR step actually keeps.
- `lambda_mult: 0.7` — relevance/diversity tradeoff (`1.0` = pure similarity,
  `0.0` = pure diversity). `0.7` leans toward relevance but still penalizes
  near-duplicates.
- The result is `doc.metadata` directly — never `doc.page_content` — so no
  parsing of the combined title+description text is needed on the read side.

## `config/vectorstore.py`

```python
def get_vectorstore() -> PGVector:
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = PGVector(
            embeddings=get_embeddings(),
            connection=engine,          # reuses db.session.engine -- same connection pool as everything else
            collection_name="incidents",
            embedding_length=EMBEDDING_DIM,  # 1536, from app/models/incidents.py
        )
    return _vectorstore
```

Unlike `config/llm.py`'s `get_llm()` or `config/embeddings.py`'s
`get_embeddings()` (cheap client stubs, rebuilt fresh every call),
constructing a `PGVector` issues real DDL
(`CREATE EXTENSION IF NOT EXISTS vector`, `CREATE TABLE IF NOT EXISTS`) — so
this is a lazily-built, cached module-level singleton instead, built once per
process.

## Inspecting it directly

```bash
docker exec devops-agent-postgres-1 psql -U devops_agent -d devops_agent -c "
SELECT i.id, i.title, i.risk_tier, (e.embedding IS NOT NULL) AS has_vector
FROM incidents i
JOIN langchain_pg_embedding e ON e.id = i.id::text
ORDER BY i.created_at DESC
LIMIT 5;
"
```

Exercise the retriever directly (no alarm needed):

```python
from config.vectorstore import get_vectorstore

retriever = get_vectorstore().as_retriever(
    search_type="mmr",
    search_kwargs={"k": 3, "fetch_k": 20, "lambda_mult": 0.7},
)
for doc in retriever.invoke("Dev Aurora CPU Spike AWS/RDS CPUUtilization"):
    print(doc.metadata["title"], "|", doc.metadata["risk_tier"])
```

## Known limitations / not done here

- Write path isn't transactional with the `incidents` insert within a single
  attempt (see above) — reconciliation only happens if something else causes
  `persist_incident()` to run again (a Celery retry); there's no standalone
  backfill job that scans for incidents missing an embedding on its own.
- `Incident.summary_embedding` is legacy/dead — worth a follow-up migration
  to drop it once this is proven out, not urgent.
- Only the RDS domain agent uses this so far; the pattern will need to
  replicate (or generalize) once other domain agents exist (Phase 6).
