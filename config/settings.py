from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppDbConfig(BaseModel):
    """Connection info for one environment's app database -- read-only credentials only."""

    host: str
    port: int
    database: str
    readonly_username: str
    readonly_password: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://devops_agent:devops_agent@localhost:5432/devops_agent"
    log_level: str = "INFO"
    sns_auto_confirm_subscriptions: bool = True
    github_webhook_secret: str = ""
    slack_webhook_url: str = ""
    aws_region: str = "ap-south-1"

    # LLM provider -- swap by changing llm_provider (+ matching api key/model), no agent code changes needed.
    llm_provider: str = "openai"  # "openai" | "anthropic"
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
    # Field names mirror the sgm-backend app's own DB_{ENV}_* .env convention,
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


settings = Settings()
