from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppDbConfig(BaseModel):
    """Connection info for one environment's app database -- read-only credentials only."""

    host: str
    port: int
    database: str
    readonly_username: str
    readonly_password: str


class RemediationDbConfig(BaseModel):
    """Connection info for one environment's app database using the write-capable
    remediation role -- never the same credentials as AppDbConfig's readonly role."""

    host: str
    port: int
    database: str
    username: str
    password: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://devops_agent:devops_agent@localhost:5432/devops_agent"
    celery_broker_url: str = "redis://localhost:6379/0"
    # Caps how many diagnosis runs can start per minute regardless of alarm volume or
    # worker concurrency -- an alarm storm (e.g. a real outage triggering many alarms
    # at once) would otherwise fan out into that many concurrent LLM pipelines with no
    # ceiling on cost. Celery enforces this per-worker via task_annotations (see
    # config/celery_app.py), throttling task pickup rather than dropping/rejecting.
    celery_task_rate_limit: str = "20/m"
    # First line of defense against a webhook flood (real or malicious) before it even
    # reaches Celery -- deliberately looser than celery_task_rate_limit above, since
    # this guards raw request volume, not diagnosis-pipeline cost specifically.
    webhook_rate_limit: int = 60
    webhook_rate_limit_window_seconds: int = 60
    log_level: str = "INFO"
    sns_auto_confirm_subscriptions: bool = True
    github_webhook_secret: str = ""
    slack_webhook_url: str = ""
    # Verifies POST /webhooks/slack/interactions actually came from Slack (the
    # button-click payload for HITL remediation approval) -- see
    # app/controllers/concerns/webhooks/verifiable.py's verify_slack_signature.
    # Copied from the Slack app's Basic Information page, not the incoming webhook URL.
    slack_signing_secret: str = ""
    # Who is allowed to approve a remediation action (Approve / Approve All Remaining)
    # in POST /webhooks/slack/interactions -- comma-separated Slack user IDs and/or
    # usernames, e.g. "U0123ABC,jane.doe". A valid Slack signature only proves the
    # click came from Slack, not that the clicking user is authorized to trigger a
    # write action (pg_cancel_backend/pg_terminate_backend) -- this is that missing
    # check. Deliberately fails closed: blank means nobody is authorized, not
    # "check disabled" (unlike GITHUB_WEBHOOK_SECRET above) -- an unconfigured
    # allowlist should never silently mean "anyone can approve." Rejecting a
    # remediation is not gated by this; only approving one is.
    slack_approver_allowlist: str = ""
    # A query running longer than this is a candidate for the "cancel a runaway query"
    # remediation (propose_remediation.py) -- see get_long_running_queries.
    remediation_long_query_threshold_seconds: int = 60
    # A connection idle-in-transaction longer than this is a candidate for the
    # "terminate idle connection" remediation (propose_idle_connection_remediation.py)
    # -- see get_idle_in_transaction_connections. Deliberately higher than the query
    # threshold above: terminating a connection is more disruptive than cancelling a
    # query, so the bar to even propose it is set higher.
    remediation_idle_connection_threshold_seconds: int = 300
    aws_region: str = "ap-south-1"
    # Left blank, config/aws.py falls back to boto3's own default credential chain (a
    # configured AWS CLI profile, instance role, etc.) -- set these when there's no
    # such fallback available, e.g. inside the Docker containers in docker-compose.yml.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # LLM provider -- swap by changing llm_provider (+ matching api key/model), no agent code changes needed.
    llm_provider: str = "openai"  # "openai" | "anthropic"
    # Every LLM call in this app goes through config.llm.get_llm(), so this bounds all
    # of them -- without it, a slow/hung provider response blocks the Celery task (and
    # the worker slot running it) indefinitely, since nothing else times it out.
    llm_timeout_seconds: float = 60.0
    # investigate_further.py's ReAct loop is the only place an LLM decides how many
    # times to call tools -- recursion_limit already bounds the number of round trips,
    # this bounds the actual cost of them. See config/reliability/token_budget.py for
    # why this is checked after the loop finishes, not used to interrupt it mid-flight.
    max_investigation_tokens: int = 20000
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # Embeddings always go through OpenAI regardless of llm_provider -- Anthropic has no embeddings API.
    embedding_model: str = "text-embedding-3-small"

    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: str = ""
    langsmith_project: str = ""

    # App database connections for the RDS domain agent's DB diagnostic tools.
    # Field names mirror the main app's own DB_{ENV}_* .env convention,
    # except "readonly_" stays explicit in the credential fields -- unlike the
    # app's own DB_STAGING_USERNAME/PASSWORD, these must never be superuser creds.
    db_dev_host: str = ""
    db_dev_port: int = 5432
    db_dev_database: str = ""
    db_dev_readonly_username: str = ""
    db_dev_readonly_password: str = ""

    db_staging_host: str = ""
    db_staging_port: int = 5432
    db_staging_database: str = ""
    db_staging_readonly_username: str = ""
    db_staging_readonly_password: str = ""

    db_production_host: str = ""
    db_production_port: int = 5432
    db_production_database: str = ""
    db_production_readonly_username: str = ""
    db_production_readonly_password: str = ""

    # Separate write-capable credentials for the RDS domain agent's remediation tools
    # (e.g. cancel_backend) -- deliberately not the same role as the *_readonly_*
    # credentials above. Granted membership in pg_signal_backend only, never superuser --
    # see documentation/rds-agent/1.your-rds-readonly-db-role-setup.md for the grant.
    db_dev_remediation_username: str = ""
    db_dev_remediation_password: str = ""

    db_staging_remediation_username: str = ""
    db_staging_remediation_password: str = ""

    db_production_remediation_username: str = ""
    db_production_remediation_password: str = ""

    def app_db_config(self, environment: str) -> AppDbConfig:
        """Look up the app database for a CloudWatch alarm's "environment" tag value."""
        configs = {
            "dev": AppDbConfig(
                host=self.db_dev_host,
                port=self.db_dev_port,
                database=self.db_dev_database,
                readonly_username=self.db_dev_readonly_username,
                readonly_password=self.db_dev_readonly_password,
            ),
            "stag": AppDbConfig(
                host=self.db_staging_host,
                port=self.db_staging_port,
                database=self.db_staging_database,
                readonly_username=self.db_staging_readonly_username,
                readonly_password=self.db_staging_readonly_password,
            ),
            "production": AppDbConfig(
                host=self.db_production_host,
                port=self.db_production_port,
                database=self.db_production_database,
                readonly_username=self.db_production_readonly_username,
                readonly_password=self.db_production_readonly_password,
            ),
        }
        if environment not in configs:
            raise ValueError(f"no app database configured for environment={environment!r}")
        return configs[environment]

    def remediation_db_config(self, environment: str) -> RemediationDbConfig:
        """Like app_db_config(), but with write-capable remediation credentials instead
        of the readonly role -- host/port/database are shared, only the role differs."""
        readonly = self.app_db_config(environment)
        username, password = {
            "dev": (self.db_dev_remediation_username, self.db_dev_remediation_password),
            "stag": (self.db_staging_remediation_username, self.db_staging_remediation_password),
            "production": (self.db_production_remediation_username, self.db_production_remediation_password),
        }[environment]
        return RemediationDbConfig(
            host=readonly.host, port=readonly.port, database=readonly.database, username=username, password=password
        )

    def slack_approver_allowlist_set(self) -> set[str]:
        """Parses SLACK_APPROVER_ALLOWLIST into a lowercased set for a forgiving match
        against a Slack user's id/username/name -- see the field's own comment for why
        this fails closed on blank rather than disabling the check."""
        return {entry.strip().lower() for entry in self.slack_approver_allowlist.split(",") if entry.strip()}


settings = Settings()
