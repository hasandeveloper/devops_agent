# Contributing

Thanks for considering a contribution to devops-agent. This document covers
how to get set up, run the test suite, and submit a change.

## Getting set up

Either a local venv or Docker works -- see `README.md`'s "Local setup" and
"Run with Docker" sections for the full steps. Docker is the faster path if
you just want the whole stack (Postgres, Redis, API, Celery worker) running
with one command:

```bash
cp .env.example .env   # fill in the values you need for what you're working on
docker compose up --build
```

You don't need real AWS credentials or a real Slack webhook to work on most
of the codebase -- they're only required for the RDS MCP server's live AWS
calls and for `notify_slack`'s Slack POST, both of which are exercised by
`tests/eval/` (real API calls, not run by default) rather than `tests/unit/`.

## Running tests

```bash
pytest                     # fast, deterministic, no API cost
pytest -m llm tests/eval   # golden-dataset check against a real LLM call --
                            # real cost, run this if you change a prompt or
                            # LLM_PROVIDER/OPENAI_MODEL/ANTHROPIC_MODEL
```

New behavior should come with a `tests/unit/` test where it can be tested
without a live API call. Changes to `app/prompts/rds/` or `diagnose()`'s
output shape should also update/extend `tests/eval/`'s golden dataset.

## Database migrations

Schema changes go through Alembic, not manual DDL:

```bash
alembic revision -m "describe the change"   # then fill in upgrade()/downgrade()
alembic upgrade head
```

## Commit messages

This repo uses a lightweight `type: summary` convention -- `feat`, `fix`,
`chore`, `documentation` are the types in use so far (see `git log` for
examples). Keep the summary focused on *why*, not a restatement of the diff.

## Submitting a change

1. Open a pull request against `main` with a short description of what
   changed and why.
2. Make sure `pytest` passes locally.
3. Keep changes scoped -- a bug fix doesn't need an accompanying refactor,
   and vice versa. Smaller, focused PRs are easier to review.

## Questions

Open a GitHub issue, or see `SECURITY.md` if what you found is a security
concern rather than a bug.
