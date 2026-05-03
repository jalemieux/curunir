from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Pin .env lookup to portal/.env regardless of where uvicorn is started from.
_PORTAL_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_PORTAL_ENV_FILE, extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/portal"
    portal_secret_key: str = "dev-only-do-not-use-in-prod"
    portal_base_url: str = "http://localhost:8000"
    email_api_key: str = ""
    email_from: str = "noreply@example.com"
    admin_emails: str = ""  # comma-separated
    rate_limit_per_min: int = 10
    debug: bool = False
    # Application-level heartbeat sent to each connected agent socket so a
    # cycling portal app behind a connection-keeping proxy is detectable.
    agent_heartbeat_interval: float = 15.0

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


settings = Settings()
