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
