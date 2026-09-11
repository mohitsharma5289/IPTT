"""Application configuration. Every value is environment-driven.

Replaces the hardcoded literals in the legacy app (audit B4, B7): database URL,
session secret, SMTP settings and the project start date were all source literals.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- identity -----------------------------------------------------------
    app_name: str = "IPTT"
    environment: Literal["local", "dev", "uat", "prod"] = "local"
    debug: bool = False

    # --- database -----------------------------------------------------------
    # postgresql+psycopg://user:pass@host:5432/iptt
    database_url: str = Field(default="postgresql+psycopg://iptt:iptt@localhost:5432/iptt")
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_pre_ping: bool = True
    db_echo: bool = False

    # --- security -----------------------------------------------------------
    # MUST be injected from an OpenShift Secret. No default in any deployed env.
    session_secret: SecretStr = SecretStr("dev-only-insecure-change-me")
    session_max_age_seconds: int = 60 * 60 * 8
    session_cookie_name: str = "iptt_session"
    session_https_only: bool = True
    allow_self_registration: bool = False
    password_min_length: int = 12

    # --- cors / frontend ----------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- behaviour ----------------------------------------------------------
    # Audit M12: uploads previously had no bound and were read fully into memory.
    max_upload_bytes: int = 10 * 1024 * 1024
    api_page_size_default: int = 25
    api_page_size_max: int = 200

    # --- observability ------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    @field_validator("session_secret")
    @classmethod
    def _reject_default_secret_outside_local(cls, v: SecretStr, info):  # noqa: ANN001
        return v

    def validate_for_environment(self) -> None:
        """Fail fast at startup rather than shipping an insecure deployment."""
        problems: list[str] = []
        if self.environment != "local":
            if self.session_secret.get_secret_value() == "dev-only-insecure-change-me":
                problems.append("SESSION_SECRET must be set from a Secret outside local")
            if len(self.session_secret.get_secret_value()) < 32:
                problems.append("SESSION_SECRET must be at least 32 characters")
            if "localhost" in self.database_url:
                problems.append("DATABASE_URL still points at localhost")
            if self.debug:
                problems.append("DEBUG must be false outside local")
        if problems:
            raise RuntimeError("Invalid configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
