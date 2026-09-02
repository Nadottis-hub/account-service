from functools import lru_cache
from typing import Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# asyncpg rejects libpq-style query parameters that psycopg accepts. Managed Postgres
# providers (Railway included) often append them, so they are stripped here and
# translated into driver arguments in app/db.py.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "target_session_attrs", "connect_timeout"}
_SSL_MODES = {"require", "verify-ca", "verify-full"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/account_service",
    )
    clerk_webhook_secret: str = Field(default="")
    log_level: str = Field(default="INFO")
    env: str = Field(default="local")
    port: int = Field(default=8000)

    # Derived from the connection string, not read from the environment directly.
    require_ssl: bool = False

    @model_validator(mode="after")
    def _normalise_database_url(self) -> Self:
        """Accept the plain URL Railway injects and hand asyncpg something it can parse."""
        parts = urlsplit(self.database_url)

        scheme = parts.scheme
        if scheme in ("postgres", "postgresql"):
            scheme = "postgresql+asyncpg"

        kept: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query):
            if key == "sslmode" and value in _SSL_MODES:
                self.require_ssl = True
            if key not in _LIBPQ_ONLY_PARAMS:
                kept.append((key, value))

        self.database_url = urlunsplit(
            (scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
        )
        return self

    @property
    def is_local(self) -> bool:
        return self.env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
