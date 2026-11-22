"""All configuration comes from environment variables (spec section 7, portability rule 1)."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/rah"
    STORAGE_BACKEND: str = "local"  # local | s3
    STORAGE_LOCAL_PATH: str = "/data"
    S3_BUCKET: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_REGION: str = "us-east-1"

    AUTH_MODE: str = "local"  # local | oidc
    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_REDIRECT_URI: str = ""

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "resource-hub@example.com"
    TEAMS_WEBHOOK_URL: str = ""

    IMPORT_CRON: str = "0 6 * * *"
    IMPORT_TIMEZONE: str = "America/Chicago"
    IMPORT_WATCH_DIR: str = ""  # IMP-5: watched folder for scheduled pulls
    SCHEDULER_ENABLED: bool = True

    OPENAIR_BASE_URL: str = ""  # IMP-6
    OPENAIR_CLIENT_ID: str = ""
    OPENAIR_CLIENT_SECRET: str = ""
    OPENAIR_REFRESH_TOKEN: str = ""
    OPENAIR_REPORT_ID: str = ""

    APP_BASE_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me-in-production"
    TOKEN_TTL_HOURS: int = 12
    COOKIE_SECURE: bool = False

    SEED_DEMO: bool = False
    DEMO_PASSWORD: str = "demo1234"
    ADMIN_EMAIL: str = ""  # first Admin for non-demo deploys (created only when there are no users)
    ADMIN_PASSWORD: str = ""
    PORT: int = 8000
    STATIC_DIR: str = ""

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Railway / Heroku style URLs use postgres:// or postgresql:// - force the psycopg3 driver.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
