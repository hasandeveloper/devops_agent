# RDS Diagnosis Pipeline — End to End

How one CloudWatch alarm actually travels through this system, from the SNS
notification hitting the webhook to a Slack message landing in the channel —
every hop, every guardrail, every place something can go wrong and what
happens when it does. Read this before touching anything under
`app/agents/domains/rds/`, `jobs/webhooks_job.py`, `config/celery_app.py`, or
`config/logging.py` — this is the map that ties all of those together.

This project grew a lot of moving parts across one working session (Celery,
rate limits, timeouts, idempotency, a token budget, tests). Each one exists
for a specific, verified reason — this doc is the antidote to "wait, why do
we have all this," written once so nobody has to reconstruct it from git
history or from asking the agent again.

## The 30,000-foot view

```
CloudWatch alarm fires
  -> SNS
  -> POST /webhooks/cloudwatch  (FastAPI, app/controllers/webhooks.py)
  -> rate limit check (Redis)
  -> stored as a raw_events row
  -> aws_sns_event_job.delay(...)   <- webhook responds here, near-instantly
  -----------------------------------------------------------------
  (everything below runs later, in a separate Celery worker process)
  -----------------------------------------------------------------
  -> Celery picks up the task (rate-limited to CELERY_TASK_RATE_LIMIT)
  -> supervisor.route()            (app/agents/supervisor.py)
  -> the RDS LangGraph pipeline     (app/agents/domains/rds/builder.py)
       gather_context
       -> retrieve_similar_incidents
       -> investigate_further
       -> diagnose
       -> persist_incident
       -> notify_slack
```

Two processes, not one: the FastAPI app just stores the event and enqueues a
job. Everything expensive (AWS calls, database lookups, every LLM call) runs
inside the Celery worker, minutes or seconds later, decoupled from the
webhook's response.

## Step 1 — The webhook (`app/controllers/webhooks.py`)

`handle_cloudwatch_webhook` does, in order:

1. **Rate limit check first**, before anything else is parsed or verified —
   `is_rate_limited("rate_limit:cloudwatch_webhook", limit=WEBHOOK_RATE_LIMIT,
   window_seconds=WEBHOOK_RATE_LIMIT_WINDOW_SECONDS)` (default 60 requests /
   60s). Cheapest possible rejection for a flood, real or malicious, before
   spending any effort on it. Backed by Redis (`config/redis_client.py`,
   `app/services/rate_limiter.py`) — a simple `INCR` + `EXPIRE` fixed window,
   not a sliding window, so a burst right at a window boundary can briefly
   allow close to 2x the limit. That's an accepted tradeoff for a circuit
   breaker, not a precise quota system.
2. SNS signature verification, subscription-confirmation handshake handling
   (unrelated to the diagnosis pipeline itself — see
   `handle_sns_control_message`).
3. `store_raw_event(...)` — a `raw_events` row is created. This is the
   permanent record that a notification arrived, independent of whether
   diagnosis ever succeeds.
4. `aws_sns_event_job.delay(...)` — pushes a message onto the Celery/Redis
   queue and returns immediately. **The webhook's job ends here.**

Nothing about diagnosis quality, AI calls, or Slack happens synchronously
with the HTTP request. If nothing ever picks the job up (worker down), the
`raw_events` row still exists with `processed=False` — a durable trail, not
a lost event.

## Step 2 — Celery hands the job to a worker

`jobs/webhooks_job.py`'s `aws_sns_event_job`:

```python
@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def aws_sns_event_job(self, raw_event: dict) -> None:
    raw_event = {**raw_event, "id": uuid.UUID(raw_event["id"])}
    try:
        asyncio.run(supervisor.route(raw_event))
    except (NotImplementedError, ValueError) as exc:
        logger.warning(...)                  # no domain agent -- don't retry
    except TokenBudgetExceeded as exc:
        logger.warning(...)                  # blew its cost budget -- don't retry
    except Exception as exc:
        logger.exception(...)
        raise self.retry(exc=exc)            # genuinely unexpected -- retry (3x, 30s apart)
```

Before this even runs, `config/celery_app.py` applies
`task_annotations = {"...aws_sns_event_job": {"rate_limit": CELERY_TASK_RATE_LIMIT}}`
(default `20/m`) — Celery itself throttles how fast tasks get picked up,
independent of the webhook-level rate limit above. Two separate limits, two
separate layers: one guards raw request volume, the other guards actual
diagnosis-pipeline cost.

**The retry-classification table, in full:**

| Exception | Retried? | Why |
|---|---|---|
| `NotImplementedError` | No | Domain agent (ECS/ALB/CI-CD) genuinely doesn't exist yet — retrying can't ever succeed |
| `ValueError` | No | Unrecognized alarm namespace — same reasoning |
| `TokenBudgetExceeded` | No | Retrying would spend the same excess tokens again for nothing |
| `asyncio.TimeoutError` (LLM/MCP timeout) | **Yes** | Could be a transient hang — worth one more try |
| `GraphRecursionError` (investigation loop cap hit) | **Yes** | Falls through to the generic case today — see "Known limitations" below |
| Anything else | **Yes** | Genuinely unexpected — 3 retries, 30s apart |

`supervisor.route()` itself (`app/agents/supervisor.py`) is a pure table
lookup on `raw_event["source"]` and the alarm's `Trigger.Namespace` — no LLM
call, deterministic, fully covered by `tests/unit/test_supervisor.py`.

## Step 3 — The graph, node by node

`app/agents/domains/rds/builder.py` wires six nodes in a straight line —
no branches, no loops at the graph level (the only loop is *inside*
`investigate_further`'s own agent, see below). `handle()` calls
`_graph.ainvoke(initial_state)` with no `try/except` of its own — anything
any node raises propagates straight up to `webhooks_job.py`'s handling above.

### `gather_context` — no LLM, six sequential lookups

Calls the RDS MCP server (`mcp_server.py`, spawned as its own subprocess) for
cluster info, recent metric trend, the alarm's `environment` tag, active
connections, and lock waits. Every call goes through `config/mcp.py`'s
`invoke_tool()`, which wraps it in `asyncio.wait_for(..., timeout=30)` — a
stalled AWS API call or a wedged database connection can't hang the whole
pipeline (or the worker slot) forever.

`_parse_mcp_result` unwraps whatever text the MCP tool returned, falling back
to raw text if it isn't JSON — a real bug shipped here once (assumed
everything was JSON-encoded, broke on a bare string), now covered by
`tests/unit/test_mcp_result_parsing.py`.

### `retrieve_similar_incidents` — no LLM, one vector search

MMR similarity search (`k=3, fetch_k=20, lambda_mult=0.7`) against past
incidents — see `documentation/rag/pgvector-retrieval.md` for the full
mechanics of that store.

**Retrieval-poisoning risk, currently unmitigated:** every past incident,
right or wrong, gets fed back into every future diagnosis of a similar alarm
as "supporting evidence" — there's no way today to exclude a confirmed-wrong
diagnosis from that. A `reject_incident()` + metadata filter mechanism was
built and tested for this, then deliberately removed (2026-08-15) since
nothing called it — no UI/endpoint/Slack action existed to actually mark an
incident wrong, so it was inert, unreachable code. Revisit this once Phase 4
(human-approval loop) exists and there's a real caller for it.

### `investigate_further` — the one place an LLM makes its own decisions

Everything else in this pipeline is either deterministic code or a single
structured-output LLM call. This node is different: it hands two tools
(`get_performance_insights_top_sql`, `explain_query_for_pid`) to a
`create_agent(...)` ReAct loop and lets the model decide whether to call
them, how many times, and in what order. Three separate guardrails exist
specifically because of that open-endedness:

1. **`recursion_limit=9`** on `agent.ainvoke(...)` — caps the total number of
   agent/tool round trips (roughly 4 tool calls + 1 final answer). Hit it,
   and LangGraph raises `GraphRecursionError`.
2. **`with_timeout()`** wraps both tools (`config/mcp.py`) so the *agent's own*
   tool calls — which our code never invokes directly — still can't hang
   forever. (`invoke_tool()`, used in `gather_context`, only covers calls
   *we* make explicitly; this covers calls the *agent* makes on its own.)
3. **`TokenBudgetTracker`** (`app/agents/shared/token_budget.py`) accumulates
   token usage across every LLM call in the loop via a LangChain callback,
   then `token_tracker.check()` is called *after* the loop returns, raising
   `TokenBudgetExceeded` if the total crossed `MAX_INVESTIGATION_TOKENS`
   (default 20,000).

**Important, non-obvious limitation of #3, confirmed empirically, not
assumed:** raising an exception from inside a LangChain callback (`on_llm_end`)
gets silently caught and only logged by LangChain's own callback manager —
it does **not** interrupt the agent loop mid-flight. So the token tracker can
only *count* while the loop runs; the actual enforcement happens after the
loop finishes on its own (bounded by #1 above), stopping the *pipeline* from
proceeding to `diagnose`/`persist_incident`/`notify_slack`, not stopping the
*investigation* itself from finishing. #1 (`recursion_limit`) is the real
mid-flight ceiling; #3 is a cost gate layered on top of it.

The system prompt (`app/prompts/rds/investigation.py`) also explicitly tells
the model to treat the alarm payload and tool results as data, never as
instructions — see "Prompt injection defense" below.

### `diagnose` — the one structured-output LLM call

```python
llm = get_llm().with_structured_output(Diagnosis)
diagnosis: Diagnosis = await llm.ainvoke(prompt)
```

`Diagnosis` (`app/agents/shared/schema/diagnosis.py`) is a Pydantic model —
`title: str`, `description: str`, `risk_tier: RiskTier` (enum:
`low`/`medium`/`high`). This is the only place in the whole pipeline where
LLM output is schema-constrained; `investigate_further`'s output is a plain
string.

**Prompt injection defense**, added and verified this session: both
`app/prompts/rds/diagnose.py` and `investigation.py` explicitly instruct the
model to treat the alarm/context/tool-result data as data to analyze, never
as instructions — because that data ultimately comes from CloudWatch/the app
database, not from a trusted operator. `tests/eval/test_diagnose_eval.py`
verifies this against a live model with an alarm payload that actually
attempts an injection ("IGNORE ALL PREVIOUS INSTRUCTIONS...") and confirms
the model doesn't comply.

### `persist_incident` — idempotent on a Celery retry

```python
incident = db.execute(select(Incident).filter_by(raw_event_id=raw_event_id)).scalar_one_or_none()
if incident is None:
    incident = Incident(...)
    db.add(incident)
    ...
    db.commit()
_embed_incident(incident)   # always runs, whether just-created or found existing
```

The reason this matters: Celery retries re-run the **entire graph** from
`gather_context` onward, not just the failed step. Without this check, a
failure in `notify_slack` (after `persist_incident` already succeeded) would
create a second incident row and a second vectorstore embedding on retry.
`_embed_incident` deliberately runs unconditionally — PGVector's `add_texts()`
upserts by id (`ON CONFLICT DO UPDATE`), so re-embedding is always safe, and
it's what rescues an incident whose first-attempt embedding write crashed
before completing.

`persist_incident_node` (the graph node wrapping this) also rolls back the
DB session on any exception before closing it — explicit rather than relying
on the connection pool's own reset-on-return behavior.

### `notify_slack` — idempotent on a Celery retry, independently

A **separate** mechanism from the one above, because "does the incident
exist" doesn't answer "did Slack already get told":

```python
if is_incident_notified(db, incident_id):     # check FIRST
    return {"slack_message_ts": None}          # already sent -- skip

await post_diagnosis(...)                       # actually send it

mark_incident_notified(db, incident_id)         # only THEN mark it sent
```

`Incident.notified_at` (migration `0002`) is `NULL` until a Slack post
*actually succeeds*. The ordering here is deliberate: marking it sent
*before* attempting the send would mean a real failure gets permanently
mistaken for "already handled" and the notification silently vanishes
forever, with no retry ever fixing it.

## Where the logs actually go

Two files, two audiences, split by *process*, not by log level:

- **`logs/app.log`** — the FastAPI/Uvicorn process. Startup/shutdown, access
  logs, anything logged while handling an HTTP request.
- **`logs/jobs.log`** — the Celery worker process, *and* the RDS MCP server
  subprocess it spawns. `config/celery_app.py`'s `after_setup_logger` hook
  attaches a handler directly to Celery's own (hijacked) root logger right
  after worker startup, so everything that happens during a job — pipeline
  node logs, MCP tool calls, every LLM HTTP request — lands here, not in
  `app.log`. The MCP subprocess is a *separate OS process* though (spawned
  fresh per tool session via `config/mcp.py`'s `stdio_server()`), so it can't
  inherit that handler — instead `stdio_server()` sets `LOG_FILE_NAME=jobs.log`
  in its environment, and `config/logging.py` reads that env var to decide
  which file its own `configure_logging()` call writes to.

Both files rotate at 50MB (`LOG_MAX_BYTES`/`LOG_BACKUP_COUNT`, one shared
definition in `config/logging.py`) using `ConcurrentRotatingFileHandler`
(file-locking, safe if more than one OS process ever writes the same file —
plain `RotatingFileHandler` isn't).

**The Celery worker does not auto-reload.** Every code change described in
this doc requires restarting it (`Ctrl+C` then
`python -m celery -A config.celery_app worker --loglevel=info`) before it
takes effect — `uvicorn --reload` picks up FastAPI-side changes on its own,
Celery never does.

## What happens when an alarm resolves (`ALARM` → `OK`)

CloudWatch fires a **completely separate SNS notification** for the
recovery — a new `raw_event`, a new UUID, run through the exact same
pipeline above as if it were a brand-new alarm. It is not the same request
retried; it's a second, legitimate event. The resulting incident is a
**second, unlinked row** — same `AlarmName`, different `raw_event_id`,
different `incidents.id`. `Incident.resolved_at` exists on the model
specifically for linking a resolution back to the original incident, but
nothing sets it today — it's dead, same as `IncidentStatus.awaiting_approval`
below. Any sense that the two Slack messages "know about" each other comes
entirely from the AI reading the first incident as a similar-incidents
match and narrating the connection in prose — not from any stored
relationship.

## Known limitations / not done here

- **No linking between an alarm's `ALARM` and `OK` incidents.** See above —
  a real gap, discussed and scoped, not yet built.
- **`GraphRecursionError` (the investigation step-cap) falls through to the
  generic retryable case** in `webhooks_job.py` today, rather than getting
  its own non-retryable branch like `TokenBudgetExceeded` does. Retrying a
  run that hit its recursion limit will very likely hit it again — arguably
  should be reclassified as non-retryable too.
- **No retrieval-poisoning mitigation.** Removed (2026-08-15) rather than
  left unreachable — see `retrieve_similar_incidents`'s section above. Rebuild
  once Phase 4's human-approval loop gives it an actual caller.
- **`IncidentStatus.awaiting_approval`/`resolved`/`diagnosing` are dead
  code** — every incident sits at the model's default (`open`) forever.
  Same Phase 4 gap.
- **No mid-flight interruption for token overspend** — see
  `investigate_further`'s section above; `recursion_limit` is the only real
  mid-flight ceiling today.
- **The webhook-level and Celery-level rate limits are independent** and
  don't share configuration or a combined view — tuning one doesn't
  automatically account for the other.
- **This doc doesn't cover deployment topology.** There's a stated intent to
  run the API and the Celery worker as separate deployed services
  eventually; nothing in the codebase enforces or assumes that split yet —
  it's still one codebase, two entry points, run on one machine during
  development.
