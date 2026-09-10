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
    AI_MODEL: str = "zai-org/GLM-5.3-Flash"
    AI_TIMEOUT_SECONDS: int = 120
    # GLM-5.3-Flash is a REASONING model: it spends the output budget on a
    # hidden reasoning pass first and leaves `message.content` empty when the
    # budget is too small. The extraction / consolidation calls therefore need a
    # generous ceiling — far above the 4000 that the chat agent gets by default.
    AI_EXTRACTION_MAX_TOKENS: int = 12000
    # Optional per-task model tiering (app.services.ai_models). Comma-separated
    # `TASK=model` pairs, e.g. "COMPARE=zai-org/GLM-4.6,PROPUESTA=zai-org/GLM-4.6".
    # Blank => every task uses AI_MODEL. A seam, not a behaviour change.
    AI_MODEL_TIERS: str = ""
    # The holistic comparison sees ALL cotización readings at once. Above this
    # assembled-input size the readings are split into batches, each run then
    # merged into one standardized table. Sized so a batch is ~1-2 DENSE quotes:
    # each batch must re-emit its dimensions × cells WITH verbatim, which is a
    # large output, and both that output AND the 360s timeout have to fit. A
    # dense 3-quote comparison (~90k chars) therefore batches (correct); a small
    # or sparse comparison still fits one call. (Keep backend.env aligned.)
    AI_COMPARE_MAX_INPUT_CHARS: int = 45000
    # The comparison OUTPUT budget — SEPARATE from and LARGER than the extraction
    # budget, because standardizing a batch re-emits every dimension's cells with
    # verbatim. Too small and GLM (a reasoning model) spends the budget thinking,
    # then truncates the tool arguments to an empty dict — the bug this fixes.
    AI_COMPARE_MAX_TOKENS: int = 24000

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

    # --- Expediente packs (submission / comparison / proposal) ---
    # The ZIP is assembled in memory and the generation is synchronous, so the
    # size is bounded: over this the endpoint returns 413 and the UI tells the
    # user to download the sections one by one.
    PACK_MAX_MB: int = 64

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
        "AI_EXTRACTION_MAX_TOKENS",
        "AI_COMPARE_MAX_INPUT_CHARS",
        "AI_COMPARE_MAX_TOKENS",
        "MEDIA_MAX_UPLOAD_MB",
        "DOCUMENT_MAX_UPLOAD_MB",
        "PACK_MAX_MB",
        mode="before",
    )
    @classmethod
    def _coerce_int(cls, v):  # noqa: ANN001
        if isinstance(v, str) and v.strip():
            return int(v.strip())
        return v


settings = Settings()
