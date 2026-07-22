from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://devops_agent:devops_agent@localhost:5432/devops_agent"
    sns_auto_confirm_subscriptions: bool = True
    github_webhook_secret: str = ""


settings = Settings()
