# RDS/Aurora PostgreSQL Monitoring — Alarm Setup Runbook

How to configure, verify, and test CloudWatch alarms for an **Aurora PostgreSQL Serverless v2** database.

This document is intentionally independent from the ECS/container/application monitoring runbook.

It covers only the **database/data layer**:

- Database connections
- CPU utilization
- Freeable memory
- Serverless v2 ACU capacity

Follow this document when setting up RDS/Aurora monitoring for a new environment such as `dev`, `stag`, or `production`.

---

## 1. What This Runbook Covers

For each Aurora PostgreSQL Serverless v2 cluster, configure four alarms:

| Alarm | CloudWatch Metric | Purpose |
|---|---|---|
| Database Connections | `DatabaseConnections` | Detect connection exhaustion |
| CPU | `CPUUtilization` | Detect sustained database CPU pressure |
| Freeable Memory | `FreeableMemory` | Detect low available memory |
| Serverless v2 Capacity | `ServerlessDatabaseCapacity` | Detect sustained ACU pressure |

Each alarm sends both:

- `ALARM` notifications
- `OK` / recovery notifications

to the configured SNS topic.

The SNS topic is expected to be connected to the application's CloudWatch webhook.

---

# 2. Architecture

The RDS monitoring flow is:

```text
Aurora PostgreSQL
      |
      | CloudWatch metrics
      v
CloudWatch Alarm
      |
      | ALARM / OK
      v
SNS Topic
      |
      v
CloudWatch Webhook
      |
      v
raw_events
      |
      v
Celery
      |
      v
RDS Diagnosis Agent
      |
      v
Diagnosis
      |
      v
Incident
      |
      v
Slack
```

The alarms are responsible only for detecting database conditions and publishing SNS notifications.

The DevOps agent processes the notification after it reaches the webhook.

---

# 3. Why These Four Metrics?

Aurora Serverless v2 behaves differently from a traditional fixed-capacity RDS instance.

The database automatically scales compute capacity using **Aurora Capacity Units (ACUs)**.

Therefore, this monitoring setup focuses on:

```text
Connections
CPU
Memory
ACU capacity
```

---

## 3.1 Database Connections

Metric:

```text
AWS/RDS
DatabaseConnections
```

This detects a database approaching connection exhaustion.

If the database reaches its connection limit:

```text
Application
    |
    v
Database connection request
    |
    X
Connection rejected
```

This can result in API failures, worker failures, and application-wide errors.

---

## 3.2 CPU Utilization

Metric:

```text
AWS/RDS
CPUUtilization
```

This detects sustained CPU pressure on the Aurora instance.

High CPU can indicate:

* Expensive queries
* Inefficient queries
* Increased traffic
* Missing indexes
* Background jobs consuming database resources
* Unexpected workload spikes

---

## 3.3 Freeable Memory

Metric:

```text
AWS/RDS
FreeableMemory
```

This measures the amount of memory available to the database instance.

Unlike CPU utilization, this metric is measured in **bytes**.

For example:

```text
200 MB = 200000000 bytes
```

Low freeable memory can indicate that the database is under memory pressure.

---

## 3.4 Serverless v2 Capacity

Metric:

```text
AWS/RDS
ServerlessDatabaseCapacity
```

This is particularly important for Aurora Serverless v2.

It represents the current ACU capacity being used by the database.

For example, if the cluster is configured as:

```text
Minimum ACU: 0.5
Maximum ACU: 2.0
```

and the database repeatedly reaches:

```text
1.8 ACU
1.9 ACU
2.0 ACU
```

the workload may be approaching the configured capacity ceiling.

---

# 4. Prerequisites

Before creating the alarms, make sure you have:

1. AWS CLI installed.
2. AWS CLI authenticated against the correct AWS account.
3. Correct AWS region.
4. Aurora PostgreSQL cluster identifier.
5. Aurora writer instance identifier.
6. SNS topic ARN.
7. Correct environment name.

Supported environment values are:

```text
dev
stag
production
```

The environment value is important because the RDS diagnosis agent uses the alarm's `environment` tag to determine which application database configuration it should use.

---

# 5. Verify AWS Authentication

Run:

```bash
aws sts get-caller-identity
```

Make sure the returned AWS account is the account where the Aurora cluster exists.

---

# 6. Set the Environment Variables

Set the variables for the environment you are configuring.

Example:

```bash
REGION="ap-south-1"

ENVIRONMENT="dev"

SNS_TOPIC="arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts"

DB_CLUSTER_IDENTIFIER="my-cluster-dev"

DB_INSTANCE_IDENTIFIER="my-cluster-dev-instance-1"
```

For another environment, change the values accordingly.

For example:

```bash
ENVIRONMENT="stag"
```

or:

```bash
ENVIRONMENT="production"
```

---

# 7. Discover Aurora PostgreSQL Clusters

Before creating alarms, verify the Aurora clusters available in the region.

```bash
aws rds describe-db-clusters \
  --region "$REGION" \
  --query 'DBClusters[?Engine==`aurora-postgresql`].{
    ClusterId:DBClusterIdentifier,
    Engine:Engine,
    EngineVersion:EngineVersion,
    ServerlessV2Scaling:ServerlessV2ScalingConfiguration,
    Members:DBClusterMembers[].{
      Instance:DBInstanceIdentifier,
      Writer:IsClusterWriter
    }
  }' \
  --output json
```

Record:

```text
DB_CLUSTER_IDENTIFIER
DB_INSTANCE_IDENTIFIER
MinCapacity
MaxCapacity
```

---

# 8. Verify the Writer Instance

Run:

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier "$DB_CLUSTER_IDENTIFIER" \
  --region "$REGION" \
  --query 'DBClusterMembers[].{
    Instance:DBInstanceIdentifier,
    Writer:IsClusterWriter
  }' \
  --output table
```

The writer should show:

```text
Writer = True
```

The following instance-level metrics use the writer instance:

* `DatabaseConnections`
* `CPUUtilization`
* `FreeableMemory`

---

# 9. Check Serverless v2 Scaling Configuration

Run:

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier "$DB_CLUSTER_IDENTIFIER" \
  --region "$REGION" \
  --query 'DBClusters[0].ServerlessV2ScalingConfiguration' \
  --output json
```

Example:

```json
{
  "MinCapacity": 0.5,
  "MaxCapacity": 2.0
}
```

The maximum ACU is important when configuring the Serverless capacity alarm.

---

# 10. Alarm Configuration

Configure these four alarms:

```text
1. Database Connections
2. CPU
3. Freeable Memory
4. Serverless v2 ACU Capacity
```

Every alarm must include:

```text
environment=<ENVIRONMENT>
```

and both:

```bash
--alarm-actions $SNS_TOPIC
--ok-actions $SNS_TOPIC
```

This ensures both problem and recovery events reach the DevOps pipeline.

---

# 11. Alarm 1 — Database Connections

## Metric

```text
Namespace:
AWS/RDS

Metric:
DatabaseConnections
```

This alarm monitors the number of active database connections on the writer instance.

### Initial threshold

```text
> 50 connections
```

This is only a starting value.

The correct threshold should eventually be based on the actual `max_connections` value of the database.

## Create the alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$ENVIRONMENT Aurora Connections" \
  --namespace "AWS/RDS" \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=$DB_INSTANCE_IDENTIFIER \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 50 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=$ENVIRONMENT \
  --alarm-actions $SNS_TOPIC \
  --ok-actions $SNS_TOPIC \
  --region $REGION
```

This means:

```text
Average connections > 50
for 2 consecutive 5-minute periods
        |
        v
      ALARM
```

---

# 12. Verify Database Connection Limits

The threshold of `50` should not be treated as a permanent value.

Connect to the Aurora PostgreSQL database and run:

```sql
SHOW max_connections;
```

For example:

```text
max_connections
---------------
100
```

A better alarm threshold could then be approximately:

```text
80 connections
```

or another value based on the application's normal workload.

The objective is to detect connection pressure **before** the database reaches the actual connection limit.

---

# 13. Alarm 2 — CPU Utilization

## Metric

```text
Namespace:
AWS/RDS

Metric:
CPUUtilization
```

This detects sustained CPU pressure.

### Initial threshold

```text
80%
```

## Create the alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$ENVIRONMENT Aurora CPU Spike" \
  --namespace "AWS/RDS" \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=$DB_INSTANCE_IDENTIFIER \
  --statistic Average \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data missing \
  --tags Key=environment,Value=$ENVIRONMENT \
  --alarm-actions $SNS_TOPIC \
  --ok-actions $SNS_TOPIC \
  --region $REGION
```

This means:

```text
Average CPU > 80%
for 5 minutes
        |
        v
      ALARM
```

---

# 14. Alarm 3 — Freeable Memory

## Metric

```text
Namespace:
AWS/RDS

Metric:
FreeableMemory
```

Unlike CPU, this metric is measured in bytes.

For the current development Aurora cluster, an initial threshold of approximately:

```text
200 MB
```

is used.

In bytes:

```text
200 MB = 200000000
```

## Create the alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$ENVIRONMENT Aurora Low Memory" \
  --namespace "AWS/RDS" \
  --metric-name FreeableMemory \
  --dimensions Name=DBInstanceIdentifier,Value=$DB_INSTANCE_IDENTIFIER \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 200000000 \
  --comparison-operator LessThanThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=$ENVIRONMENT \
  --alarm-actions $SNS_TOPIC \
  --ok-actions $SNS_TOPIC \
  --region $REGION
```

This means:

```text
Average FreeableMemory < 200 MB
for 2 consecutive 5-minute periods
        |
        v
      ALARM
```

---

# 15. Alarm 4 — Serverless v2 ACU Capacity

## Metric

```text
Namespace:
AWS/RDS

Metric:
ServerlessDatabaseCapacity
```

This is a **cluster-level** metric.

Therefore, use:

```text
DBClusterIdentifier
```

rather than:

```text
DBInstanceIdentifier
```

---

## Example

Suppose the Aurora cluster is configured as:

```text
Minimum ACU: 0.5
Maximum ACU: 2.0
```

An initial alarm threshold could be:

```text
1.8 ACU
```

This provides some warning before the database reaches its configured maximum.

---

## Create the alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$ENVIRONMENT Aurora ACU Ceiling" \
  --namespace "AWS/RDS" \
  --metric-name ServerlessDatabaseCapacity \
  --dimensions Name=DBClusterIdentifier,Value=$DB_CLUSTER_IDENTIFIER \
  --statistic Average \
  --period 300 \
  --evaluation-periods 3 \
  --threshold 1.8 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=$ENVIRONMENT \
  --alarm-actions $SNS_TOPIC \
  --ok-actions $SNS_TOPIC \
  --region $REGION
```

This means:

```text
Average ACU >= 1.8
for 3 consecutive 5-minute periods
        |
        v
      ALARM
```

---

# 16. Important — ACU Threshold Depends on Max Capacity

The `1.8` threshold above is only suitable for a cluster whose maximum capacity is approximately:

```text
2.0 ACU
```

For another cluster, calculate the threshold from its configured maximum.

Example:

| Max ACU | Example Warning Threshold |
| ------: | ------------------------: |
|     2.0 |                       1.8 |
|     4.0 |                       3.6 |
|     8.0 |                       7.2 |
|    16.0 |                      14.4 |

A reasonable starting point is approximately:

```text
90% of Max ACU
```

This should eventually be tuned against real workload behavior.

---

# 17. How Long Until You're Actually Notified

Each alarm's `--period`/`--evaluation-periods` sets a minimum evaluation
window before the alarm can even transition to `ALARM` — this is not
optional overhead, it's what prevents a single noisy datapoint from firing
a false alarm.

| Alarm       | Period | Evaluation periods | Minimum evaluation window |
| ----------- | -----: | ------------------: | -------------------------: |
| CPU Spike   |  5 min |                    1 |                     ~5 min |
| Connections |  5 min |                    2 |                    ~10 min |
| Low Memory  |  5 min |                    2 |                    ~10 min |
| ACU Ceiling |  5 min |                    3 |                    ~15 min |

This assumes the underlying condition is sustained continuously through
every period in the window — a spike that lasts 2 minutes and then drops
won't breach even a single 5-minute period.

The evaluation window is only part of the real end-to-end latency:

```text
Spike starts
     │
     ▼
CloudWatch publishes the breaching datapoint(s)
     │   (the evaluation window above, plus CloudWatch's own
     │    publish lag -- typically another minute or two beyond
     │    the period itself, not something this project controls)
     ▼
Alarm transitions to ALARM
     │
     ▼
SNS delivers the notification            (seconds)
     │
     ▼
Webhook stores raw_event, queues Celery  (near-instant)
     │
     ▼
gather_context → retrieve_similar_incidents
→ investigate_further → diagnose         (~15-30 seconds, observed)
     │
     ▼
Slack message posted
```

For CPU Spike specifically (the fastest-configured alarm), realistic total
latency from spike onset to a Slack message is closer to **~6-8 minutes**,
not the ~5 minutes the evaluation window alone suggests. For ACU Ceiling,
closer to **~16-18 minutes**.

`--evaluation-periods` (and `--period`, if the underlying metric supports
finer granularity) is the lever for trading detection speed against false
positives. CPU Spike is deliberately set to fire on a single period because
CPU spikes are usually worth reacting to fast; ACU Ceiling's 3-period
requirement is deliberate too, since Aurora Serverless v2's normal
scale-up/down behavior would otherwise trip it on noise.

---

# 18. Why There Is No `FreeStorageSpace` Alarm

Aurora storage behaves differently from a traditional fixed-size EBS-backed database volume.

Aurora storage automatically scales as data grows.

Therefore, this monitoring setup does **not** create:

```text
FreeStorageSpace
```

alarms.

The primary concerns for this Serverless v2 setup are:

```text
Connections
CPU
Memory
ACU capacity
```

---

# 19. Why There Is No `AuroraReplicaLag` Alarm

`AuroraReplicaLag` is useful when the Aurora cluster has reader instances and replication needs to be monitored.

If the cluster has only:

```text
Writer
```

and no:

```text
Reader
```

there is no replica lag to monitor.

Check the cluster members:

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier "$DB_CLUSTER_IDENTIFIER" \
  --region "$REGION" \
  --query 'DBClusters[0].DBClusterMembers[].{
    Instance:DBInstanceIdentifier,
    Writer:IsClusterWriter
  }' \
  --output table
```

If readers are introduced later, reconsider adding replica-lag monitoring.

---

# 20. Verify All Four Alarms

Run:

```bash
aws cloudwatch describe-alarms \
  --region "$REGION" \
  --alarm-name-prefix "$ENVIRONMENT Aurora" \
  --query 'MetricAlarms[].{
    Name:AlarmName,
    State:StateValue,
    Metric:MetricName
  }' \
  --output table
```

You should see four alarms:

```text
$ENVIRONMENT Aurora Connections
$ENVIRONMENT Aurora CPU Spike
$ENVIRONMENT Aurora Low Memory
$ENVIRONMENT Aurora ACU Ceiling
```

---

# 21. Verify Alarm Configuration

To inspect the complete configuration:

```bash
aws cloudwatch describe-alarms \
  --region "$REGION" \
  --alarm-name-prefix "$ENVIRONMENT Aurora" \
  --output json
```

Verify each alarm has:

```text
AlarmActions
OKActions
```

and that both point to:

```text
$SNS_TOPIC
```

Also verify the environment tag.

---

# 22. Verify the Environment Tag

The RDS diagnosis agent uses the alarm's `environment` tag to determine which application database it should connect to.

Supported values are:

```text
dev
stag
production
```

Do not use:

```text
staging
prod
development
```

unless the application configuration explicitly supports those values.

For development, the alarm should contain:

```text
Key: environment
Value: dev
```

---

# 23. Test the Four Alarms End-to-End

CloudWatch allows us to manually force an alarm into the `ALARM` state.

This allows us to test the complete pipeline without intentionally creating a real database problem.

Before testing, make sure:

* FastAPI/Uvicorn is running.
* The webhook endpoint is reachable.
* SNS subscription is confirmed.
* Celery worker is running.
* Redis is available.
* PostgreSQL used by the DevOps agent is available.

---

# 24. Trigger the Four Alarms

Set the alarm names:

```zsh
REGION="ap-south-1"

RDS_ALARMS=(
  "Dev Aurora Connections"
  "Dev Aurora CPU Spike"
  "Dev Aurora Low Memory"
  "Dev Aurora ACU Ceiling"
)
```

Trigger them:

```zsh
for name in "${RDS_ALARMS[@]}"; do
  echo "Triggering: $name"

  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value ALARM \
    --state-reason "manual test - RDS DevOps agent verification" \
    --region "$REGION"
done
```

This does not create a real database problem.

It only forces the CloudWatch alarm state for testing.

---

# 25. Verify SNS/Webhook Delivery

After triggering the alarms, check the application's `raw_events` table.

Example:

```bash
docker exec devops-agent-postgres-1 \
  psql \
  -U devops_agent \
  -d devops_agent \
  -c "
SELECT
    payload->>'AlarmName' AS alarm_name,
    payload->>'NewStateValue' AS state,
    received_at
FROM raw_events
WHERE source = 'cloudwatch'
  AND received_at > now() - interval '10 minutes'
ORDER BY received_at DESC;
"
```

Expected alarms:

```text
Dev Aurora Connections
Dev Aurora CPU Spike
Dev Aurora Low Memory
Dev Aurora ACU Ceiling
```

You should see four new CloudWatch events.

---

# 26. Verify the Diagnosis Pipeline

Receiving the event in `raw_events` confirms:

```text
CloudWatch
    |
    v
SNS
    |
    v
Webhook
    |
    v
raw_events
```

worked.

To verify the complete DevOps agent pipeline, confirm that the event continues through:

```text
raw_events
    |
    v
Celery
    |
    v
Supervisor
    |
    v
RDS Agent
    |
    v
Gather Context
    |
    v
Similar Incidents
    |
    v
Investigation
    |
    v
Diagnosis
    |
    v
Incident
    |
    v
Slack
```

Check the Celery worker logs:

```text
logs/jobs.log
```

and verify that the resulting diagnosis reaches Slack.

---

# 27. Reset the Alarms to OK

After testing, reset the alarms:

```zsh
for name in "${RDS_ALARMS[@]}"; do
  echo "Resetting: $name"

  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value OK \
    --state-reason "reset after manual RDS alarm test" \
    --region "$REGION"
done
```

This prevents the CloudWatch console from remaining in a manually forced `ALARM` state.

CloudWatch will evaluate the real metric data again.

---

# 28. Verify Recovery Notifications

Because every alarm includes:

```bash
--ok-actions $SNS_TOPIC
```

the reset should generate another SNS notification.

Verify it in `raw_events`:

```bash
docker exec devops-agent-postgres-1 \
  psql \
  -U devops_agent \
  -d devops_agent \
  -c "
SELECT
    payload->>'AlarmName' AS alarm_name,
    payload->>'NewStateValue' AS state,
    received_at
FROM raw_events
WHERE source = 'cloudwatch'
  AND received_at > now() - interval '10 minutes'
ORDER BY received_at DESC;
"
```

You should see both:

```text
ALARM
OK
```

events.

---

# 29. Threshold Tuning

The thresholds in this document are **starting defaults**, not final production values.

They should be tuned after observing real database behavior.

## Initial Values

| Metric               | Initial Threshold |
| -------------------- | ----------------: |
| Database Connections |              > 50 |
| CPU                  |             > 80% |
| Freeable Memory      |          < 200 MB |
| Serverless ACU       |     >= 90% of max |

These values should eventually be based on actual workload and historical CloudWatch metrics.

---

# 30. Recommended Production Tuning Process

## Step 1 — Observe Normal Traffic

Monitor:

```text
DatabaseConnections
CPUUtilization
FreeableMemory
ServerlessDatabaseCapacity
```

during:

* Normal traffic
* Peak traffic
* Scheduled jobs
* Deployments
* Known high-load periods

## Step 2 — Establish a Baseline

Determine:

```text
Normal minimum
Normal average
Normal peak
Abnormal peak
```

## Step 3 — Set Warning Thresholds

Alarm before the system reaches a dangerous state.

For example:

```text
Normal CPU:
30–50%

Warning:
70–80%

Critical:
90%+
```

The exact values depend on the workload.

## Step 4 — Review False Positives

If alarms trigger frequently without an actual incident, tune:

* Threshold
* Evaluation periods
* Period duration

---

# 31. Environment Isolation

Each environment should have a clear relationship between:

```text
CloudWatch Alarm
      |
      v
environment tag
      |
      v
Application DB configuration
```

For example:

```text
Dev alarm
    |
    | environment=dev
    v
dev AppDbConfig
```

```text
Staging alarm
    |
    | environment=stag
    v
stag AppDbConfig
```

```text
Production alarm
    |
    | environment=production
    v
production AppDbConfig
```

The `environment` tag must therefore be correct.

---

# 32. Important Shared-Cluster Limitation

CloudWatch RDS metrics are associated with the RDS instance/cluster, not individual PostgreSQL databases inside the instance.

For example, suppose one Aurora cluster contains:

```text
Shared Aurora Cluster
    |
    +-- dev database
    |
    +-- stag database
```

A CPU or connection alarm is associated with the Aurora instance/cluster.

The alarm cannot determine:

```text
Was this CPU spike caused by dev?
or
Was this CPU spike caused by stag?
```

The RDS diagnosis agent may still connect to one environment based on the alarm's:

```text
environment
```

tag.

This means the agent could investigate the wrong database if multiple environments share the same Aurora cluster.

## Recommended Architecture

Prefer:

```text
Dev
 |
 +--> Aurora cluster/instance

Staging
 |
 +--> Aurora cluster/instance

Production
 |
 +--> Aurora cluster/instance
```

rather than:

```text
Shared Aurora
 |
 +--> dev database
 +--> stag database
```

when environment-specific automated diagnosis is required.

---

# 33. Current Known Limitations

## 32.1 Connection Threshold Is Not Dynamically Calculated

The current alarm uses:

```text
50 connections
```

This should eventually be derived from:

```sql
SHOW max_connections;
```

and tuned according to the application's actual workload.

---

## 32.2 Memory Threshold Is a Starting Point

The current:

```text
200 MB
```

threshold is not universally correct for every Aurora Serverless v2 configuration.

Different ACU ranges have different memory characteristics.

---

## 32.3 ACU Threshold Depends on Max Capacity

The ACU alarm must be adjusted when the cluster's Serverless v2 configuration changes.

For example:

```text
MaxCapacity = 2 ACU
```

may use:

```text
1.8 ACU
```

as a warning threshold.

But:

```text
MaxCapacity = 8 ACU
```

should use a different threshold.

---

## 32.4 No Replica-Lag Monitoring for Writer-Only Clusters

If reader instances are introduced later, add appropriate replica-lag monitoring.

---

## 32.5 No Storage Alarm

Aurora storage is not treated like a fixed EBS volume in this monitoring design.

Storage monitoring can be revisited if the architecture or operational requirements change.

---

# 34. Verification Checklist

Use this checklist when configuring a new environment.

## AWS

* [ ] AWS CLI authenticated
* [ ] Correct AWS account verified
* [ ] Correct AWS region selected
* [ ] Aurora PostgreSQL cluster identified
* [ ] Writer instance identified
* [ ] Serverless v2 MinCapacity identified
* [ ] Serverless v2 MaxCapacity identified
* [ ] SNS topic identified

## Alarm Configuration

* [ ] Database Connections alarm created
* [ ] CPU alarm created
* [ ] Freeable Memory alarm created
* [ ] Serverless ACU alarm created
* [ ] `environment` tag added to every alarm
* [ ] `--alarm-actions` configured
* [ ] `--ok-actions` configured

## End-to-End Testing

* [ ] Connections alarm manually triggered
* [ ] CPU alarm manually triggered
* [ ] Memory alarm manually triggered
* [ ] ACU alarm manually triggered
* [ ] Four ALARM events received by webhook
* [ ] Four events stored in `raw_events`
* [ ] Celery processed the events
* [ ] RDS agent processed the events
* [ ] Incidents created
* [ ] Slack notifications received
* [ ] Alarms reset to OK
* [ ] Recovery events received
* [ ] Recovery events stored in `raw_events`

---

# 35. Quick Setup

For an already-known Aurora Serverless v2 cluster:

```bash
REGION="ap-south-1"
ENVIRONMENT="dev"

SNS_TOPIC="arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts"

DB_CLUSTER_IDENTIFIER="my-cluster-dev"

DB_INSTANCE_IDENTIFIER="my-cluster-dev-instance-1"
```

Create these four alarms:

```text
1. DatabaseConnections
2. CPUUtilization
3. FreeableMemory
4. ServerlessDatabaseCapacity
```

All four must include:

```bash
--tags Key=environment,Value=$ENVIRONMENT
```

and:

```bash
--alarm-actions $SNS_TOPIC
--ok-actions $SNS_TOPIC
```

Then:

```text
Verify
  |
  v
Trigger ALARM
  |
  v
Verify raw_events
  |
  v
Verify Celery / RDS Agent
  |
  v
Verify Slack
  |
  v
Reset to OK
  |
  v
Verify recovery event
```

---

# 36. Final Architecture

```text
                 Aurora PostgreSQL
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
     Connections       CPU         Memory
          |             |             |
          +-------------+-------------+
                        |
                        v
              Serverless v2 ACU
                        |
                        v
               CloudWatch Alarms
                        |
                   ALARM / OK
                        |
                        v
                   SNS Topic
                        |
                        v
              CloudWatch Webhook
                        |
                        v
                   raw_events
                        |
                        v
                     Celery
                        |
                        v
                  RDS Agent
                        |
             +----------+----------+
             |          |          |
             v          v          v
          Context     RAG      Investigation
             |          |          |
             +----------+----------+
                        |
                        v
                    Diagnosis
                        |
                        v
                    Incident
                        |
                        v
                      Slack
```

This document covers the **RDS/Aurora monitoring and alarm configuration only**.

The detailed diagnosis-pipeline behavior is documented separately in:

```text
RDS Diagnosis Pipeline — End to End
```
