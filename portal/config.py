from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Pin .env lookup to portal/.env regardless of where uvicorn is started from.
_PORTAL_ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_PORTAL_ENV_FILE, extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/portal"
    portal_secret_key: str = "dev-only-do-not-use-in-prod"
    portal_base_url: str = "http://localhost:8000"
    admin_emails: str = ""  # comma-separated
    rate_limit_per_min: int = 10
    debug: bool = False
    log_level: str = "INFO"  # root level for the `portal.*` logger tree

    # Dev-only seed: when DEBUG=true and both are set, lifespan startup
    # idempotently upserts a user with this email + container token.
    # Lets `docker compose up` produce a known token without a manual CLI step.
    seed_user_email: str = ""
    seed_container_token: str = ""

    # Stripe subscription billing (portal/billing.py). The /pricing page and
    # the /billing/* routes 404 until the secret key and price ids are set.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Comma-separated plans, in display order. Each is a recurring Stripe Price
    # id, optionally followed by `+` and a one-time Price id charged with the
    # first payment (an upfront fee): `price_monthly+price_upfront,price_other`.
    stripe_price_ids: str = ""

    @property
    def stripe_plans(self) -> dict[str, str | None]:
        """Recurring price id -> its upfront one-time price id, or None."""
        plans: dict[str, str | None] = {}
        for entry in self.stripe_price_ids.split(","):
            recurring, _, upfront = entry.strip().partition("+")
            if recurring.strip():
                plans[recurring.strip()] = upfront.strip() or None
        return plans

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


settings = Settings()
