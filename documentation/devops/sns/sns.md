# SNS Setup — Topic, Subscription, and Registration

How to create the SNS topic CloudWatch alarms publish to, subscribe this
project's webhook to it, and confirm that subscription — from scratch, with
nothing assumed to already exist.

Every other runbook in this repo (`documentation/devops/rds/rds.md`,
`documentation/devops/consolidated-monitoring-setup.md`) treats an SNS topic
as a precondition — "have the SNS topic ARN ready." This document is what
comes *before* that: how to actually get one.

---

## 1. Where SNS Fits

```text
CloudWatch Alarm
      │
      │  ALARM / OK
      ▼
   SNS Topic
      │
      │  HTTPS POST
      ▼
POST /webhooks/cloudwatch
      │
      ▼
  raw_events
      │
      ▼
    Celery
      │
      ▼
  Domain Agent
```

SNS is the delivery mechanism between CloudWatch and this application. A
CloudWatch alarm doesn't call the webhook directly — it publishes to an SNS
topic, and SNS fans that out to every subscriber of that topic (in this
project, exactly one subscriber: `POST /webhooks/cloudwatch`).

---

## 2. Prerequisites

1. AWS CLI installed and authenticated against the target account:
   ```bash
   aws sts get-caller-identity
   ```
2. The correct AWS region for the resources involved.
3. **A public URL for the webhook.** SNS delivers over HTTPS to a public
   endpoint — it cannot reach `localhost`. For local development, start a
   tunnel first and keep it running for every step below:
   ```bash
   ngrok http 8000
   ```
   Note the `https://....ngrok-free.app` URL it prints — that's what gets
   subscribed in Step 4, with `/webhooks/cloudwatch` appended. For a real
   deployment, this is your actual public domain instead.

---

## 3. Create the SNS Topic

```bash
REGION="ap-south-1"

aws sns create-topic \
  --name devops-agent-alerts \
  --region "$REGION"
```

This returns a `TopicArn`, e.g.:

```json
{
  "TopicArn": "arn:aws:sns:ap-south-1:376129878424:devops-agent-alerts"
}
```

Record this ARN — it's used both when subscribing the webhook (Step 4) and
later as every CloudWatch alarm's `--alarm-actions`/`--ok-actions` (see the
RDS/ECS alarm runbooks).

If a topic with this name already exists, `create-topic` is idempotent —
running it again just returns the same ARN rather than erroring or creating
a duplicate.

---

## 4. Subscribe the Webhook

With the tunnel from Step 2 already running:

```bash
TOPIC_ARN="arn:aws:sns:ap-south-1:376129878424:devops-agent-alerts"
ENDPOINT="https://<your-tunnel-or-domain>/webhooks/cloudwatch"

aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol https \
  --notification-endpoint "$ENDPOINT" \
  --region "$REGION"
```

This does not complete the subscription immediately. AWS responds with:

```json
{
  "SubscriptionArn": "pending confirmation"
}
```

and sends a `SubscriptionConfirmation` message to `$ENDPOINT` — handled in
the next step.

---

## 5. How Confirmation Actually Happens

The `SubscriptionConfirmation` message SNS just sent contains a
`SubscribeURL`. Visiting that URL (a plain HTTPS GET) is what tells AWS
"yes, this endpoint really does want this subscription."

This project's webhook handles that automatically. The relevant code is
`handle_sns_control_message()` in `app/controllers/webhooks.py`:

```text
SubscriptionConfirmation received
        │
        ▼
SNS_AUTO_CONFIRM_SUBSCRIPTIONS=true (default)?
        │
        ├── No  → return {"status": "subscription_confirmation_handled"}, don't confirm
        │
        └── Yes
              │
              ▼
        Is SubscribeURL's host *.amazonaws.com?
              │
              ├── No  → 400 "untrusted SubscribeURL host"
              │
              └── Yes → GET the SubscribeURL → subscription confirmed
```

The host check (`is_trusted_sns_url()` in
`app/controllers/concerns/webhooks/verifiable.py`) exists so a forged
`SubscriptionConfirmation` message pointing `SubscribeURL` at an
attacker-controlled host can't trick this webhook into making an outbound
request to it.

`SNS_AUTO_CONFIRM_SUBSCRIPTIONS` defaults to `true` — no manual
`aws sns confirm-subscription` call is needed in the normal case. Set it to
`false` in `.env` if you'd rather confirm subscriptions by hand.

---

## 6. Verify the Subscription Confirmed

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$TOPIC_ARN" \
  --region "$REGION"
```

Look at `SubscriptionArn` for your endpoint:

```text
PendingConfirmation  → not confirmed yet -- check the webhook logs (logs/app.log)
arn:aws:sns:...      → confirmed, ready to receive real notifications
```

If it's stuck on `PendingConfirmation`, see Troubleshooting below.

---

## 7. Every Inbound Message Is Signature-Verified

Regardless of message type (`Notification`, `SubscriptionConfirmation`,
`UnsubscribeConfirmation`), `handle_cloudwatch_webhook()` calls
`verify_sns_signature()` before doing anything else with the body. This
checks:

1. `SigningCertURL` matches AWS's documented certificate URL pattern
   (`https://sns.<region>.amazonaws.com/SimpleNotificationService-*.pem`) —
   rejecting anything else before ever fetching a certificate from it.
2. The message's signature verifies against that certificate's public key,
   over the exact canonical field order AWS specifies per message type.

A request that fails either check gets `401 invalid SNS signature` and
never reaches `raw_events`. This is why a hand-crafted `curl` POST to
`/webhooks/cloudwatch` (without a valid AWS signature) won't work for
testing — see Testing below for the actual way to generate one.

---

## 8. Point CloudWatch Alarms at This Topic

Once the subscription shows as confirmed, this topic ARN is what every
CloudWatch alarm's `--alarm-actions` and `--ok-actions` should reference —
covered in `documentation/devops/rds/rds.md` and
`documentation/devops/consolidated-monitoring-setup.md`. Both directions
matter:

```bash
--alarm-actions "$TOPIC_ARN"
--ok-actions "$TOPIC_ARN"
```

`--alarm-actions` alone would only ever deliver the `ALARM` state, never the
recovery `OK` state.

---

## 9. Testing End-to-End

The real way to generate a validly-signed SNS message locally is to let SNS
itself send one, by forcing a real alarm state change:

```bash
aws cloudwatch set-alarm-state \
  --alarm-name "Dev Aurora CPU Spike" \
  --state-value ALARM \
  --state-reason "manual SNS test" \
  --region "$REGION"
```

With the tunnel running and the subscription confirmed, this should:

1. Arrive at `/webhooks/cloudwatch` (visible in `logs/app.log`).
2. Pass signature verification.
3. Create a row in `raw_events`:
   ```bash
   docker exec devops-agent-postgres-1 psql \
     -U devops_agent -d devops_agent \
     -c "SELECT payload->>'AlarmName', received_at FROM raw_events
         WHERE source = 'cloudwatch' ORDER BY received_at DESC LIMIT 1;"
   ```

Reset the alarm afterward with `--state-value OK` — this also exercises the
recovery path through the same subscription.

---

## 10. Troubleshooting

**Subscription stuck on `PendingConfirmation`:**
- Is the tunnel from Step 2 still running? A restarted `ngrok` session gets
  a *new* URL — the old subscription now points at a dead endpoint and
  needs to be re-subscribed (Step 4) with the new URL.
- Check `logs/app.log` for a `401 untrusted SubscribeURL host` or a
  signature-verification failure around the time you ran `subscribe`.
- Confirm `SNS_AUTO_CONFIRM_SUBSCRIPTIONS` isn't set to `false` in `.env`.

**`401 invalid SNS signature`:**
- Usually means the request didn't actually come from SNS (e.g. a manual
  `curl` test without a real signature) — this is the security check working
  as intended, not a bug to route around.

**Alarms fire but nothing reaches `raw_events`:**
- Check `aws sns list-subscriptions-by-topic` — if the subscription isn't
  confirmed, SNS has nothing to deliver to.
- Confirm the alarm's `--alarm-actions`/`--ok-actions` actually reference
  this topic's ARN, not a different or misspelled one.

---

## 11. Known Limitations / Not Done Here

- **One topic, one subscriber.** This project uses a single SNS topic with
  a single HTTPS subscription (the webhook). Fan-out to multiple consumers
  isn't part of this setup.
- **No topic access policy hardening documented here.** By default, a new
  SNS topic's access policy allows any CloudWatch alarm in the same account
  to publish to it. Cross-account publishing restrictions, if ever needed,
  aren't covered.
- **Local tunnel URLs are not stable.** A free-tier `ngrok` URL changes
  every restart, which means the SNS subscription (Step 4) needs to be
  redone each time unless a paid/static tunnel domain is used.
- **No automation.** Like the other runbooks in this repo, this is a manual,
  copy-the-commands setup, not a script.
