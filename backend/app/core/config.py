"""Application settings loaded from environment / .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationInfo, field_validator
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

    # --- v12: mail (docs/v12/04-mail.md §1) ---
    # Master switch. False => every send is recorded as status "skipped" and no
    # socket (and no boto3 client) is opened. The Radal system sender is
    # contacto@radalseguros.cl: it SENDS through Amazon SES v2 (identity
    # radalseguros.cl, Easy DKIM, MAIL FROM mail.radalseguros.cl) and its
    # replies are READ from the Zoho mailbox over IMAP. The Zoho app password
    # lives ONLY in env / S3 deploy/backend.env, never in source.
    MAIL_ENABLED: bool = False
    # How the Radal SYSTEM sender sends: "ses" (Amazon SES v2, IAM role creds)
    # or "smtp" (the Zoho SMTP fallback below). Broker custom mailboxes always
    # use their own SMTP/IMAP, whatever this says.
    MAIL_TRANSPORT: str = "ses"
    SES_REGION: str = "us-east-1"
    SES_CONFIGURATION_SET: str = ""  # blank => no ConfigurationSetName
    # How a human sees what the Radal SYSTEM sender sent: "bcc" (the default:
    # every system mail Bcc's MAIL_COPY_ADDRESS — SES envelope only, never a
    # header), "imap_append" (APPEND to MAIL_SENT_FOLDER over IMAP, \Seen,
    # best-effort; needs the Zoho IMAP login, which Zoho Free does not have)
    # or "none". Broker custom mailboxes never get a copy.
    MAIL_SENT_COPY: str = "bcc"
    MAIL_SENT_FOLDER: str = "Sent"
    # The human mailbox (Zoho contacto@): the BCC copy and the forward of every
    # reply received through SES inbound go here. Always allowed by
    # MAIL_RECIPIENT_ALLOWLIST. Blank => no copy, no forward.
    MAIL_COPY_ADDRESS: str = "contacto@radalseguros.cl"
    # Reply-To of system mail Radal must READ the answer of (insurer status
    # inquiries and their resends). Blank => RADAL_MAIL_ADDRESS (as before).
    # Dev: respuestas@correo.radalseguros.cl (SES inbound -> S3 -> mail.poll).
    # Payer reminders keep replying to the broker's own contact.
    RADAL_MAIL_REPLY_TO: str = ""
    # Where mail.poll reads the RADAL system mailbox from: "s3" (SES inbound
    # writes raw MIME under MAIL_INBOUND_S3_PREFIX of the bucket), "imap" (the
    # Zoho login), "none", or "auto" (the default: "imap" when
    # RADAL_MAIL_PASSWORD is set, else "none"). Broker mailboxes always IMAP.
    MAIL_INBOUND_SOURCE: str = "auto"
    MAIL_INBOUND_S3_PREFIX: str = "inbound-mail/"
    MAIL_INBOUND_S3_BUCKET: str = ""  # blank => S3_BUCKET
    # Objects read per mail.poll run (the rest wait for the next minute).
    MAIL_INBOUND_S3_MAX_PER_RUN: int = 50
    # Dev safety: comma-separated domains ("radalseguros.cl") or full
    # addresses. To/Cc outside it are dropped; nothing left => "skipped".
    # Blank => no restriction (production). MAIL_COPY_ADDRESS always passes.
    MAIL_RECIPIENT_ALLOWLIST: str = ""
    RADAL_MAIL_ADDRESS: str = "contacto@radalseguros.cl"
    RADAL_MAIL_DISPLAY_NAME: str = "Radal"
    RADAL_MAIL_USERNAME: str = ""  # Zoho login (usually the address)
    RADAL_MAIL_PASSWORD: str = ""  # Zoho app password (IMAP; SMTP when MAIL_TRANSPORT=smtp)
    # Zoho: smtp.zoho.com 465 SSL / 587 STARTTLS; imap.zoho.com 993 SSL.
    # Region hosts (smtp.zoho.eu …) exist, hence settings, not constants.
    # The SMTP_* keys are only used when MAIL_TRANSPORT=smtp.
    RADAL_MAIL_SMTP_HOST: str = "smtp.zoho.com"
    RADAL_MAIL_SMTP_PORT: int = 465
    RADAL_MAIL_SMTP_SECURITY: str = "ssl"  # ssl | starttls | none
    RADAL_MAIL_IMAP_HOST: str = "imap.zoho.com"
    RADAL_MAIL_IMAP_PORT: int = 993
    RADAL_MAIL_IMAP_SECURITY: str = "ssl"  # ssl | starttls | none
    # Where RPA / job failures are reported to the RADAL team. Blank = nowhere.
    RADAL_OPS_EMAIL: str = ""
    MAIL_TIMEOUT_SECONDS: int = 20

    # --- v12: jobs & secrets ---
    # Shared secret of the internal job routes (header X-Radal-Job-Token / the
    # EventBridge body token). Blank => those routes answer 404.
    JOBS_TOKEN: str = ""
    # Whether the LWA ``POST /events`` route accepts scheduler events at all.
    JOBS_EVENTS_ENABLED: bool = False
    # Fernet key for stored secrets (SMTP/IMAP, Forpay, portal passwords).
    # Blank => derived from SECRET_KEY (app.core.crypto). Generate once and back
    # it up: losing it makes every stored secret unreadable.
    CREDENTIALS_KEY: str = ""

    # --- v12: public URLs ---
    # The SPA origin used in share links / mail. Blank => first CORS origin.
    PUBLIC_APP_URL: str = ""
    # The API origin handed to third parties (Forpay webhooks). Blank =>
    # PUBLIC_APP_URL (the SPA and the API share an origin behind CloudFront).
    PUBLIC_API_URL: str = ""

    # --- v12: FX + payment provider ---
    FX_SOURCE_URL: str = "https://mindicador.cl/api"
    FORPAY_DEFAULT_BASE_URL: str = "https://devapi.forpayservices.cl/white/label"

    # --- v12: portal-search RPA (Playwright) ---
    RPA_ENABLED: bool = False
    # True => run a queued search inside the request (dev); False => job rpa.run.
    RPA_INLINE: bool = False
    RPA_HEADLESS: bool = True
    RPA_HDI_BASE_URL: str = ""
    # Product flag (v12 copilot pass): portal search needs each insurer's own
    # user + password to apply filters, so it ships as "pronto". Off => the
    # API refuses runs (409), the portals read ``coming_soon``, ``rpa.run`` is a
    # no-op and the agent is not offered the portal tools/skill. The code stays.
    PORTAL_SEARCH_ENABLED: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def public_app_url(self) -> str:
        """SPA origin, no trailing slash. Falls back to the first CORS origin."""
        url = self.PUBLIC_APP_URL.strip()
        if not url:
            origins = self.cors_origins_list
            url = origins[0] if origins else ""
        return url.rstrip("/")

    @property
    def public_api_url(self) -> str:
        """API origin (no ``/api/v1``, no trailing slash) for third-party callbacks."""
        url = self.PUBLIC_API_URL.strip()
        return (url or self.public_app_url).rstrip("/")

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
        "RADAL_MAIL_SMTP_PORT",
        "RADAL_MAIL_IMAP_PORT",
        "MAIL_TIMEOUT_SECONDS",
        "MAIL_INBOUND_S3_MAX_PER_RUN",
        mode="before",
    )
    @classmethod
    def _coerce_int(cls, v):  # noqa: ANN001
        if isinstance(v, str) and v.strip():
            return int(v.strip())
        return v

    @field_validator(
        "MAIL_ENABLED",
        "JOBS_EVENTS_ENABLED",
        "RPA_ENABLED",
        "RPA_INLINE",
        "RPA_HEADLESS",
        "PORTAL_SEARCH_ENABLED",
        mode="before",
    )
    @classmethod
    def _coerce_bool(cls, v, info: ValidationInfo):  # noqa: ANN001
        # A blank env value (``MAIL_ENABLED=``) means "unset", not an error —
        # keep the field's declared default rather than failing the boot.
        if isinstance(v, str):
            token = v.strip().lower()
            if token == "":
                return cls.model_fields[info.field_name].default
            if token in ("0", "false", "no", "off"):
                return False
            if token in ("1", "true", "yes", "on"):
                return True
        return v

    @field_validator("MAIL_TRANSPORT", mode="before")
    @classmethod
    def _check_mail_transport(cls, v):  # noqa: ANN001
        token = (v or "").strip().lower() if isinstance(v, str) else v
        if token == "":
            return cls.model_fields["MAIL_TRANSPORT"].default
        if token not in ("ses", "smtp"):
            raise ValueError("MAIL_TRANSPORT must be one of: ses, smtp")
        return token

    @field_validator("MAIL_SENT_COPY", mode="before")
    @classmethod
    def _check_mail_sent_copy(cls, v):  # noqa: ANN001
        token = (v or "").strip().lower() if isinstance(v, str) else v
        if token == "":
            return cls.model_fields["MAIL_SENT_COPY"].default
        if token not in ("bcc", "imap_append", "none"):
            raise ValueError("MAIL_SENT_COPY must be one of: bcc, imap_append, none")
        return token

    @field_validator("MAIL_INBOUND_SOURCE", mode="before")
    @classmethod
    def _check_mail_inbound_source(cls, v):  # noqa: ANN001
        token = (v or "").strip().lower() if isinstance(v, str) else v
        if token == "":
            return cls.model_fields["MAIL_INBOUND_SOURCE"].default
        if token not in ("auto", "s3", "imap", "none"):
            raise ValueError("MAIL_INBOUND_SOURCE must be one of: auto, s3, imap, none")
        return token

    @field_validator("MAIL_INBOUND_S3_PREFIX", mode="before")
    @classmethod
    def _normalise_inbound_prefix(cls, v):  # noqa: ANN001
        if isinstance(v, str):
            v = v.strip().lstrip("/")
            if v == "":
                return cls.model_fields["MAIL_INBOUND_S3_PREFIX"].default
            if not v.endswith("/"):
                v += "/"
        return v

    @field_validator("MAIL_COPY_ADDRESS", "RADAL_MAIL_REPLY_TO", "MAIL_RECIPIENT_ALLOWLIST",
                     "MAIL_INBOUND_S3_BUCKET", mode="before")
    @classmethod
    def _strip_text(cls, v):  # noqa: ANN001
        return v.strip() if isinstance(v, str) else v

    @property
    def mail_inbound_source(self) -> str:
        """Effective source of the Radal system mailbox: ``s3`` | ``imap`` | ``none``."""
        token = (self.MAIL_INBOUND_SOURCE or "auto").lower()
        if token == "auto":
            return "imap" if self.RADAL_MAIL_PASSWORD else "none"
        return token

    @property
    def mail_inbound_bucket(self) -> str:
        return self.MAIL_INBOUND_S3_BUCKET or self.S3_BUCKET

    @field_validator("SES_REGION", "MAIL_SENT_FOLDER", mode="before")
    @classmethod
    def _blank_is_default(cls, v, info: ValidationInfo):  # noqa: ANN001
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return cls.model_fields[info.field_name].default
        return v

    @field_validator("RADAL_MAIL_SMTP_SECURITY", "RADAL_MAIL_IMAP_SECURITY", mode="before")
    @classmethod
    def _check_mail_security(cls, v):  # noqa: ANN001
        token = (v or "").strip().lower() if isinstance(v, str) else v
        if token not in ("ssl", "starttls", "none"):
            raise ValueError("mail security must be one of: ssl, starttls, none")
        return token


settings = Settings()
