"""Application settings loaded from environment / .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (parent of app/)
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Central config. Values come from backend/.env (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Auth / JWT
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str = "sqlite:///./radal.db"

    # CORS — comma-separated origins
    CORS_ORIGINS: str = "http://localhost:5173"

    # API
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Radal Backend"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES", "REFRESH_TOKEN_EXPIRE_DAYS", mode="before")
    @classmethod
    def _coerce_int(cls, v):  # noqa: ANN001
        if isinstance(v, str) and v.strip():
            return int(v.strip())
        return v


settings = Settings()
