# Slack Setup — Incoming Webhook for a Specific Channel

How to create the Slack webhook this project posts diagnoses to, and how to
control which channel it lands in. `SLACK_WEBHOOK_URL` shows up in
`.env`/`.env.example` as something to "fill in" everywhere else in this
repo — this document is where that value actually comes from.

---

## 1. Where This Fits

```text
persist_incident
      │
      ▼
notify_slack
      │
      │  HTTP POST {"text": "..."}
      ▼
SLACK_WEBHOOK_URL
      │
      ▼
  One specific Slack channel
```

`notify_slack` (`app/agents/domains/rds/nodes/notify_slack.py`) calls
`post_diagnosis()` in `app/services/slack_service.py`, which does exactly
one thing: an HTTP POST of `{"text": "..."}` to whatever URL
`SLACK_WEBHOOK_URL` holds. There's no Slack SDK, no bot token, no OAuth —
just a plain webhook POST. This is Slack's **Incoming Webhooks** mechanism,
not a Slack bot/app with a bot token.

The important consequence: **a Slack Incoming Webhook URL is permanently
tied to one channel**, chosen when the webhook is created. To post
diagnoses to a different channel, you create a *different* webhook URL —
you can't redirect an existing one.

---

## 2. Prerequisites

- Admin access (or permission to install apps) on the target Slack
  workspace.
- Decide which channel should receive diagnoses *before* creating the
  webhook — e.g. `#devops-alerts` — since that choice is locked in at
  creation time (see above).

---

## 3. Create a Slack App

Incoming Webhooks are configured through a Slack App, even though no bot
functionality is used.

1. Go to <https://api.slack.com/apps> and click **Create New App**.
2. Choose **From scratch**.
3. Name it (e.g. `devops-agent`) and select the target workspace.

---

## 4. Activate Incoming Webhooks

1. In the app's settings, open **Incoming Webhooks** (left sidebar, under
   *Features*).
2. Toggle **Activate Incoming Webhooks** to on.
3. Scroll down and click **Add New Webhook to Workspace**.
4. Select the specific channel this webhook should post to, then **Allow**.

Slack generates a URL shaped like:

```text
https://hooks.slack.com/services/<TEAM_ID>/<WEBHOOK_ID>/<TOKEN>
```

That URL *is* the channel selection — there's no separate "channel" field
sent in the request body that overrides it for this style of webhook.

---

## 5. Configure the Application

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/<TEAM_ID>/<WEBHOOK_ID>/<TOKEN>
```

Add this to `.env` (local) or the environment variables passed to the
`api`/`worker` containers (Docker — see `docker-compose.yml`'s `env_file`).

If this is left blank, `post_diagnosis()` doesn't error — it logs
`SLACK_WEBHOOK_URL not set, skipping notification for incident_id=...` and
returns. Diagnoses still get created and persisted either way; only the
Slack post is skipped.

---

## 6. Posting to a Different or Additional Channel

**To change the channel:** repeat Step 4 (Add New Webhook to Workspace)
selecting the new channel, then update `SLACK_WEBHOOK_URL` to the new URL.
The old webhook keeps working independently until you deactivate it — it
just keeps posting to the old channel if anything still references it.

**To post to more than one channel:** this app supports exactly one
`SLACK_WEBHOOK_URL`. Fanning out to multiple channels isn't implemented —
`post_diagnosis()` posts to a single configured URL. If this is needed
later, Slack's own answer is either multiple webhooks (one POST per
channel, called from the same place) or moving to a real Slack bot token
with `chat.postMessage` and an explicit `channel` parameter per call.

---

## 7. What Actually Gets Posted

From `app/services/slack_service.py`'s `post_diagnosis()`:

```text
*{diagnosis title}*
Risk: {risk_tier}
{diagnosis description}

{query evidence, if any -- see below}

Incident: {incident_id}
```

If `investigate_further` found a specific SQL statement (via
`get_performance_insights_top_sql` or `explain_query_for_pid`), it's
appended under a `Top-load query:`/`Query being explained:` heading as a
code block — the literal SQL text, not the LLM's summary of it.

---

## 8. Testing

The fastest check bypasses the whole pipeline — confirm the webhook URL
itself works before worrying about alarms, Celery, or the LLM:

```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"devops-agent: test message"}' \
  "$SLACK_WEBHOOK_URL"
```

A `#devops-alerts` (or whichever channel you chose) message should appear
immediately. If this doesn't work, nothing downstream will either — fix
this before testing the full alarm pipeline.

Once that works, test the real path by firing an actual alarm (see
`documentation/devops/sns/sns.md` §9 and `documentation/devops/rds/rds.md`)
and confirming the diagnosis lands in the channel with the same formatting
as Section 7.

---

## 9. Troubleshooting

**`curl` test works but real diagnoses never arrive:**
- Check `logs/jobs.log` (the Celery worker's log) for
  `SLACK_WEBHOOK_URL not set, skipping notification` — confirms the
  *worker* process's environment actually has the variable (Docker: check
  `docker-compose.yml`'s `env_file` applies to the `worker` service, not
  just `api`).
- Confirm `persist_incident` actually ran — if `diagnose` or an earlier
  node raised, `notify_slack` never gets reached at all.

**Message posts but to the wrong channel:**
- The webhook URL itself determines the channel (Section 4) — there is no
  runtime channel override to check. Re-create the webhook against the
  correct channel (Section 6).

**`404`/`no_service_id` on the `curl` test:**
- The webhook was likely deactivated or deleted from the Slack App's
  settings. Re-create it (Section 4) and update `.env`.

---

## 10. Known Limitations / Not Done Here

- **One channel per webhook, one webhook configured.** No multi-channel
  routing (e.g. "production alarms to `#prod-incidents`, dev alarms to
  `#dev-alerts`") is implemented — every diagnosis goes to whatever single
  channel `SLACK_WEBHOOK_URL` points at, regardless of the alarm's
  `environment`.
- **No interactive buttons.** Posting is one-way — this is plain Incoming
  Webhooks, not Slack's Block Kit interactive components. The planned
  human-approval workflow (see README's Roadmap) would need a real Slack
  app with a bot token and request signing, not this webhook mechanism.
- **No message threading.** Each diagnosis is a standalone message; an
  `ALARM` and its later `OK` recovery aren't grouped into a thread.
- **No retry-specific messaging.** If `notify_slack` is reached again via a
  Celery retry, `is_incident_notified()`'s check prevents a duplicate post
  (see `documentation/rds-agent/2.how-agent-pipeline-works-end-to-end.md`
  §25) — but there's no distinct "this is a retry" indicator in the
  message itself.
