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

## Run with Docker

The whole stack (Postgres, Redis, the API, the Celery worker, and the
one-shot `alembic upgrade head` migration) runs via Compose -- no local
venv or Redis instance required:

```bash
cp .env.example .env   # fill in OPENAI_API_KEY/SLACK_WEBHOOK_URL/DB_*_READONLY_*/AWS_* etc.
docker compose up --build
```

The `worker` container has no host `~/.aws` to fall back on, so
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION` must be set in
`.env` -- every AWS call the RDS MCP server makes (`mcp_server.py`) goes
through `config/aws.py`'s `get_boto3_client()`, which reads these from
`Settings` explicitly rather than relying on boto3's own default credential
chain. `DATABASE_URL` and `CELERY_BROKER_URL`
are set in `docker-compose.yml` to point at the `postgres`/`redis` service
names -- everything else (API keys, Slack webhook, app database creds)
still comes from `.env` via `env_file`.

Postgres is reachable from the host at `localhost:5433` and Redis at
`localhost:6379`, same ports as the non-Docker setup above, so both setups
can use the same `.env`.

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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, running tests, and
how to submit a change. Participation is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md). Found a security issue? See
[SECURITY.md](SECURITY.md) rather than opening a public issue.

## License

[MIT](LICENSE)
