from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://devops_agent:devops_agent@localhost:5432/devops_agent"
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


settings = Settings()
