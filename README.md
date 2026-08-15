# devops-agent

Phase 1 of a DevOps agentic system: ingestion + storage only. No agent
reasoning yet — CloudWatch alarms and GitHub Actions events land here and are
persisted as `raw_events` for later phases to diagnose.

## Roadmap

1. **This phase** — FastAPI ingestion + Postgres/pgvector schema (CloudWatch + GitHub Actions webhooks).
2. LangGraph Triage agent with read-only MCP tools (ECS, CloudWatch) → posts diagnosis to Slack.
3. pgvector RAG over past incidents to improve diagnosis quality.
4. Human approval loop: LangGraph `interrupt()` + Slack interactive buttons.
5. Mutating MCP tools (restart/scale/rollback) executed via Celery after approval.
6. Supervisor + per-domain agents (ECS, RDS, ALB/TargetGroup, ASG, CloudFront/S3, CI/CD).

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

docker compose up -d postgres
alembic upgrade head

uvicorn main:app --reload
```

Alarm dispatch runs through Celery (`jobs/webhooks_job.py`), so a worker needs
to be running too, alongside a Redis instance (`CELERY_BROKER_URL` in `.env` —
this project doesn't run Redis itself, point it at one you already have):

```bash
python -m celery -A config.celery_app worker --loglevel=info
```

(`python -m celery`, not the bare `celery` command -- the console-script entry
point doesn't reliably put the project root on `sys.path`, so the worker
fails to import `jobs.webhooks_job` with `ModuleNotFoundError: No module
named 'jobs'` otherwise. `python -m` always adds the current directory to
`sys.path`.)

## Testing

```bash
pytest                # fast, deterministic, no API cost -- tests/unit/
pytest -m llm tests/eval   # golden-dataset regression check for diagnose()'s LLM
                            # output -- real OpenAI calls, real cost, excluded from the
                            # default run (see pytest.ini). Run this after changing
                            # OPENAI_MODEL/ANTHROPIC_MODEL or any RDS prompt.
```

## Endpoints

- `GET /health` — DB connectivity check.
- `POST /webhooks/cloudwatch` — SNS delivery target for CloudWatch Alarms.
  Handles `SubscriptionConfirmation` (auto-confirms if the `SubscribeURL`
  host is `*.amazonaws.com`), `UnsubscribeConfirmation`, and `Notification`
  message types. Alarm payloads are stored as `raw_events`.
- `POST /webhooks/github` — GitHub Actions webhook target. If
  `GITHUB_WEBHOOK_SECRET` is set, requests are verified against
  `X-Hub-Signature-256`. Events are stored as `raw_events`.

## Schema

- `raw_events` — every inbound alert/event, source + type + resource id + raw payload.
- `incidents` — one row per triaged issue, linked to its originating `raw_event`,
  carries a `pgvector` `summary_embedding` column populated once diagnosis
  (Phase 2/3) generates a summary, used for similarity search against past
  incidents.
