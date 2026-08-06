"""Application settings loaded from environment / .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (parent of app/)
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Central config. Values come from backend/.env (see .env.example).

    Secrets (SECRET_KEY, AI_API_KEY, DB credentials) must NEVER be hardcoded in
    source. The defaults below are safe placeholders for local development only.
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Auth / JWT ---
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Database ---
    DATABASE_URL: str = "sqlite:///./radal.db"

    # --- CORS — comma-separated origins ---
    CORS_ORIGINS: str = "http://localhost:4000"

    # --- API ---
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Radal Backend"

    # --- AI (DeepInfra, OpenAI-compatible) ---
    # Used by the proposal processor, the proposal chat agent and the broker
    # agent. Accessed through the official `openai` SDK pointed at AI_BASE_URL.
    AI_BASE_URL: str = "https://api.deepinfra.com/v1/openai"
    AI_API_KEY: str = ""
    AI_MODEL: str = "meta-llama/Llama-3.3-70B-Instruct"
    AI_TIMEOUT_SECONDS: int = 120

    # --- Storage (S3) ---
    # One bucket holds both `media/` (logos, avatars) and `documents/` (files).
    S3_BUCKET: str = "radal-dev-185011028331"
    S3_REGION: str = "us-east-1"

    # --- Media / logos & avatars (WEBP 512x512) ---
    MEDIA_BACKEND: str = "local"  # "local" | "s3"
    MEDIA_LOCAL_DIR: str = str(BASE_DIR / "media")
    MEDIA_PUBLIC_BASE_URL: str = "/media"  # url prefix for locally-served media
    MEDIA_S3_PREFIX: str = "media/"
    MEDIA_MAX_UPLOAD_MB: int = 8

    # --- Documents ---
    DOCUMENT_S3_PREFIX: str = "documents/"
    DOCUMENT_MAX_UPLOAD_MB: int = 25

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # Media and documents share one bucket; these keep the older call sites
    # (app.services.media) working without duplicating the value in .env.
    @property
    def MEDIA_S3_BUCKET(self) -> str:  # noqa: N802 — matches the env-style naming
        return self.S3_BUCKET

    @property
    def MEDIA_S3_REGION(self) -> str:  # noqa: N802 — matches the env-style naming
        return self.S3_REGION

    @field_validator(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "REFRESH_TOKEN_EXPIRE_DAYS",
        "AI_TIMEOUT_SECONDS",
        "MEDIA_MAX_UPLOAD_MB",
        "DOCUMENT_MAX_UPLOAD_MB",
        mode="before",
    )
    @classmethod
    def _coerce_int(cls, v):  # noqa: ANN001
        if isinstance(v, str) and v.strip():
            return int(v.strip())
        return v


settings = Settings()
