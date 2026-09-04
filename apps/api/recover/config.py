"""Runtime configuration. All secrets come from the environment; none are hardcoded."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database -----------------------------------------------------------
    # Postgres in Docker Compose; SQLite by default so the API runs on a clean
    # clone with no infrastructure. See docs/decisions.md (ADR-009).
    database_url: str = f"sqlite:///{(REPO_ROOT / 'recover.db').as_posix()}"

    # --- Razorpay (Test Mode only) -----------------------------------------
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # --- Anthropic ----------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    ai_timeout_seconds: float = 45.0

    # --- App ----------------------------------------------------------------
    public_web_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    demo_seed: int = 20260901
    cors_origins: str = "http://localhost:3000"

    @property
    def razorpay_enabled(self) -> bool:
        """True only when real Test Mode credentials are present."""
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def is_test_mode_key(self) -> bool:
        """Razorpay test keys are prefixed rzp_test_. Guards against live keys."""
        return self.razorpay_key_id.startswith("rzp_test_")

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
