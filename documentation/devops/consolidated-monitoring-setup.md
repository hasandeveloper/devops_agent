# CloudWatch Alarm Setup — New Environment Runbook

How to set up the full monitoring coverage for any ECS environment from scratch.

This runbook covers:

1. **ECS infrastructure** — CPU, memory, disk, and EC2 host health
2. **Container/orchestration** — ECS service and load-balancer target health
3. **Application** — latency and HTTP 5xx errors
4. **RDS/Aurora** — database connections, CPU, memory, and Serverless v2 capacity

Follow this document from top to bottom when setting up a new environment such as **dev, staging, or production**.

> **Important:** None of these commands run automatically. Copy the commands into a terminal with AWS CLI authenticated against the target AWS account.

---

# 1. Monitoring Architecture

The monitoring setup has four layers.

| Layer                     | What it monitors                   |        Alarms |
| ------------------------- | ---------------------------------- | ------------: |
| Infrastructure            | ECS CPU, memory, EBS, EC2 health   |             4 |
| Container / Orchestration | ALB target health                  | 1 per service |
| Application               | Application latency and 5xx errors | 2 per service |
| Database                  | Aurora PostgreSQL health           |             4 |

For a cluster with **4 ECS services**, this results in:

* 4 infrastructure alarms
* 4 target-health alarms
* 4 latency alarms
* 4 application 5xx alarms
* 4 Aurora alarms

**Total: 20 alarms**

If the cluster has a different number of services, the container/orchestration and application alarm counts change accordingly.

---

# 2. Prerequisites

## 2.1 AWS CLI authentication

Make sure the AWS CLI is authenticated against the correct AWS account.

```bash
aws sts get-caller-identity
```

Verify that the returned account and identity belong to the environment you are configuring.

---

## 2.2 Shell requirement

This runbook is written for **zsh**, which is the default interactive shell on macOS.

The loops in Step 4 use:

```zsh
"${(k@)TG}"
```

This is zsh's syntax for iterating over the keys of an associative array.

Do **not** replace it with:

```bash
"${!TG[@]}"
```

when running directly in zsh.

The `${(k@)TG}` form is required because:

* `(k)` returns the associative-array keys.
* `@` keeps each key as a separate word.
* Without `@`, quoted zsh expansion can combine the keys into one string.

If this runbook is being adapted for a Linux CI environment using **bash 4+**, the loops can instead use:

```bash
"${!TG[@]}"
```

---

## 2.3 SNS topic

An SNS topic must already exist and be subscribed to the CloudWatch webhook.

The flow should be:

```text
CloudWatch Alarm
      |
      v
     SNS
      |
      v
POST /webhooks/cloudwatch
      |
      v
DevOps Agent
```

Have the SNS topic ARN available before continuing.

If the SNS topic has not been configured yet, first complete the project's CloudWatch webhook setup:

```text
Create SNS topic
      ↓
Subscribe /webhooks/cloudwatch over HTTPS
      ↓
Confirm SNS subscription
```

---

## 2.4 Environment name

Every CloudWatch alarm must have an `environment` tag.

Supported values are:

```text
dev
stag
production
```

For example:

```bash
--tags Key=environment,Value=dev
```

This value is important because the RDS agent uses the alarm's `environment` tag to determine which application database it should connect to.

The lookup is performed by:

```text
app/agents/tools/mcp/rds/mcp_server.py
```

using:

```text
config/settings.py
```

The environment must therefore match one of the values supported by `Settings.app_db_config()`.

### Do not use

```text
staging
prod
```

Use:

```text
stag
production
```

---

## 2.5 One environment per RDS instance

A monitored RDS instance should represent exactly one environment.

For example:

```text
dev      → RDS instance A
stag     → RDS instance B
production → RDS instance C
```

Do not rely on a single RDS instance containing multiple environment databases.

CloudWatch RDS alarms are instance-level or cluster-level metrics. They cannot identify which individual PostgreSQL database caused a CPU, memory, connection, or other database-level issue.

The RDS agent therefore uses the alarm's `environment` tag to determine the database connection.

This becomes particularly important for tools such as:

```text
get_table_bloat
explain_query_for_pid
```

If `dev` and `stag` share one RDS instance, the agent cannot reliably determine which database caused the problem.

For the RDS agent to provide reliable diagnostics:

> **Prefer one environment per RDS instance/cluster.**

See:

```text
documentation/rds-agent/readonly-db-role-setup.md
```

for the database-access details.

---

# 3. Step 1 — Discover Your Resources

Run these commands against the environment being configured.

Replace:

```text
<CLUSTER_NAME>
<REGION>
```

with the actual values.

```bash
CLUSTER_NAME="<CLUSTER_NAME>"
REGION="<REGION>"
```

---

## 3.1 List ECS services

```bash
aws ecs list-services \
  --cluster "$CLUSTER_NAME" \
  --region "$REGION" \
  --output text
```

Record every ECS service.

---

## 3.2 Get service and target-group information

```bash
aws ecs describe-services \
  --cluster "$CLUSTER_NAME" \
  --region "$REGION" \
  --services <service-1> <service-2> ... \
  --query 'services[].{
    name:serviceName,
    launchType:launchType,
    targetGroups:loadBalancers[].targetGroupArn
  }' \
  --output json
```

Record:

* Service name
* Launch type
* Target group ARN

This information will be used when creating the Layer 2 and Layer 3 alarms.

---

## 3.3 Discover ECS EC2 instances

This project uses **EC2 launch type**, not Fargate.

```bash
CI_ARNS=$(aws ecs list-container-instances \
  --cluster "$CLUSTER_NAME" \
  --region "$REGION" \
  --query 'containerInstanceArns' \
  --output text)

aws ecs describe-container-instances \
  --cluster "$CLUSTER_NAME" \
  --container-instances $CI_ARNS \
  --region "$REGION" \
  --query 'containerInstances[].{
    ec2InstanceId:ec2InstanceId,
    status:status
  }' \
  --output json
```

Record the EC2 instance ID if the environment uses a fixed EC2 instance.

---

## 3.4 Discover EBS volumes

For a fixed EC2 instance:

```bash
aws ec2 describe-volumes \
  --filters "Name=attachment.instance-id,Values=<INSTANCE_ID>" \
  --region "$REGION" \
  --query 'Volumes[].{
    VolumeId:VolumeId,
    Size:Size,
    State:State
  }' \
  --output json
```

Record the volume ID.

---

## 3.5 Discover the load balancer

For each target group:

```bash
aws elbv2 describe-target-groups \
  --target-group-arns "<TARGET_GROUP_ARN>" \
  --region "$REGION" \
  --query 'TargetGroups[0].{
    name:TargetGroupName,
    lbArns:LoadBalancerArns
  }' \
  --output json
```

Record:

* Load balancer ARN
* Target group ARN

The CloudWatch dimensions require the shortened load-balancer and target-group identifiers rather than the complete ARNs.

---

## 3.6 Auto Scaling Group environments

If the cluster's EC2 instances are managed by an Auto Scaling Group and the number of instances can change:

**Skip the following infrastructure alarms:**

* Disk
* EC2 host health

The reason is that:

```text
INSTANCE_ID
VOLUME_ID
```

assume a fixed instance and fixed volume.

An Auto Scaling Group can replace instances, making those dimensions obsolete.

The existing Layer 2:

```text
UnHealthyHostCount
```

still provides load-balancer-level health monitoring.

For broader ASG monitoring, a future implementation can alarm on:

```text
GroupInServiceInstances
```

from:

```text
AWS/AutoScaling
```

That is outside this runbook.

---

# 4. Step 2 — Set Your Variables

Fill these variables using the output from Step 1.

```bash
REGION="<REGION>"
SNS_TOPIC="<SNS_TOPIC_ARN>"
ENVIRONMENT="<ENVIRONMENT>"
CLUSTER_NAME="<CLUSTER_NAME>"

INSTANCE_ID="<INSTANCE_ID>"
VOLUME_ID="<VOLUME_ID>"

LB_DIM="<app/LOAD-BALANCER-NAME/ID>"
```

For example:

```bash
REGION="ap-south-1"
SNS_TOPIC="arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts"
ENVIRONMENT="dev"
CLUSTER_NAME="my-cluster-development"

INSTANCE_ID="i-075d4878cffb36797"
VOLUME_ID="vol-0d7ac883b8fc21530"

LB_DIM="app/my-alb/1234567890abcdef"
```

---

## 4.1 Configure ECS services and target groups

Create one entry per ECS service.

```zsh
typeset -A TG=(
  ["<service-1>"]="targetgroup/<name-1>/<id-1>"
  ["<service-2>"]="targetgroup/<name-2>/<id-2>"
  ["<service-3>"]="targetgroup/<name-3>/<id-3>"
  ["<service-4>"]="targetgroup/<name-4>/<id-4>"
)
```

For example:

```zsh
typeset -A TG=(
  ["my-backend-dev"]="targetgroup/my-dev-backend/2222222222222222"
  ["my-frontend-dev"]="targetgroup/my-dev-frontend/3333333333333333"
  ["my-frontend-dev-v2"]="targetgroup/my-dev-frontend-v2/4444444444444444"
  ["my-admin-dev"]="targetgroup/my-dev-admin/1111111111111111"
)
```

---

# 5. Step 3 — Create ECS Alarms

There are three monitoring layers for ECS:

```text
Layer 1 → Infrastructure
Layer 2 → Container / orchestration
Layer 3 → Application
```

---

# 6. Layer 1 — Infrastructure

There are four infrastructure alarms.

| Alarm       | Metric                    | Purpose                      |
| ----------- | ------------------------- | ---------------------------- |
| CPU         | `CPUUtilization`          | Detect high ECS CPU usage    |
| Memory      | `MemoryUtilization`       | Detect high ECS memory usage |
| Disk        | `VolumeIOPSExceededCheck` | Detect EBS IOPS throttling   |
| Host Health | `StatusCheckFailed`       | Detect EC2 host failures     |

---

## 6.1 CPU Alarm

Metric:

```text
AWS/ECS
CPUUtilization
```

The alarm triggers when average CPU usage exceeds 80% for 5 minutes.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME CPU Spike" \
  --namespace "AWS/ECS" \
  --metric-name CPUUtilization \
  --dimensions Name=ClusterName,Value=$CLUSTER_NAME \
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

---

## 6.2 Memory Alarm

Metric:

```text
AWS/ECS
MemoryUtilization
```

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME Memory Spike" \
  --namespace "AWS/ECS" \
  --metric-name MemoryUtilization \
  --dimensions Name=ClusterName,Value=$CLUSTER_NAME \
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

---

## 6.3 Disk / EBS IOPS Alarm

Metric:

```text
AWS/EBS
VolumeIOPSExceededCheck
```

This is a **binary metric**, not a percentage.

The values are effectively:

```text
0 = normal
1 = IOPS exceeded
```

Therefore the correct configuration is:

```text
Statistic: Maximum
Threshold: 0
Comparison: GreaterThanThreshold
```

Do not use:

```text
Average > 80
```

That configuration would be inappropriate for this metric.

> **Skip this alarm for Auto Scaling Group environments** where there is no fixed EBS volume.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME Disk Spike" \
  --namespace "AWS/EBS" \
  --metric-name VolumeIOPSExceededCheck \
  --dimensions \
    Name=VolumeId,Value=$VOLUME_ID \
    Name=InstanceId,Value=$INSTANCE_ID \
  --statistic Maximum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data missing \
  --tags Key=environment,Value=$ENVIRONMENT \
  --alarm-actions $SNS_TOPIC \
  --ok-actions $SNS_TOPIC \
  --region $REGION
```

---

## 6.4 EC2 Host Health Alarm

Metric:

```text
AWS/EC2
StatusCheckFailed
```

This catches EC2 host-level failures that may not appear as CPU, memory, or disk problems.

Examples include:

* Network partition
* Host failure
* Underlying infrastructure failure

> **Skip this alarm for Auto Scaling Group environments** where the instance ID is not fixed.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "$CLUSTER_NAME EC2 Instance Health" \
  --namespace "AWS/EC2" \
  --metric-name StatusCheckFailed \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID \
  --statistic Maximum \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=$ENVIRONMENT \
  --alarm-actions $SNS_TOPIC \
  --ok-actions $SNS_TOPIC \
  --region $REGION
```

---

# 7. Layer 2 — Container / Orchestration

Create one alarm per ECS service.

Metric:

```text
AWS/ApplicationELB
UnHealthyHostCount
```

This is the ALB health-check verdict.

It can detect problems before CPU or memory rises.

Examples:

* Application process crashed
* Health-check endpoint failed
* Bad deployment
* Application unavailable
* Container became unhealthy

The alarm checks every 60 seconds and requires two consecutive unhealthy periods.

```zsh
for svc in "${(k@)TG}"; do
  aws cloudwatch put-metric-alarm \
    --alarm-name "$CLUSTER_NAME $svc Target Unhealthy" \
    --namespace "AWS/ApplicationELB" \
    --metric-name UnHealthyHostCount \
    --dimensions \
      Name=LoadBalancer,Value=$LB_DIM \
      Name=TargetGroup,Value=${TG[$svc]} \
    --statistic Maximum \
    --period 60 \
    --evaluation-periods 2 \
    --threshold 0 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --tags Key=environment,Value=$ENVIRONMENT \
    --alarm-actions $SNS_TOPIC \
    --ok-actions $SNS_TOPIC \
    --region $REGION
done
```

---

# 8. Layer 3 — Application Monitoring

There are two alarms per service:

1. Latency
2. HTTP 5xx errors

For four services, this creates eight alarms.

---

## 8.1 Latency Alarm

Metric:

```text
AWS/ApplicationELB
TargetResponseTime
```

This is the metric closest to:

> "How long does the user have to wait for the application?"

The current starting threshold is:

```text
2 seconds
```

for three consecutive five-minute evaluation periods.

```zsh
for svc in "${(k@)TG}"; do
  aws cloudwatch put-metric-alarm \
    --alarm-name "$CLUSTER_NAME $svc Latency" \
    --namespace "AWS/ApplicationELB" \
    --metric-name TargetResponseTime \
    --dimensions \
      Name=LoadBalancer,Value=$LB_DIM \
      Name=TargetGroup,Value=${TG[$svc]} \
    --statistic Average \
    --period 300 \
    --evaluation-periods 3 \
    --threshold 2 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --tags Key=environment,Value=$ENVIRONMENT \
    --alarm-actions $SNS_TOPIC \
    --ok-actions $SNS_TOPIC \
    --region $REGION
done
```

---

## 8.2 HTTP 5xx Alarm

Metric:

```text
AWS/ApplicationELB
HTTPCode_Target_5XX_Count
```

This counts HTTP 5xx responses generated by the application target.

The current starting threshold is:

```text
More than 5 errors in 5 minutes
```

```zsh
for svc in "${(k@)TG}"; do
  aws cloudwatch put-metric-alarm \
    --alarm-name "$CLUSTER_NAME $svc 5xx Errors" \
    --namespace "AWS/ApplicationELB" \
    --metric-name HTTPCode_Target_5XX_Count \
    --dimensions \
      Name=LoadBalancer,Value=$LB_DIM \
      Name=TargetGroup,Value=${TG[$svc]} \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 5 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --tags Key=environment,Value=$ENVIRONMENT \
    --alarm-actions $SNS_TOPIC \
    --ok-actions $SNS_TOPIC \
    --region $REGION
done
```

---

# 9. Alarm Thresholds

The thresholds in this runbook are **starting defaults**.

| Metric              | Current threshold |
| ------------------- | ----------------: |
| ECS CPU             |             > 80% |
| ECS Memory          |             > 80% |
| EBS IOPS exceeded   |               > 0 |
| EC2 health          |               ≥ 1 |
| Target unhealthy    |               > 0 |
| Application latency |       > 2 seconds |
| Application 5xx     |   > 5 / 5 minutes |

These values should eventually be tuned using real traffic and production baselines.

For example:

```text
Dev:
80% CPU may be acceptable

Production:
80% CPU may already be too high
```

Similarly, a high-traffic service may naturally generate more than five 5xx responses in five minutes, while a low-traffic service may consider a single 5xx significant.

---

# 10. Step 4 — Verify ECS Alarms

Run:

```bash
aws cloudwatch describe-alarms \
  --region $REGION \
  --query 'MetricAlarms[].AlarmName' \
  --output table
```

For a four-service cluster you should see:

```text
4 infrastructure alarms
4 target-health alarms
4 latency alarms
4 5xx alarms
```

Total:

```text
16 ECS alarms
```

Every alarm should have:

```text
AlarmActions → SNS topic
OKActions    → SNS topic
environment  → correct environment
```

---

# 11. Step 5 — Test ECS Alarms End-to-End

`set-alarm-state` can manually force an alarm into the `ALARM` state.

This allows the complete pipeline to be tested without generating actual CPU, memory, latency, or application failures.

> **Before testing:** make sure the FastAPI application and its public tunnel/endpoint are running. These tests generate real SNS notifications.

---

## 11.1 Define alarms

Example:

```zsh
REGION="ap-south-1"

ALARMS=(
  "Dev CPU Spike"
  "Dev Memory Spike"
  "Dev Disk Spike"
  "Dev EC2 Instance Health"
  "Dev my-backend-dev Target Unhealthy"
  "Dev my-frontend-dev Target Unhealthy"
  "Dev my-frontend-dev-v2 Target Unhealthy"
  "Dev my-admin-dev Target Unhealthy"
  "Dev my-backend-dev Latency"
  "Dev my-frontend-dev Latency"
  "Dev my-frontend-dev-v2 Latency"
  "Dev my-admin-dev Latency"
  "Dev my-backend-dev 5xx Errors"
  "Dev my-frontend-dev 5xx Errors"
  "Dev my-frontend-dev-v2 5xx Errors"
  "Dev my-admin-dev 5xx Errors"
)
```

---

## 11.2 Trigger all alarms

```zsh
for name in "${ALARMS[@]}"; do
  echo "Triggering: $name"

  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value ALARM \
    --state-reason "manual test - devops-agent verification" \
    --region $REGION
done
```

---

## 11.3 Verify events in Postgres

```bash
docker exec devops-agent-postgres-1 psql \
  -U devops_agent \
  -d devops_agent \
  -c "
SELECT
  payload->>'AlarmName' AS alarm_name,
  received_at
FROM raw_events
WHERE source = 'cloudwatch'
  AND received_at > now() - interval '10 minutes'
ORDER BY received_at DESC;
"
```

For a four-service environment, you should see:

```text
16 rows
```

---

## 11.4 Reset alarms to OK

After testing:

```zsh
for name in "${ALARMS[@]}"; do
  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value OK \
    --state-reason "reset after manual test" \
    --region $REGION
done
```

The reset is only a temporary state override. CloudWatch will eventually evaluate the real metric and determine the actual alarm state.

---

# 12. Step 6 — Aurora PostgreSQL Monitoring

The project's databases use:

```text
Aurora PostgreSQL
Aurora Serverless v2
```

This changes which metrics are useful.

---

## 12.1 Why some traditional RDS alarms are not used

### FreeStorageSpace

Not used because Aurora storage automatically scales.

Aurora can scale storage significantly without requiring manual EBS volume management.

Therefore:

```text
FreeStorageSpace
```

is not treated as a primary failure signal here.

---

### AuroraReplicaLag

Not currently used because the monitored Aurora clusters have no reader instances.

Replica lag only becomes relevant when reader instances are introduced.

---

### ServerlessDatabaseCapacity

This is important for Aurora Serverless v2.

Instead of thinking only about CPU:

```text
CPU → EC2
```

for Aurora Serverless v2 we also need to monitor:

```text
ACU → Aurora Serverless capacity
```

If the database remains close to its maximum ACU, it may be requesting more compute than the configured maximum allows.

---

# 13. Discover Aurora Clusters

Run:

```bash
aws rds describe-db-clusters \
  --region ap-south-1 \
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

Example clusters previously discovered:

```text
my-cluster-dev
my-serverless-db
```

The first cluster has been configured with alarms.

The second cluster requires confirmation of its purpose before applying the same environment-specific configuration.

---

# 14. Aurora Alarm Coverage

The current Aurora setup contains four alarms:

| Alarm       | Metric                       | Purpose                      |
| ----------- | ---------------------------- | ---------------------------- |
| Connections | `DatabaseConnections`        | Detect connection exhaustion |
| CPU         | `CPUUtilization`             | Detect high database CPU     |
| Memory      | `FreeableMemory`             | Detect low available memory  |
| ACU         | `ServerlessDatabaseCapacity` | Detect capacity ceiling      |

---

# 15. Aurora Connections Alarm

Metric:

```text
AWS/RDS
DatabaseConnections
```

This is monitored against the writer instance.

The current starting threshold is:

```text
> 50 connections
```

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Dev Aurora Connections" \
  --namespace "AWS/RDS" \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=my-cluster-dev-instance-1 \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 50 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=dev \
  --alarm-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --ok-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --region ap-south-1
```

> **Important:** `50` is only a starting value. Aurora's actual `max_connections` depends on available memory/ACU. Check the actual value with:
>
> ```sql
> SHOW max_connections;
> ```
>
> Then consider alarming around 80% of the real limit.

---

# 16. Aurora CPU Alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Dev Aurora CPU Spike" \
  --namespace "AWS/RDS" \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=my-cluster-dev-instance-1 \
  --statistic Average \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data missing \
  --tags Key=environment,Value=dev \
  --alarm-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --ok-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --region ap-south-1
```

---

# 17. Aurora Low-Memory Alarm

Metric:

```text
AWS/RDS
FreeableMemory
```

This metric reports bytes, not a percentage.

The current starting threshold is:

```text
200 MB
```

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Dev Aurora Low Memory" \
  --namespace "AWS/RDS" \
  --metric-name FreeableMemory \
  --dimensions Name=DBInstanceIdentifier,Value=my-cluster-dev-instance-1 \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 200000000 \
  --comparison-operator LessThanThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=dev \
  --alarm-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --ok-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --region ap-south-1
```

---

# 18. Aurora Serverless ACU Ceiling Alarm

Metric:

```text
AWS/RDS
ServerlessDatabaseCapacity
```

Unlike the previous database alarms, this is dimensioned using:

```text
DBClusterIdentifier
```

because ACU is a cluster-level scaling metric.

For the current cluster:

```text
Minimum ACU = 0.5
Maximum ACU = 2.0
```

The alarm is configured at:

```text
1.8 ACU
```

which represents approximately 90% of the maximum.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Dev Aurora ACU Ceiling" \
  --namespace "AWS/RDS" \
  --metric-name ServerlessDatabaseCapacity \
  --dimensions Name=DBClusterIdentifier,Value=my-cluster-dev \
  --statistic Average \
  --period 300 \
  --evaluation-periods 3 \
  --threshold 1.8 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --tags Key=environment,Value=dev \
  --alarm-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --ok-actions arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts \
  --region ap-south-1
```

---

# 19. Test Aurora Alarms

Define the alarms:

```zsh
REGION="ap-south-1"

RDS_ALARMS=(
  "Dev Aurora Connections"
  "Dev Aurora CPU Spike"
  "Dev Aurora Low Memory"
  "Dev Aurora ACU Ceiling"
)
```

---

## 19.1 Trigger the alarms

```zsh
for name in "${RDS_ALARMS[@]}"; do
  echo "Triggering: $name"

  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value ALARM \
    --state-reason "manual test - devops-agent verification" \
    --region $REGION
done
```

---

## 19.2 Verify events

```bash
docker exec devops-agent-postgres-1 psql \
  -U devops_agent \
  -d devops_agent \
  -c "
SELECT
  payload->>'AlarmName' AS alarm_name,
  received_at
FROM raw_events
WHERE source = 'cloudwatch'
  AND received_at > now() - interval '10 minutes'
ORDER BY received_at DESC;
"
```

You should see the four Aurora alarm events.

---

## 19.3 Reset alarms

```zsh
for name in "${RDS_ALARMS[@]}"; do
  aws cloudwatch set-alarm-state \
    --alarm-name "$name" \
    --state-value OK \
    --state-reason "reset after manual test" \
    --region $REGION
done
```

---

# 20. CloudWatch → SNS → DevOps Agent Verification

The complete expected flow is:

```text
CloudWatch Alarm
       |
       | ALARM / OK
       v
      SNS
       |
       | HTTPS
       v
POST /webhooks/cloudwatch
       |
       v
FastAPI webhook
       |
       v
raw_events
       |
       v
Celery
       |
       v
RDS / ECS Agent
```

The important point is that both alarm states should be delivered:

```text
ALARM → SNS → webhook
OK    → SNS → webhook
```

This is why every alarm command contains both:

```bash
--alarm-actions $SNS_TOPIC
--ok-actions $SNS_TOPIC
```

---

# 21. Changelog — Resource ID Fix

**Date: 2026-07-26**

Testing the alarm pipeline exposed a problem where `resource_id` was `NULL` for three alarm types:

```text
Dev Disk Spike
Dev EC2 Instance Health
Dev Aurora ACU Ceiling
```

The problem was caused by `_RESOURCE_DIMENSION_NAMES` in:

```text
app/controllers/webhooks.py
```

The following dimensions were missing:

```text
InstanceId
VolumeId
DBClusterIdentifier
```

They were added to the supported resource-dimension set.

The existing NULL rows were also backfilled directly in SQL using the same first-matching-dimension logic.

### Result

New alarms created with this runbook correctly populate:

```text
resource_id
```

for these resource types.

---

# 22. Changelog — Recovery Signals

**Date: 2026-07-26**

Testing also revealed that alarm recovery notifications were not being received.

The reason was that existing alarms had no:

```text
OKActions
```

Therefore only:

```text
ALARM
```

events were being delivered.

All 20 existing alarms were updated to include:

```text
--ok-actions
```

using the same SNS topic as the alarm action.

All commands in this runbook now include both:

```bash
--alarm-actions
--ok-actions
```

### Result

The pipeline now receives both:

```text
ALARM
OK
```

notifications.

---

# 23. Changelog — Environment Tag

**Date: 2026-08-15**

Every alarm now includes:

```bash
--tags Key=environment,Value=$ENVIRONMENT
```

This is required by the RDS agent.

The agent's:

```text
get_alarm_environment
```

tool reads this tag and uses it to determine which application database configuration to use.

Without the tag, the RDS agent cannot reliably determine the target database.

### Existing alarms

The four existing dev Aurora alarms were checked using:

```bash
aws rds list-tags-for-resource
```

and were confirmed to already have:

```text
environment=dev
```

Therefore no production infrastructure backfill was required.

The problem was in the documentation/setup process, not the live dev configuration.

---

# 24. Not Covered

The following monitoring areas are intentionally deferred.

---

## 24.1 ElastiCache Redis

Native AWS CloudWatch metrics exist for ElastiCache Redis.

They are not currently configured in this runbook.

Potential future metrics include:

```text
CPUUtilization
FreeableMemory
CurrConnections
Evictions
CacheHitRate
```

---

## 24.2 Self-hosted Elasticsearch

The project's Elasticsearch is self-hosted.

It does not automatically provide the same AWS-managed CloudWatch metrics available for services such as:

```text
RDS
ElastiCache
ALB
ECS
```

A CloudWatch agent would need to be installed/configured first.

---

## 24.3 Synthetic / Business Monitoring

Not currently covered:

```text
Uptime checks
Synthetic transactions
Business workflows
Checkout flow monitoring
Login monitoring
Payment workflow monitoring
```

These should eventually be handled by synthetic/business monitoring.

---

## 24.4 Auto Scaling Group Monitoring

Production environments may use an Auto Scaling Group.

A fixed:

```text
INSTANCE_ID
VOLUME_ID
```

cannot reliably represent an ASG because instances can be replaced or scaled dynamically.

The existing:

```text
UnHealthyHostCount
```

alarm provides per-target health.

A future ASG-level alarm could monitor:

```text
AWS/AutoScaling
GroupInServiceInstances
```

to detect when the overall number of healthy instances drops below an expected level.

---

# 25. Log-Based Alarms

Metric alarms do not inspect application logs.

Therefore some failures can remain invisible if:

```text
CPU is normal
Memory is normal
Latency is normal
5xx count is normal
```

but an important background process is failing.

CloudWatch Logs Metric Filters can eventually be used to convert log patterns into CloudWatch metrics.

The flow would be:

```text
Application logs
       |
       v
CloudWatch Logs
       |
       v
Metric Filter
       |
       v
Custom CloudWatch Metric
       |
       v
CloudWatch Alarm
       |
       v
SNS
       |
       v
DevOps Agent
```

---

# 26. Candidate Log Patterns

The following patterns were identified during the 2026-07-26 review.

## Tier 1 — Critical infrastructure failures

| Pattern                     | Source                                      | Meaning                                                    |
| --------------------------- | ------------------------------------------- | ---------------------------------------------------------- |
| `shutting down gracefully`  | `queues/queue.js:50`                        | pg-boss reconnects failed and application is shutting down |
| `[PG-BOSS] lost connection` | `queues/queue.js:33`                        | pg-boss lost its database connection                       |
| `❌ Redis Error`             | `config/redis.js:52`                        | Redis failure                                              |
| `❌ Redis Sub Error`         | `config/redis.js:59`                        | Redis subscription failure                                 |
| `Failed to start listener`  | `services/cache-subscription-service.js:30` | Cache subscription failed                                  |

---

## Tier 2 — Background business failures

These errors may be caught and logged without being re-thrown.

| Pattern                                      | Source                                  |
| -------------------------------------------- | --------------------------------------- |
| `Error fetching payment status from network` | `controllers/order-controller.js:56`    |
| `Track failed for Order`                     | Ainsoft / Nasex tracking workers        |
| `[WEBHOOK-In-Dispatcher]`                    | `integration/webhooks/dispatcher.js:34` |

---

## Tier 3 — Webhook security failures

Potential patterns:

```text
invalid signature
NETWORK_WEBHOOK_SECRET
Network-Decrypt] Failed
```

For these, alerting on a **spike** is preferable to alerting on every individual occurrence.

A single invalid webhook signature may simply be a malformed or expired request.

A sudden large increase could indicate:

```text
misconfiguration
integration failure
attack
credential mismatch
```

---

# 27. Worked Example — `my-cluster-development`

The following values represent the first environment where this runbook was executed.

| Resource      | Value                                                     |
| ------------- | --------------------------------------------------------- |
| Region        | `ap-south-1`                                              |
| Environment   | `dev`                                                     |
| SNS topic     | `arn:aws:sns:ap-south-1:123456789012:devops-agent-alerts` |
| ECS cluster   | `my-cluster-development`                                 |
| EC2 instance  | `i-075d4878cffb36797`                                     |
| EBS volume    | `vol-0d7ac883b8fc21530`                                   |
| Load balancer | `app/my-alb/1234567890abcdef`                     |

---

# 28. ECS Services

| Service               | Target Group                                             |
| --------------------- | -------------------------------------------------------- |
| `my-backend-dev`     | `targetgroup/my-dev-backend/2222222222222222`     |
| `my-frontend-dev`    | `targetgroup/my-dev-frontend/3333333333333333`    |
| `my-frontend-dev-v2` | `targetgroup/my-dev-frontend-v2/4444444444444444` |
| `my-admin-dev`       | `targetgroup/my-dev-admin/1111111111111111`       |

---

# 29. Development Alarm Status

As of **2026-07-26**:

```text
16 / 16 ECS alarms created
4 / 4 RDS alarms created
20 / 20 total alarms
```

| Alarm                               | Status                      |
| ----------------------------------- | --------------------------- |
| `Dev CPU Spike`                     | ✅                           |
| `Dev Memory Spike`                  | ✅                           |
| `Dev Disk Spike`                    | ✅ Corrected                 |
| `Dev EC2 Instance Health`           | ✅                           |
| `Dev {4 services} Target Unhealthy` | ✅                           |
| `Dev {4 services} Latency`          | ✅                           |
| `Dev {4 services} 5xx Errors`       | ✅                           |
| `Dev Aurora Connections`            | ✅ Threshold requires tuning |
| `Dev Aurora CPU Spike`              | ✅                           |
| `Dev Aurora Low Memory`             | ✅                           |
| `Dev Aurora ACU Ceiling`            | ✅                           |

Verification:

```bash
aws cloudwatch describe-alarms \
  --region ap-south-1 \
  --alarm-name-prefix "Dev" \
  --query 'length(MetricAlarms)' \
  --output text
```

Expected result:

```text
20
```

---

# 30. Final Verification Checklist

Before considering a new environment complete, verify the following.

## AWS

* [ ] AWS CLI is authenticated against the correct account
* [ ] Correct AWS region selected
* [ ] ECS cluster exists
* [ ] ECS services discovered
* [ ] Target groups discovered
* [ ] Load balancer discovered
* [ ] EC2 instance identified, if applicable
* [ ] EBS volume identified, if applicable
* [ ] Aurora cluster identified
* [ ] Aurora writer instance identified

---

## SNS

* [ ] SNS topic exists
* [ ] SNS subscription exists
* [ ] SNS subscription is confirmed
* [ ] `/webhooks/cloudwatch` endpoint is reachable

---

## Alarm configuration

* [ ] CPU alarm created
* [ ] Memory alarm created
* [ ] Disk alarm created, if applicable
* [ ] EC2 health alarm created, if applicable
* [ ] Target unhealthy alarm created for every service
* [ ] Latency alarm created for every service
* [ ] 5xx alarm created for every service
* [ ] Aurora connections alarm created
* [ ] Aurora CPU alarm created
* [ ] Aurora memory alarm created
* [ ] Aurora ACU alarm created

---

## Alarm actions

Every alarm must have:

```text
AlarmActions → SNS
OKActions    → SNS
```

---

## Environment tagging

Every alarm must have:

```text
environment=<dev|stag|production>
```

Verify that the value is correct.

---

## End-to-end testing

* [ ] ECS alarms manually triggered
* [ ] ECS alarm events received by SNS
* [ ] ECS alarm events received by webhook
* [ ] ECS events stored in `raw_events`
* [ ] Aurora alarms manually triggered
* [ ] Aurora alarm events received by SNS
* [ ] Aurora events received by webhook
* [ ] Aurora events stored in `raw_events`
* [ ] All alarms reset to `OK`

---

# 31. Current Coverage and Next Steps

The current monitoring architecture provides:

```text
                     CloudWatch
                         |
        +----------------+----------------+
        |                |                |
        v                v                v
   ECS Infra       ECS/Application      Aurora
        |                |                |
        +----------------+----------------+
                         |
                         v
                        SNS
                         |
                         v
                 CloudWatch Webhook
                         |
                         v
                    raw_events
                         |
                         v
                    DevOps Agent
```

For `my-cluster-development`, the current coverage is:

```text
ECS Infrastructure       4 alarms
ECS Target Health        4 alarms
Application Latency      4 alarms
Application 5xx          4 alarms
Aurora PostgreSQL        4 alarms
                         ─────────
                         20 alarms
```

Next environments:

```text
my-cluster-staging
my-cluster-production
```

Before production setup, confirm whether the production ECS cluster uses an Auto Scaling Group and apply the ASG-specific guidance in this document.

Remaining monitoring areas:

```text
ElastiCache Redis
Self-hosted Elasticsearch
Synthetic/business monitoring
ASG-level monitoring
Log-based alarms
```

These are intentionally deferred and should be added as separate monitoring improvements rather than mixed into the basic ECS/RDS alarm setup.
