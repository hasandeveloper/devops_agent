# CloudWatch Alarm Setup — New Environment Runbook

How to set up the full 3-layer alarm coverage (infra, container/orchestration,
application) for any ECS cluster from scratch. Follow this top to bottom for
a brand-new environment (staging, production, a new dev cluster, etc.) —
every command is parameterized, nothing here is specific to one cluster.

None of these commands run automatically — copy them into a terminal with
AWS CLI authenticated against the target AWS account and run them yourself.

## The 3 monitoring layers

1. **Infra** — CPU, memory, disk, network/host health
2. **Container/orchestration** — is ECS actually running what it's supposed
   to, are load-balancer targets healthy
3. **Application** — latency and error rate, closest to what a user actually
   experiences

16 alarms total per cluster: 4 infra, 4 container/orchestration (one per
service), 8 application (latency + 5xx, one each per service). If your
cluster runs a different number of services, scale the container/orchestration
and application counts accordingly — infra stays fixed at 4.

## Prerequisites

1. **AWS CLI authenticated** against the target account:
   ```bash
   aws sts get-caller-identity
   ```
2. **This runbook is written for zsh** (macOS's default interactive shell).
   The `for` loops in Step 3 use `${(k@)TG}` — zsh's native syntax for "list
   this associative array's keys, one per word." Don't substitute the bash
   equivalent (`${!TG[@]}`) here: interactively, zsh's history expansion
   treats a bare `!` as a history reference and fails with `event not
   found`, and macOS's bundled `/bin/bash` is the ancient 3.2 (pre-2007,
   frozen by Apple over GPLv3 licensing), which predates associative arrays
   (`declare -A`) entirely — so "just switch to bash" isn't a reliable fix
   on a Mac unless you have a modern bash installed separately (e.g. via
   Homebrew).
   **The `@` flag matters, not just `(k)`:** quoted zsh array expansions
   without `@` (e.g. `"${(k)TG}"`) collapse all elements into one joined
   string instead of splitting into separate loop words — the loop then
   runs once with a garbage combined value instead of four times, and the
   associative-array lookup silently returns empty. `"${(k@)TG}"` is the
   form that actually iterates one word per key, the zsh equivalent of
   bash's `"${!TG[@]}"`.
   If you're adapting this runbook for a bash-only environment (e.g. a
   Linux CI runner with bash 4+), swap `${(k@)TG}` back to `${!TG[@]}` in
   the three loops below.
3. **An SNS topic that your webhook is subscribed to.** Alarms need
   somewhere to deliver to. If you haven't set one up for this environment
   yet, see the CloudWatch webhook setup steps already documented for this
   project (create topic → subscribe `/webhooks/cloudwatch` over HTTPS →
   confirm subscription). Have the topic ARN ready before continuing.

---

## Step 1 — Discover your resources

Run these against the environment you're setting up to fill in the
variables used in Step 2. Replace `<CLUSTER_NAME>` and `<REGION>` throughout.

```bash
CLUSTER_NAME="<CLUSTER_NAME>"
REGION="<REGION>"

# List services on the cluster
aws ecs list-services --cluster "$CLUSTER_NAME" --region "$REGION" --output text

# For each service: launch type, desired/running count, target group ARN
aws ecs describe-services --cluster "$CLUSTER_NAME" --region "$REGION" \
  --services <service-1> <service-2> ... \
  --query 'services[].{name:serviceName,launchType:launchType,targetGroups:loadBalancers[].targetGroupArn}' \
  --output json

# EC2 instance(s) backing the cluster (only relevant if launchType is EC2, not FARGATE)
CI_ARNS=$(aws ecs list-container-instances --cluster "$CLUSTER_NAME" --region "$REGION" --query 'containerInstanceArns' --output text)
aws ecs describe-container-instances --cluster "$CLUSTER_NAME" --container-instances $CI_ARNS --region "$REGION" \
  --query 'containerInstances[].{ec2InstanceId:ec2InstanceId,status:status}' --output json

# EBS volume(s) on that instance
aws ec2 describe-volumes --filters "Name=attachment.instance-id,Values=<INSTANCE_ID>" --region "$REGION" \
  --query 'Volumes[].{VolumeId:VolumeId,Size:Size,State:State}' --output json

# Load balancer ARN behind a target group
aws elbv2 describe-target-groups --target-group-arns "<TARGET_GROUP_ARN>" --region "$REGION" \
  --query 'TargetGroups[0].{name:TargetGroupName,lbArns:LoadBalancerArns}' --output json
```

> If the cluster's services run on **FARGATE** rather than EC2, skip the
> EC2-instance and EBS-volume discovery (and the layer-1 disk/host-health
> alarms below) — there's no underlying host to alarm on; Fargate manages
> that layer for you.

---

## Step 2 — Set your variables

Fill these in from Step 1's output before running Step 3.

```bash
REGION="<REGION>"                                            # e.g. ap-south-1
SNS_TOPIC="<SNS_TOPIC_ARN>"                                   # from Prerequisites
CLUSTER_NAME="<CLUSTER_NAME>"                                 # e.g. sgm-development-cluster
INSTANCE_ID="<INSTANCE_ID>"                                   # EC2-launch-type clusters only
VOLUME_ID="<VOLUME_ID>"                                       # EC2-launch-type clusters only
LB_DIM="<app/LOAD-BALANCER-NAME/ID>"                          # from describe-target-groups

# One entry per service: service name -> "targetgroup/NAME/ID"
declare -A TG=(
  ["<service-1>"]="targetgroup/<name-1>/<id-1>"
  ["<service-2>"]="targetgroup/<name-2>/<id-2>"
)
```

---

## Step 3 — Create the 16 alarms

### Layer 1 — Infra (4 alarms)

**CPU** — `CPUUtilization` (AWS/ECS), cluster-wide. Fires if average cluster
CPU exceeds 80% for 5 minutes.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME CPU Spike" \
  --namespace "AWS/ECS" \
  --metric-name CPUUtilization \
  --dimensions Name=ClusterName,Value=$CLUSTER_NAME \
  --statistic Average --period 300 --evaluation-periods 1 \
  --threshold 80 --comparison-operator GreaterThanThreshold \
  --treat-missing-data missing \
  --alarm-actions $SNS_TOPIC --region $REGION
```

**Memory** — `MemoryUtilization` (AWS/ECS), same shape as CPU.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME Memory Spike" \
  --namespace "AWS/ECS" \
  --metric-name MemoryUtilization \
  --dimensions Name=ClusterName,Value=$CLUSTER_NAME \
  --statistic Average --period 300 --evaluation-periods 1 \
  --threshold 80 --comparison-operator GreaterThanThreshold \
  --treat-missing-data missing \
  --alarm-actions $SNS_TOPIC --region $REGION
```

**Disk** — `VolumeIOPSExceededCheck` (AWS/EBS). This is a **binary check
metric** (0 or 1 per datapoint), not a percentage — use `Maximum` +
`threshold 0`, not `Average` + `threshold 80` (that combination is nearly
impossible to trigger and silently fails to catch real throttling).

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME Disk Spike" \
  --namespace "AWS/EBS" \
  --metric-name VolumeIOPSExceededCheck \
  --dimensions Name=VolumeId,Value=$VOLUME_ID Name=InstanceId,Value=$INSTANCE_ID \
  --statistic Maximum --period 300 --evaluation-periods 1 \
  --threshold 0 --comparison-operator GreaterThanThreshold \
  --treat-missing-data missing \
  --alarm-actions $SNS_TOPIC --region $REGION
```

**Host health** — `StatusCheckFailed` (AWS/EC2). AWS's own "is this host
actually dead" signal — catches failures CPU/memory/disk won't (e.g. a
network partition where resource usage looks fine).

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME EC2 Instance Health" \
  --namespace "AWS/EC2" \
  --metric-name StatusCheckFailed \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID \
  --statistic Maximum --period 300 --evaluation-periods 2 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions $SNS_TOPIC --region $REGION
```

### Layer 2 — Container/orchestration: target group health (1 per service)

`UnHealthyHostCount` (AWS/ApplicationELB) — the ALB's own health-check
verdict. Often fires *before* CPU/memory even spike, since a task can be
unhealthy for reasons unrelated to resource usage (crashed process, failed
health-check endpoint, bad deploy).

```bash
for svc in "${(k@)TG}"; do
  aws cloudwatch put-metric-alarm \
    --alarm-name "$CLUSTER_NAME $svc Target Unhealthy" \
    --namespace "AWS/ApplicationELB" \
    --metric-name UnHealthyHostCount \
    --dimensions Name=LoadBalancer,Value=$LB_DIM Name=TargetGroup,Value=${TG[$svc]} \
    --statistic Maximum --period 60 --evaluation-periods 2 \
    --threshold 0 --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions $SNS_TOPIC --region $REGION
done
```

Short period (60s) and low evaluation count (2) deliberately — target group
health is meant to be a fast signal, unlike resource-usage alarms which can
tolerate longer averaging windows.

### Layer 3 — Application: latency + error rate (2 per service)

**Latency** — `TargetResponseTime` (AWS/ApplicationELB), the metric closest
to "what does a user actually experience."

```bash
for svc in "${(k@)TG}"; do
  aws cloudwatch put-metric-alarm \
    --alarm-name "$CLUSTER_NAME $svc Latency" \
    --namespace "AWS/ApplicationELB" \
    --metric-name TargetResponseTime \
    --dimensions Name=LoadBalancer,Value=$LB_DIM Name=TargetGroup,Value=${TG[$svc]} \
    --statistic Average --period 300 --evaluation-periods 3 \
    --threshold 2 --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions $SNS_TOPIC --region $REGION
done
```

**5xx errors** — `HTTPCode_Target_5XX_Count` (AWS/ApplicationELB), counts
failures from the target itself (not the ALB), i.e. the application is
actually erroring, not just slow.

```bash
for svc in "${(k@)TG}"; do
  aws cloudwatch put-metric-alarm \
    --alarm-name "$CLUSTER_NAME $svc 5xx Errors" \
    --namespace "AWS/ApplicationELB" \
    --metric-name HTTPCode_Target_5XX_Count \
    --dimensions Name=LoadBalancer,Value=$LB_DIM Name=TargetGroup,Value=${TG[$svc]} \
    --statistic Sum --period 300 --evaluation-periods 1 \
    --threshold 5 --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions $SNS_TOPIC --region $REGION
done
```

> **Thresholds above (80% CPU/memory, 2s latency, >5 5xx/5min) are
> reasonable starting defaults, not measured from real traffic.** Tighten
> them once you have a baseline for the specific environment — production
> traffic patterns will differ significantly from dev/staging.

---

## Step 4 — Verify

```bash
aws cloudwatch describe-alarms --region $REGION \
  --query 'MetricAlarms[].AlarmName' --output table
```

You should see all 16 alarms for the cluster. Each delivers to `$SNS_TOPIC`,
so as long as that topic's subscription to `/webhooks/cloudwatch` is
confirmed, every alarm state change will land in `raw_events`.

---

## Step 5 — Test all 16 alarms end-to-end

`set-alarm-state` works identically for any CloudWatch alarm regardless of
its underlying metric, so this is the simplest way to test all 16 in one
pass — it forces each alarm to fire without needing real metric data to
cross a threshold.

**Before running:** make sure your `uvicorn` app + tunnel (ngrok or wherever
it's reachable) are actually up — each of these fires a real SNS
notification through your subscription, and if the endpoint is down they'll
just fail delivery silently rather than erroring here.

```zsh
REGION="ap-south-1"

ALARMS=(
  "Dev CPU Spike"
  "Dev Memory Spike"
  "Dev Disk Spike"
  "Dev EC2 Instance Health"
  "Dev sgm-backend-dev Target Unhealthy"
  "Dev sgm-frontend-dev Target Unhealthy"
  "Dev sgm-frontend-dev-v2 Target Unhealthy"
  "Dev sgm-admin-dev Target Unhealthy"
  "Dev sgm-backend-dev Latency"
  "Dev sgm-frontend-dev Latency"
  "Dev sgm-frontend-dev-v2 Latency"
  "Dev sgm-admin-dev Latency"
  "Dev sgm-backend-dev 5xx Errors"
  "Dev sgm-frontend-dev 5xx Errors"
  "Dev sgm-frontend-dev-v2 5xx Errors"
  "Dev sgm-admin-dev 5xx Errors"
)

for name in "${ALARMS[@]}"; do
  echo "Triggering: $name"
  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value ALARM \
    --state-reason "manual test - devops-agent verification" \
    --region $REGION
done
```

**Verify all 16 landed in Postgres:**

```bash
docker exec devops-agent-postgres-1 psql -U devops_agent -d devops_agent -c "
SELECT payload->>'AlarmName' AS alarm_name, received_at
FROM raw_events
WHERE source = 'cloudwatch' AND received_at > now() - interval '10 minutes'
ORDER BY received_at DESC;
"
```

You should see 16 rows, one per alarm name above.

**Reset back to OK afterward** (so the alarms aren't left showing a false
"in alarm" state in the console):

```zsh
for name in "${ALARMS[@]}"; do
  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value OK \
    --state-reason "reset after manual test" \
    --region $REGION
done
```

This reset is also just a forced override — on its next real scheduled
evaluation, CloudWatch recomputes each alarm's actual state from real
metric data regardless, so this only matters for avoiding a stale display
in the meantime.

---

## Not covered here (deferred)

- **Data/dependency layer** — RDS connections/replication lag, ElastiCache
  Redis metrics (native, if Redis is AWS-managed), and self-hosted
  Elasticsearch (needs the CloudWatch agent installed first — it's not an
  AWS-managed service and has no native CloudWatch metrics).
- **Synthetic/business layer** — uptime checks / synthetic transactions.
- **FARGATE-specific host metrics** — this runbook's layer-1 disk/host-health
  alarms assume EC2 launch type; Fargate clusters skip those two.
- **Log-based alarms (CloudWatch Logs Metric Filters)** — deferred, revisit
  later. Everything above is metric-based; none of it looks inside what the
  app actually logs, so caught/handled errors that never breach a metric
  threshold go unnoticed. Sentry (wired into Express's error middleware)
  covers unhandled HTTP-request-cycle exceptions, but pg-boss job workers,
  Redis's `on('error')` listener, and queue reconnect logic all run outside
  Express entirely — Sentry likely never sees them. Codebase search (2026-07-26)
  found these real, ungrepped-for-yet patterns in `/ecs/sgm-backend-dev`,
  prioritized by severity:

  **Tier 1 — critical background-infra failures:**
  | Pattern | Source |
  |---|---|
  | `"shutting down gracefully"` | `queues/queue.js:50` — all pg-boss reconnects failed, app about to exit |
  | `"[PG-BOSS] lost connection"` | `queues/queue.js:33` |
  | `"❌ Redis Error"` / `"❌ Redis Sub Error"` | `config/redis.js:52,59` |
  | `"Failed to start listener"` | `services/cache-subscription-service.js:30` |

  **Tier 2 — silent business-logic failures (caught, never rethrown):**
  | Pattern | Source |
  |---|---|
  | `"Error fetching payment status from network"` | `controllers/order-controller.js:56` |
  | `"Track failed for Order"` | Ainsoft + Nasex tracking workers (matches both) |
  | `"[WEBHOOK-In-Dispatcher]"` | `integration/webhooks/dispatcher.js:34` |

  **Tier 3 — webhook signature failures (security-relevant, prefer alerting on a spike over a single occurrence):**
  `"invalid signature"`, `"NETWORK_WEBHOOK_SECRET"`, `"Network-Decrypt] Failed"` in `integration/webhooks/verifiers.js`

  Mechanism when picked back up: CloudWatch Logs Metric Filter on the log
  group → turns pattern matches into a custom metric → alarm on that metric
  via the same `put-metric-alarm` pattern used throughout this doc, same
  `devops-agent-alerts` SNS topic, no new pipeline needed.

---

## Worked example: `sgm-development-cluster`

Real values this runbook was first run against, for reference:

- **Region**: `ap-south-1`
- **SNS topic**: `arn:aws:sns:ap-south-1:376129878424:devops-agent-alerts`
- **Cluster**: `sgm-development-cluster`
- **EC2 instance**: `i-075d4878cffb36797`
- **EBS volume**: `vol-0d7ac883b8fc21530`
- **Load balancer**: `app/sgm-alb-mumbai/506663c423928032`
- **Services / target groups**:
  - `sgm-backend-dev` → `targetgroup/sgm-dev-backend-mb-01/67a53f62eee32383`
  - `sgm-frontend-dev` → `targetgroup/sgm-dev-frontend-mb-01/a5fbd03416ef7e38`
  - `sgm-frontend-dev-v2` → `targetgroup/sgm-dev-frontend-v2-mb-01/ce8b58a8b0901f92`
  - `sgm-admin-dev` → `targetgroup/sgm-dev-admin-mb-01/bedd928006d33b37`

Alarms use `Dev <name>` naming for this cluster (predates this runbook's
`$CLUSTER_NAME`-prefixed convention above). `Dev Disk Spike` was corrected
on 2026-07-26 from a non-functional `Average > 80` config to the `Maximum >
0` config documented in Step 3.

**Status as of 2026-07-26: 16 of 16 created and verified.**

| Alarm | Status |
|---|---|
| `Dev CPU Spike` | ✅ |
| `Dev Memory Spike` | ✅ |
| `Dev Disk Spike` | ✅ (corrected) |
| `Dev EC2 Instance Health` | ✅ |
| `Dev {4 services} Target Unhealthy` | ✅ all 4 |
| `Dev {4 services} Latency` | ✅ all 4 |
| `Dev {4 services} 5xx Errors` | ✅ all 4 |

Confirmed via:
```bash
aws cloudwatch describe-alarms --region ap-south-1 --alarm-name-prefix "Dev" \
  --query 'length(MetricAlarms)' --output text
# -> 16
```

Full 3-layer coverage (infra, container/orchestration, application) for
`sgm-development-cluster` is complete. Next: repeat this runbook for
`sgm-staging-cluster` and `sgm-production-cluster` when ready, and see
"Not covered here" above for the data/dependency and synthetic layers still
deferred.
