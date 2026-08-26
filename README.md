<div align="center">

# 🩺 devops-agent

## AI-powered AWS incident investigation

![Python](https://img.shields.io/badge/Python-3.13-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![LangGraph](https://img.shields.io/badge/LangGraph-Agent%20Orchestration-orange)
![Postgres](https://img.shields.io/badge/Postgres-pgvector-336791)
![Celery](https://img.shields.io/badge/Celery-Redis-37814A)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20%7C%20Anthropic-black)
![Tests](https://github.com/hasandeveloper/devops_agent/actions/workflows/tests.yml/badge.svg)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

</div>

# 📋 Table of Contents

- [Why do we need this?](#why-do-we-need-this)
- [What does the agent do?](#what-does-the-agent-do)
- [How the investigation works](#how-the-investigation-works)
  - [1. Receive the alarm](#1-receive-the-alarm)
  - [2. Understand the current situation](#2-understand-the-current-situation)
  - [3. Check previous incidents](#3-check-previous-incidents)
  - [4. Investigate further](#4-investigate-further)
  - [5. Generate a diagnosis](#5-generate-a-diagnosis)
  - [6. Send the result to Slack](#6-send-the-result-to-slack)
- [Human-in-the-loop remediation](#human-in-the-loop-remediation)
- [Safety: What can the AI do?](#safety-what-can-the-ai-do)
- [Cost and reliability controls](#cost-and-reliability-controls)
- [Architecture](#architecture)
- [Main components](#main-components)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
  - [Option 1 — Docker](#option-1--docker)
  - [Option 2 — Run locally](#option-2--run-locally)
- [API Endpoints](#api-endpoints)
- [Testing](#testing)
- [Key Features](#key-features)
- [Current Status](#current-status)
- [Further Documentation](#further-documentation)
- [Contributing](#contributing)
- [License](#license)

**devops-agent** is an AI system that automatically investigates AWS CloudWatch alarms and explains **what is happening, why it may be happening, and what evidence supports the diagnosis**.

Instead of an engineer manually checking CloudWatch, RDS, PostgreSQL, and Performance Insights, the agent investigates the incident automatically and sends the findings to Slack.

> **The agent investigates on its own. It only ever changes anything with a human's explicit, per-action approval.**

Every investigation tool is **read-only**. The exceptions — cancelling a single, specifically-named runaway query, or terminating a single, specifically-named blocking connection — only ever run after a human approves that exact target in Slack. See [Human-in-the-loop remediation](#human-in-the-loop-remediation) below.

<a id="why-do-we-need-this"></a>
# 🎯 Why do we need this?

A CloudWatch alarm tells you:

> **Something is wrong.**

But it usually doesn't tell you:

> **Why is it wrong?**

For example:

```text
🚨 CPUUtilization > 80%
```

An engineer would normally need to:

1. Find the affected RDS instance.
2. Check whether CPU is actually increasing.
3. Check active database connections.
4. Check for lock waits.
5. Check which SQL queries are consuming the most resources.
6. Look at previous incidents.
7. Decide what the likely cause is.
8. Share the findings with the team.

The agent automates this initial investigation.

<a id="what-does-the-agent-do"></a>
# 🤖 What does the agent do?

When an AWS alarm fires, the system follows this flow:

```text
CloudWatch Alarm
       ↓
      SNS
       ↓
   devops-agent
       ↓
Understand the alarm
       ↓
Check AWS + Database
       ↓
Look for similar incidents
       ↓
Investigate further if needed
       ↓
Generate diagnosis
       ↓
Save incident
       ↓
Post findings to Slack
```

The agent can decide what additional information it needs instead of blindly running every possible check.

<a id="how-the-investigation-works"></a>
# 🔍 How the investigation works

## 1. Receive the alarm

AWS CloudWatch sends an alarm notification to:

```http
POST /webhooks/cloudwatch
```

The service verifies the SNS request and stores the original alarm in the database.

The API responds immediately instead of making AWS wait for the entire AI investigation.

## 2. Understand the current situation

The agent gathers information such as:

- RDS cluster status
- Database instance status
- Recent CloudWatch metric trends
- Active database connections
- Database lock waits
- Environment information

This gives the AI **current evidence**, rather than asking it to guess from the alarm alone.

## 3. Check previous incidents

The system searches previous incidents to find cases that look similar.

For example:

```text
Current incident:
Aurora CPU spike on DB instance A

Previous incident:
Aurora CPU spike on DB instance A
```

Previous incidents provide historical context.

This uses **PostgreSQL + pgvector** to find similar incidents.

> **Important:** Previous incidents are historical evidence, not guaranteed truth. The current incident must still be diagnosed using its own evidence.

## 4. Investigate further

The agent can decide:

> "I need more information before I can diagnose this."

For example:

```text
CPU is 100%
      ↓
Are there many active connections?
      ↓
Are queries blocked?
      ↓
What SQL is consuming the most database load?
      ↓
What is the execution plan?
```

This is handled using a **ReAct-based agent**, which means the AI can reason about what information it needs and then call the appropriate read-only tool.

## 5. Generate a diagnosis

Once enough evidence has been collected, the AI produces a structured diagnosis containing:

- **Title**
- **Risk level**
- **Plain-English explanation**
- Supporting evidence

The diagnosis is then saved to the database.

## 6. Send the result to Slack

The final diagnosis is posted to Slack.

If the investigation finds a problematic SQL query, the system includes the **actual SQL returned by the database tools**, rather than only showing the AI's interpretation of it.

Example:

```text
🚨 Dev Aurora CPU Spike

Risk: HIGH

What happened:
CPU utilization reached 100%.

Likely cause:
A high-load SQL query was consuming most of
the database resources.

Evidence:
- CPU reached 100%
- No lock waits detected
- Query X accounted for 72% of DB load

SQL:
SELECT ...
```

<a id="human-in-the-loop-remediation"></a>
# 🛠️ Human-in-the-loop remediation

Diagnosis is the foundation. For a small, deliberately narrow set of fixes, the RDS agent can now also **propose an action** — and, only if a human approves it in Slack, actually carry it out.

> **Nothing happens automatically.** Every remediation action requires an explicit human click, one target at a time, before anything runs.

## What's built today

**Phase 1 — cancel a runaway query.** When a diagnosis isn't low-risk, the agent looks for queries that have been running unusually long, asks the LLM to filter out anything that looks like expected background work (a backup, an explicit `VACUUM`), and — for what's left — posts each one to Slack individually:

```text
PID: 1234
Duration: 8m 32s
Query: SELECT ...

Reason:
This query has been running significantly longer
than the configured threshold.

[Approve]  [Reject]
```

A human can approve one, several, or all of them ("Approve All Remaining" handles the last case). Only on approval does the system re-check that the query is *still* running the *same* thing before cancelling it — if it already finished on its own, or its process ID has since been reused by something else entirely, the system backs off instead of risking the wrong target.

**Phase 2 — disconnect an idle-in-transaction connection.** An idle-in-transaction connection has no running query — there's nothing for a cancel to interrupt, so the only way to release whatever lock it's holding is to end the session entirely. That's a heavier action than Phase 1's, so the gate is stricter: a candidate has to be both idle past a (higher) threshold *and* confirmed to actually be blocking another query right now, not idle alone. The Slack copy says so honestly:

```text
PID: 5678
Idle for: 8m 03s
Query: UPDATE orders SET ...

Reason:
This connection is blocking other queries.

[Terminate Connection]  [Reject]
```

The re-check before acting is also stronger than Phase 1's — in addition to matching the query text, it confirms the connection's exact start timestamp still matches, since a pid can be reused by an unrelated newer connection and query text alone isn't a strong enough fingerprint for an action this disruptive.

One real gap worth knowing: there's no native CloudWatch metric for idle-in-transaction connection count, and Performance Insights doesn't catch it either (an idle session contributes zero to its Database Load measurement by definition) — this phase's own tool has to query `pg_stat_activity` directly because AWS's own monitoring has nothing to offer here. See `documentation/rds-agent/4.hitl-remediation-phase-2-terminate-idle-connection.md` §8 for the full detail and the workarounds if you want a dedicated alarm anyway.

**A resolved alarm never proposes anything, either.** CloudWatch sends a notification on every state change, including back to `OK` — both remediation phases check the triggering alarm's own state and skip proposing an action entirely if it isn't currently `ALARM`. Diagnosis still runs either way; only the propose-a-fix step short-circuits. This exists because two independent alarms fired off the same underlying load once re-proposed terminating connections a *different* alarm's own approved fix had already handled moments earlier.

## The full roadmap

Every future fix follows the same shape — propose, get explicit human approval, re-verify right before acting — just applied to a riskier action each time:

| Phase | Fix | Risk | Status |
|---|---|---|---|
| 1 | Cancel a runaway query | 🟢 Low–Medium | ✅ Built |
| 2 | Disconnect an idle-in-transaction connection | 🟡 Medium | ✅ Built |
| 3 | Disconnect a connection blocking others | 🟡 Medium | ⏳ Not started |
| 4 | Raise the database's capacity ceiling | 🟠 Medium–High | ⏳ Not started |

Risk rises from top to bottom on purpose — the earliest phases interrupt one query or connection without touching the instance itself; the latest phases affect the whole database. Every phase still requires a human decision per action, regardless of how low its risk is rated.

## Beyond RDS

This investigate → diagnose → propose a fix → human approves → act pattern isn't meant to stay specific to RDS. The plan is to extend the same loop to the other AWS services already stubbed into the routing layer today — **ECS, EC2, EBS, Application Load Balancers, and CI/CD pipeline events** — once their own domain agents are built, using the exact same safety model throughout rather than a special case for databases.

<a id="safety-what-can-the-ai-do"></a>
# 🔐 Safety: What can the AI do?

The most important design decision is:

> **The AI investigates freely. It only ever acts through a small, deliberately narrow set of fixes, and only with a human's explicit, per-action approval.**

The RDS agent has access to read operations such as:

```text
AWS:
✓ Describe RDS
✓ Read CloudWatch metrics
✓ Read CloudWatch tags
✓ Read Performance Insights
✓ Read replica lag

Database:
✓ Read connection information
✓ Read lock information
✓ Read running queries
✓ Run EXPLAIN
✓ Read table bloat / vacuum stats
```

It does **not** have tools to:

```text
✗ Restart a database
✗ Scale a database
✗ Modify configuration
✗ Delete data
✗ Update data
✗ Execute arbitrary SQL
```

The exceptions, each gated entirely behind human approval (see [Human-in-the-loop remediation](#human-in-the-loop-remediation) above): the agent can cancel one specific, named query, or terminate one specific, named connection that's confirmed to be blocking others — never anything else, and never without a human clicking Approve for that exact target first.

Two dedicated PostgreSQL roles enforce this separation at the database level, not just in application code:

- A **read-only** role, used by every investigation tool.
- A **separate, minimally-privileged** role, used only by the write actions — granted just enough to cancel a query, terminate a connection, and read other sessions' query text (the same `pg_signal_backend` grant covers both `pg_cancel_backend` and `pg_terminate_backend`), never superuser, never table access.

<a id="cost-and-reliability-controls"></a>
# 💰 Cost and reliability controls

The system includes protections against an alarm storm generating unlimited AI work.

It includes:

- Webhook rate limiting
- Celery task rate limiting
- Per-run LLM token limits
- Asynchronous alarm processing
- Retry handling

This prevents a large number of alarms from turning into an uncontrolled number of AI requests.

<a id="architecture"></a>
# 🏗️ Architecture

```text
                    AWS
                     │
             CloudWatch Alarm
                     │
                     ▼
                    SNS
                     │
                     ▼
          ┌─────────────────────┐
          │      FastAPI        │
          │                     │
          │ Verify + Store      │
          │ the alarm           │
          └──────────┬──────────┘
                     │
                     ▼
                 Redis/Celery
                     │
                     ▼
          ┌─────────────────────┐
          │     LangGraph       │
          │    RDS Agent        │
          │                     │
          │ Gather context      │
          │       ↓             │
          │ Similar incidents   │
          │       ↓             │
          │ Investigate         │
          │       ↓             │
          │ Diagnose            │
          │       ↓             │
          │ Save incident       │
          │       ↓             │
          │ Propose remediation │
          │       ↓             │
          │ Propose idle conn.  │
          │ remediation         │
          │       ↓             │
          │ Notify Slack        │
          └──────────┬──────────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       AWS RDS   PostgreSQL   pgvector
       CloudWatch  DB         History
       Perf. Insights
                     │
                     ▼
                  Slack
```

When the RDS agent proposes a fix, a second, independent loop takes over — triggered by a Slack click, not by the diagnosis pipeline above:

```text
     Slack (Approve/Reject click)
              │
              ▼
  POST /webhooks/slack/interactions
              │
      Verify it's really Slack
              │
              ▼
      Record the decision
              │
     ┌────────┴────────┐
     ▼                 ▼
  Rejected          Approved
     │                 │
     │                 ▼
     │        Re-check the target is
     │        still the same one
     │                 │
     │        ┌────────┴────────┐
     │        ▼                 ▼
     │  Cancel/terminate   Already resolved —
     │        it              do nothing
     │        │                 │
     └────────┴────────┬────────┘
                        ▼
           Post the result back to Slack
```

<a id="main-components"></a>
# 🧩 Main components

| Component | Purpose |
|---|---|
| **FastAPI** | Receives AWS and GitHub webhooks |
| **Celery** | Runs investigations in the background |
| **Redis** | Queues Celery jobs |
| **LangGraph** | Controls the AI investigation workflow |
| **LLM** | Reasons about the incident |
| **MCP** | Gives the AI controlled access to AWS/database tools |
| **PostgreSQL** | Stores alarms and incidents |
| **pgvector** | Finds similar historical incidents |
| **CloudWatch** | Provides AWS monitoring data |
| **Performance Insights** | Shows database load and SQL |
| **Slack** | Delivers the final diagnosis, and the Approve/Reject/Approve-All buttons for a proposed fix |
| **MCP (write-capable)** | A separate, minimally-privileged server that carries out an approved fix — never used during investigation |

<a id="project-structure"></a>
# 📁 Project structure

```text
devops-agent/
│
├── app/
│   ├── agents/
│   │   ├── supervisor.py
│   │   ├── domains/
│   │   │   └── rds/
│   │   │       ├── builder.py
│   │   │       └── nodes/
│   │   ├── shared/
│   │   └── tools/
│   │       └── mcp/
│   │           └── rds/
│   │               ├── mcp_server.py              (read-only, investigation)
│   │               └── remediation_mcp_server.py  (write, human-approved only)
│   │
│   ├── controllers/
│   ├── models/
│   ├── prompts/
│   └── services/
│
├── config/
├── db/
├── jobs/
├── routers/
├── tests/
├── documentation/
│
├── Dockerfile
├── docker-compose.yml
├── main.py
└── requirements.txt
```

The important areas are:

```text
agents/
    → AI logic

tools/
    → Tools the AI can use

models/
    → Database models

services/
    → Business operations

jobs/
    → Background processing

tests/
    → Automated tests and AI evaluation

documentation/
    → Detailed technical documentation
```

<a id="prerequisites"></a>
# ✅ Prerequisites

**Mandatory — do these before (or immediately after) you first boot the project.** Without them, the app runs but nothing useful happens: no alarms arrive, no diagnosis reaches anyone, and the database roles the agent depends on don't exist yet.

| Document | Purpose |
|---|---|
| `documentation/devops/sns/sns.md` | How to create/subscribe/register the SNS topic that delivers alarms to this app |
| `documentation/devops/slack/slack.md` | How to create the Slack Incoming Webhook and choose which channel gets diagnoses |
| `documentation/devops/rds/rds.md` | How to configure CloudWatch alarms for RDS/Aurora (connections, CPU, memory, ACU capacity) |
| `documentation/rds-agent/1.your-rds-readonly-db-role-setup.md` | How the read-only and write-capable (remediation) database roles are configured |

Do these in order — SNS first (nothing arrives without it), then Slack (nothing gets reported without it), then the RDS alarms themselves, then the database roles (required for every investigation and remediation tool the agent calls). None of these are optional for a working setup, even a local/dev one.

<a id="quick-start"></a>
# 🚀 Quick Start

> **A public tunnel (e.g. [ngrok](https://ngrok.com)) is required for local development.**
> AWS SNS delivers CloudWatch alarms over HTTPS to a public URL — it cannot
> reach `localhost`. To receive real alarms locally (either setup below),
> run something like `ngrok http 8000` and point your SNS subscription at the
> resulting `https://...ngrok-free.app/webhooks/cloudwatch` URL instead of
> `localhost:8000`. Without this, everything else works, but no CloudWatch
> alarm will ever reach your machine.

## Option 1 — Docker

The easiest way to run the complete system is:

```bash
cp .env.example .env
```

Add your required:

```text
OPENAI_API_KEY
SLACK_WEBHOOK_URL
AWS credentials
Database credentials
```

Investigation-only setup works with just those. For the remediation feature specifically, also set `SLACK_SIGNING_SECRET` and the `DB_*_REMEDIATION_*` credentials — see `documentation/rds-agent/3.hitl-remediation-phase-1-cancel-query.md`.

Then:

```bash
docker compose up --build
```

Docker starts the API, PostgreSQL, Redis, Celery worker, and database migrations.

## Option 2 — Run locally

Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure:

```bash
cp .env.example .env
```

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Run migrations:

```bash
alembic upgrade head
```

Start the API:

```bash
uvicorn main:app --reload
```

Start Celery:

```bash
python -m celery -A config.celery_app worker --loglevel=info
```

<a id="api-endpoints"></a>
# 🔌 API Endpoints

### Health check

```http
GET /health
```

Checks whether the application can connect to the database.

### CloudWatch webhook

```http
POST /webhooks/cloudwatch
```

Receives CloudWatch/SNS alarm notifications.

### GitHub webhook

```http
POST /webhooks/github
```

Receives GitHub Actions events.

GitHub events are stored, but they currently **do not trigger an AI investigation**.

### Slack interactions

```http
POST /webhooks/slack/interactions
```

Receives Approve / Reject / Approve All Remaining button clicks for a proposed remediation. Verifies the request is genuinely from Slack before recording any decision or running anything.

<a id="testing"></a>
# 🧪 Testing

Run the guardrail suite (fast, deterministic, no API cost):

```bash
pytest
```

This covers signature verification for every webhook source (SNS, GitHub, Slack), SQL-injection and stale-target safety checks, prompt structure, and routing/parsing correctness — see `tests/guardrails/`.

Run the LLM evaluation suite (real LLM calls, real cost):

```bash
pytest -m llm tests/eval
```

This is a golden-dataset regression check against `diagnose`'s structured output.

Run it when changing:

- LLM models
- Investigation prompts
- Diagnosis prompts

Both suites run automatically on every pull request via `.github/workflows/tests.yml` (guardrails against a Redis service container, eval against the real LLM configured in secrets) — a PR can't merge unless both are green.

<a id="key-features"></a>
# ✨ Key Features

A quick summary of what's described above, in one place:

- ✅ Read-only investigation by design — the AI investigates freely, but changes nothing on its own
- ✅ Human-in-the-loop remediation — a risk-ranked, growing set of fixes, each requiring explicit per-target Slack approval and a fresh safety re-check immediately before acting
- ✅ ReAct-based investigation — the agent decides what to check instead of following a fixed script
- ✅ Similar-incident search using PostgreSQL + pgvector
- ✅ Slack messages include the actual SQL a tool returned, not just the AI's interpretation of it
- ✅ Signature verification on every webhook source — SNS, GitHub, and Slack interactions
- ✅ Webhook + Celery task rate limiting, per-run LLM token limits, retry handling
- ✅ Asynchronous alarm processing — the API responds immediately, the investigation runs in the background
- ✅ Dockerized — the full stack runs with `docker compose up --build`
- ✅ Automated tests, split into a fast guardrail suite and an LLM evaluation suite
- ✅ CI on every pull request — both suites must pass before a merge is allowed

<a id="current-status"></a>
# 🗺️ Current Status

## ✅ Currently built

- CloudWatch alarm ingestion
- SNS verification
- RDS AI agent
- Read-only AWS/database investigation
- CloudWatch metric analysis
- PostgreSQL investigation
- Performance Insights investigation
- Similar-incident search using pgvector
- AI-generated diagnosis
- Slack notifications
- **Human-in-the-loop remediation, Phase 1** — propose cancelling a runaway query, Slack Approve / Reject / Approve All Remaining, a fresh safety re-check immediately before acting
- **Human-in-the-loop remediation, Phase 2** — propose terminating an idle-in-transaction connection confirmed to be blocking others, a stronger recheck (query text + connection start time) before acting
- Signature verification for every webhook source (SNS, GitHub, Slack)
- Celery/Redis background processing
- Rate limiting
- Token budgets
- Docker support
- Automated tests (guardrail suite + LLM evaluation), split into `tests/guardrails/` and `tests/eval/`

## 🚧 Planned

### More remediation phases

Phases 3–4 (disconnecting a lock blocker, raising the capacity ceiling) are not built yet — see the risk-ranked roadmap table under [Human-in-the-loop remediation](#human-in-the-loop-remediation) for exactly what's next. VACUUM, instance reboot, and failover were dropped from the roadmap entirely — VACUUM had no dedicated alarm signal to justify it, and reboot/failover were judged too drastic for this system's human-approval model to cover well.

### More AI agents

Additional domain agents, using the same investigate → diagnose → propose a fix → human approves → act pattern already built for RDS, are planned for the sources and namespaces already stubbed into `supervisor.py` today:

- ECS
- EC2
- EBS
- Application Load Balancers (ALB)
- CI/CD pipeline events (GitHub Actions)

None of these route anywhere yet — each currently raises `NotImplementedError` rather than silently doing nothing.

<a id="further-documentation"></a>
# 📚 Further Documentation

For deeper technical details follow these below sequential order:

| Document | Purpose |
|---|---|
| `documentation/devops/sns/sns.md` | How to create/subscribe/register the SNS topic that delivers alarms to this app |
| `documentation/devops/slack/slack.md` | How to create the Slack Incoming Webhook and choose which channel gets diagnoses |
| `documentation/devops/rds/rds.md` | How to configure CloudWatch alarms for RDS/Aurora (connections, CPU, memory, ACU capacity) |
| `documentation/rds-agent/1.your-rds-readonly-db-role-setup.md` | How the read-only *and* write-capable (remediation) database roles are configured |
| `documentation/rds-agent/2.how-agent-pipeline-works-end-to-end.md` | How the RDS investigation works from start to finish |
| `documentation/rds-agent/3.hitl-remediation-phase-1-cancel-query.md` | How Phase 1 remediation (cancel a runaway query) actually works, end to end |
| `documentation/rds-agent/4.hitl-remediation-phase-2-terminate-idle-connection.md` | How Phase 2 remediation (terminate a blocking idle-in-transaction connection) works, including why no CloudWatch alarm covers this directly |
| `documentation/rag/pgvector-retrieval.md` | How similar incidents are stored and retrieved |

<a id="contributing"></a>
# 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, running tests, and how to submit a change.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Found a security issue? See [SECURITY.md](SECURITY.md) rather than opening a public issue.

<a id="license"></a>
# 📄 License

[MIT](LICENSE)

# 🔑 In one sentence

> **devops-agent is an AI-powered AWS incident investigator that turns CloudWatch alarms into evidence-based diagnoses — and, for a small, risk-ranked, human-approved set of fixes, can act on them too.**
