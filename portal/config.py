from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/portal"
    portal_secret_key: str = "dev-only-do-not-use-in-prod"
    portal_base_url: str = "http://localhost:8000"
    email_api_key: str = ""
    email_from: str = "noreply@example.com"
    admin_emails: str = ""  # comma-separated
    rate_limit_per_min: int = 10
    debug: bool = False

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


settings = Settings()
