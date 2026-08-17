<div align="center">

# 🩺 devops-agent

## AI-powered AWS incident investigation

![Python](https://img.shields.io/badge/Python-3.13-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![LangGraph](https://img.shields.io/badge/LangGraph-Agent%20Orchestration-orange)
![Postgres](https://img.shields.io/badge/Postgres-pgvector-336791)
![Celery](https://img.shields.io/badge/Celery-Redis-37814A)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20%7C%20Anthropic-black)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

</div>

# 📋 Table of Contents

- [Why do we need this?](#-why-do-we-need-this)
- [What does the agent do?](#-what-does-the-agent-do)
- [How the investigation works](#-how-the-investigation-works)
  - [1. Receive the alarm](#1-receive-the-alarm)
  - [2. Understand the current situation](#2-understand-the-current-situation)
  - [3. Check previous incidents](#3-check-previous-incidents)
  - [4. Investigate further](#4-investigate-further)
  - [5. Generate a diagnosis](#5-generate-a-diagnosis)
  - [6. Send the result to Slack](#6-send-the-result-to-slack)
- [Safety: What can the AI do?](#-safety-what-can-the-ai-do)
- [Cost and reliability controls](#-cost-and-reliability-controls)
- [Architecture](#-architecture)
- [Main components](#-main-components)
- [Project structure](#-project-structure)
- [Quick Start](#-quick-start)
  - [Option 1 — Docker](#option-1--docker)
  - [Option 2 — Run locally](#option-2--run-locally)
- [API Endpoints](#-api-endpoints)
- [Testing](#-testing)
- [Key Features](#-key-features)
- [Current Status](#-current-status)
- [Further Documentation](#-further-documentation)
- [Contributing](#-contributing)
- [License](#-license)

**devops-agent** is an AI system that automatically investigates AWS CloudWatch alarms and explains **what is happening, why it may be happening, and what evidence supports the diagnosis**.

Instead of an engineer manually checking CloudWatch, RDS, PostgreSQL, and Performance Insights, the agent investigates the incident automatically and sends the findings to Slack.

> **The agent investigates — it does not make changes.**

All infrastructure and database tools available to the agent are **read-only**.

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

# 🔐 Safety: What can the AI do?

The most important design decision is:

> **The AI can investigate, but it cannot change infrastructure.**

The RDS agent currently has access only to read operations such as:

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

The database connection also uses a dedicated read-only PostgreSQL role.

# 💰 Cost and reliability controls

The system includes protections against an alarm storm generating unlimited AI work.

It includes:

- Webhook rate limiting
- Celery task rate limiting
- Per-run LLM token limits
- Asynchronous alarm processing
- Retry handling

This prevents a large number of alarms from turning into an uncontrolled number of AI requests.

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
| **Slack** | Delivers the final diagnosis |

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
│   │               └── mcp_server.py
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

# 🧪 Testing

Run normal tests:

```bash
pytest
```

Run LLM evaluation tests:

```bash
pytest -m llm tests/eval
```

LLM evaluation tests use a real LLM and therefore consume API credits.

Run them when changing:

- LLM models
- Investigation prompts
- Diagnosis prompts

# ✨ Key Features

A quick summary of what's described above, in one place:

- ✅ Read-only by design — the AI can investigate, it cannot change infrastructure
- ✅ ReAct-based investigation — the agent decides what to check instead of following a fixed script
- ✅ Similar-incident search using PostgreSQL + pgvector
- ✅ Slack messages include the actual SQL a tool returned, not just the AI's interpretation of it
- ✅ Webhook + Celery task rate limiting, per-run LLM token limits, retry handling
- ✅ Asynchronous alarm processing — the API responds immediately, the investigation runs in the background
- ✅ Dockerized — the full stack runs with `docker compose up --build`
- ✅ Automated tests, including an LLM evaluation suite for the diagnosis itself

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
- Celery/Redis background processing
- Rate limiting
- Token budgets
- Docker support
- Automated tests and LLM evaluation

## 🚧 Planned

### Human approval

The next stage can introduce:

```text
AI diagnosis
     ↓
Human approval
     ↓
Execute action
```

For example, an engineer could approve an infrastructure action from Slack.

### Mutating tools

Eventually the system may support actions such as:

```text
Restart
Scale
Rollback
```

These are **not available to the current agent**.

### More AI agents

Additional domain agents are planned for:

- ECS
- ALB / Target Groups
- ASG
- CloudFront / S3
- CI/CD

# 📚 Further Documentation

For deeper technical details:

| Document | Purpose |
|---|---|
| `documentation/devops/rds/rds.md` | How to configure CloudWatch alarms for RDS/Aurora (connections, CPU, memory, ACU capacity) |
| `documentation/rds-agent/1.your-rds-readonly-db-role-setup.md` | How the read-only database user is configured |
| `documentation/rds-agent/2.how-agent-pipeline-works-end-to-end.md` | How the RDS investigation works from start to finish |
| `documentation/rag/pgvector-retrieval.md` | How similar incidents are stored and retrieved |

# 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, running tests, and how to submit a change.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Found a security issue? See [SECURITY.md](SECURITY.md) rather than opening a public issue.

# 📄 License

[MIT](LICENSE)

# 🔑 In one sentence

> **devops-agent is an AI-powered, read-only AWS incident investigator that turns CloudWatch alarms into evidence-based diagnoses and sends the findings to Slack.**
