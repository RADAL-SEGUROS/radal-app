"""Shared enum vocabulary + the SQL enum factory used by every model module.

All enum *values* are English lowercase snake_case. They are persisted as
strings (``native_enum=False``) with an explicit VARCHAR length so the schema
compiles identically on SQLite (local) and MySQL (RDS).

Why ``values_callable``: by default SQLAlchemy stores the Python member *name*.
We always want the member *value*, so member names can stay uppercase (PEP 8)
while the database holds the lowercase English token.
"""
from __future__ import annotations

import enum
import re
from typing import Type, TypeVar

from sqlalchemy import Enum as SQLEnum

_E = TypeVar("_E", bound=enum.Enum)

# Extra head-room over the longest current value so a new member does not force
# a migration for a couple of characters.
_LENGTH_SLACK = 12


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def sql_enum(enum_cls: Type[_E], *, length: int | None = None, name: str | None = None) -> SQLEnum:
    """Build the canonical ``SQLEnum`` for a Python enum.

    ``native_enum=False`` -> portable VARCHAR + no server-side ENUM type.
    ``validate_strings=True`` -> a raw string that is not a member raises early.
    """
    values = [str(member.value) for member in enum_cls]
    computed = max(len(value) for value in values) + _LENGTH_SLACK
    return SQLEnum(
        enum_cls,
        name=name or _snake(enum_cls.__name__),
        native_enum=False,
        length=length or computed,
        validate_strings=True,
        values_callable=lambda e: [str(member.value) for member in e],
    )


class StrEnum(str, enum.Enum):
    """Base for every domain enum: comparable to, and JSON-serialisable as, str."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# --- Cross-aggregate vocabularies -------------------------------------------


class EntityType(StrEnum):
    """Polymorphic target of ``document`` / ``activity`` / ``note`` / ``agent_thread``.

    Mirrors the S3 route ``documents/{entity_type}/{entity_id}/...``.
    """

    BROKER = "broker"
    USER = "user"
    CLIENT = "client"
    INSURED = "insured"
    INSURER = "insurer"
    ASSET = "asset"
    INSURANCE_LINE = "insurance_line"
    PLACEMENT = "placement"
    QUOTE_REQUEST = "quote_request"
    PROPOSAL = "proposal"
    INSPECTION_REQUEST = "inspection_request"
    INSPECTION = "inspection"
    POLICY = "policy"
    CLAIM = "claim"
    OFFERING = "offering"
    # --- Case files (v2 expedientes) ---------------------------------------
    # The longest value stays ``inspection_request`` (18), so the computed
    # VARCHAR length is unchanged at 30 — these members are free to add.
    CASE_FILE = "case_file"
    SALES_LEAD = "sales_lead"
    ENDORSEMENT = "endorsement"
    COLLECTION_PLAN = "collection_plan"
    WARRANTY = "warranty"
    # --- Groups & accounts (v3) — group archives (``archive_pack``) hang here.
    ACCOUNT_GROUP = "account_group"  # 13 < 18: no widening
    # --- v12 policies & cobranza (docs/v12/01-data-model.md §9) ------------
    # Every value is <= 18 chars (``insurance_subtype`` = 17), so the computed
    # VARCHAR stays 30 and no column widens.
    PAYMENT = "payment"
    INSTALLMENT = "installment"
    SHARE_LINK = "share_link"
    STATUS_INQUIRY = "status_inquiry"
    MAIL_ACCOUNT = "mail_account"
    PORTAL_SEARCH = "portal_search"
    INSURANCE_SUBTYPE = "insurance_subtype"
    PAYMENT_INTENT = "payment_intent"


class CoverageKind(StrEnum):
    """A coverage line item is either something covered or something excluded."""

    COVERAGE = "coverage"
    EXCLUSION = "exclusion"


class UserType(StrEnum):
    """Actor family. Mirrors ``USER_TYPES`` in ``app.core.roles_config``."""

    PLATFORM = "platform"
    BROKER = "broker"
    INSURER = "insurer"
    INSURED = "insured"


class PersonType(StrEnum):
    NATURAL = "natural"
    LEGAL = "legal"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# --- Case files (expedientes) ------------------------------------------------


class CaseSection(StrEnum):
    """Sub-expediente vocabulary, derived 1:1 from the demo corpus folder tree.

    Used by ``document.section``, ``case_pack.section`` and the extraction
    category registry. The Spanish folder names live in i18n, never here.
    """

    ROOT_PROSPECT = "root_prospect"
    SUBMISSION = "submission"
    INSURER_QUOTES = "insurer_quotes"
    BROKER_PROPOSAL = "broker_proposal"
    POLICY_FILE = "policy_file"
    COLLECTION = "collection"
    ENDORSEMENT = "endorsement"
    CLAIM = "claim"
    RENEWAL = "renewal"


class CaseFileKind(StrEnum):
    """What the expediente is about. ``account`` wraps a placement 1:1."""

    ACCOUNT = "account"
    ENDORSEMENT = "endorsement"
    COLLECTION = "collection"
    CLAIM = "claim"
    RENEWAL = "renewal"


class CaseStage(StrEnum):
    """The journey machine (docs/v2-case-files-spec.md §5).

    The account/renewal chain encodes hitos c1-c7 of the corredor lane; the
    post-sale kinds have their own short chains. ``closed`` is terminal and
    reachable from anywhere.
    """

    # Account / renewal chain, in order.
    LEAD = "lead"
    INTAKE = "intake"
    PRE_UNDERWRITING = "pre_underwriting"
    TECHNICAL_BASIS = "technical_basis"
    MARKET_SUBMISSION = "market_submission"
    QUOTES_RECEIVED = "quotes_received"
    COMPARISON = "comparison"
    INSURED_DECISION = "insured_decision"
    PROPOSAL_ISSUED = "proposal_issued"
    RATIFIED = "ratified"
    POLICY_ISSUED = "policy_issued"
    MIRROR_VALIDATION = "mirror_validation"
    ACTIVE = "active"
    RENEWAL_REVIEW = "renewal_review"
    # Endorsement chain.
    ENDORSEMENT_REQUESTED = "endorsement_requested"
    ENDORSEMENT_PROPOSED = "endorsement_proposed"
    ENDORSEMENT_ISSUED = "endorsement_issued"
    ENDORSEMENT_APPLIED = "endorsement_applied"
    # Collection chain.
    COLLECTION_SCHEDULED = "collection_scheduled"
    COLLECTION_IN_PROGRESS = "collection_in_progress"
    COLLECTION_OVERDUE = "collection_overdue"
    COLLECTION_SETTLED = "collection_settled"
    COLLECTION_SUSPENDED = "collection_suspended"
    # Claim chain.
    CLAIM_REPORTED = "claim_reported"
    CLAIM_ADJUSTING = "claim_adjusting"
    CLAIM_PRELIMINARY = "claim_preliminary"
    CLAIM_FINAL = "claim_final"
    CLAIM_SETTLED = "claim_settled"
    # Terminal.
    CLOSED = "closed"


class CaseFileStatus(StrEnum):
    OPEN = "open"
    ON_HOLD = "on_hold"
    WON = "won"
    LOST = "lost"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class CaseOrigin(StrEnum):
    """How an account folder came to exist — the ORIGIN axis of ``case_file``.

    Three axes never mix (docs/v3-groups-accounts-spec.md §1): origin
    (``origin`` + ``origin_case_file_id``: siblings in time), version
    (``supersedes_case_file_id``: rework) and post-sale (``parent_case_file_id``
    + ``policy_id``: children of a policy). A renewal folder is a SIBLING of the
    prior vigencia, never its child.
    """

    NEW = "new"
    RENEWAL = "renewal"
    PERIOD_CHANGE = "period_change"


class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    LOST = "lost"


class PackKind(StrEnum):
    """The three generated downloadables of an expediente."""

    SUBMISSION = "submission"
    COMPARISON = "comparison"
    PROPOSAL = "proposal"


class PackStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    GENERATED = "generated"
    SENT = "sent"
    FAILED = "failed"


class RecordExpedienteStatus(StrEnum):
    """Lifecycle of a per-account antecedentes expediente (v6).

    ``extract -> human validates & completes -> register`` (CLAUDE.md rule 6):
    ``draft`` (empty shell), ``processing`` (consolidation running),
    ``review`` (AI-suggested payload awaiting human validation, nothing
    committed), ``registered`` (human-confirmed and persisted).
    """

    DRAFT = "draft"
    PROCESSING = "processing"
    REVIEW = "review"
    REGISTERED = "registered"


# --- Post-sale ---------------------------------------------------------------


class EndorsementKind(StrEnum):
    """The 15 endorsement motives observed in the corpus, plus ``period_extension``.

    The capitalised verb in the carrier's ``motive`` text (INCLUYE / EXCLUYE /
    AUMENTA / DISMINUYE) is the signal the classifier keys on.

    ``PERIOD_EXTENSION`` (prórroga) is the one motive the corpus does not show:
    it is created only through ``POST /endorsements/batch`` (one endorsement per
    policy sharing a ``batch_key``) and moves ``policy.end_date`` only — never
    the account folder's period. 16 chars < ``aggregate_reinstatement`` (23),
    so the VARCHAR length is unchanged.
    """

    LOCATION_INCLUSION = "location_inclusion"
    LOCATION_EXCLUSION = "location_exclusion"
    VEHICLE_INCLUSION = "vehicle_inclusion"
    VEHICLE_EXCLUSION = "vehicle_exclusion"
    ADDITIONAL_INSURED = "additional_insured"
    ACTIVITY_EXTENSION = "activity_extension"
    AGGREGATE_REINSTATEMENT = "aggregate_reinstatement"
    ROSTER_INCREASE = "roster_increase"
    ROSTER_ADJUSTMENT = "roster_adjustment"
    PLEDGE_UPDATE = "pledge_update"
    POLICYHOLDER_CHANGE = "policyholder_change"
    SUM_INSURED_INCREASE = "sum_insured_increase"
    SUM_INSURED_DECREASE = "sum_insured_decrease"
    DEDUCTIBLE_REDUCTION = "deductible_reduction"
    OTHER = "other"
    PERIOD_EXTENSION = "period_extension"


class EndorsementStatus(StrEnum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    ISSUED = "issued"
    APPLIED = "applied"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PaymentMode(StrEnum):
    COUPON_BOOK = "coupon_book"
    DIRECT_DEBIT = "direct_debit"
    CARD_DEBIT = "card_debit"
    TRANSFER = "transfer"
    SINGLE_CHARGE = "single_charge"
    OTHER = "other"


class CollectionPlanStatus(StrEnum):
    PENDING = "pending"
    CURRENT = "current"
    OVERDUE = "overdue"
    SETTLED = "settled"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"
    REHABILITATED = "rehabilitated"


class InstallmentStatus(StrEnum):
    PENDING = "pending"
    DUE = "due"
    PAID = "paid"
    PAID_LATE = "paid_late"
    OVERDUE = "overdue"
    CREDITED = "credited"
    CANCELLED = "cancelled"


class WarrantySource(StrEnum):
    """Where an R-n / G-n / M-n obligation came from."""

    INSPECTION_RECOMMENDATION = "inspection_recommendation"
    UNDERWRITING_WARRANTY = "underwriting_warranty"
    ENGINEERING_MEASURE = "engineering_measure"


class WarrantyStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    MET_ON_TIME = "met_on_time"
    MET_LATE = "met_late"
    MET_AFTER_CLAIM = "met_after_claim"
    BREACHED = "breached"
    WAIVED = "waived"


class ClaimItemKind(StrEnum):
    """Per-partida damage families the adjuster's tables are grouped by."""

    MATERIAL_DAMAGE = "material_damage"
    BUSINESS_INTERRUPTION = "business_interruption"
    EXPENSE = "expense"
    LIABILITY = "liability"
    PERSONAL_ACCIDENT = "personal_accident"
    RECOVERY = "recovery"


class ClaimRuling(StrEnum):
    PENDING = "pending"
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    REJECTED = "rejected"


class ProposalOutcome(StrEnum):
    """How an insurer answered the submission, independent of proposal status."""

    QUOTED = "quoted"
    DECLINED = "declined"
    CONDITIONAL = "conditional"
    NO_RESPONSE = "no_response"


class DocumentDirection(StrEnum):
    """Who sent a document to whom — the corpus's direction of travel."""

    INSURED_TO_BROKER = "insured_to_broker"
    BROKER_TO_INSURERS = "broker_to_insurers"
    BROKER_TO_INSURER = "broker_to_insurer"
    BROKER_TO_INSURED = "broker_to_insured"
    BROKER_AND_INSURED_TO_INSURERS = "broker_and_insured_to_insurers"
    INSURER_TO_BROKER = "insurer_to_broker"
    THIRD_PARTY_TO_BROKER = "third_party_to_broker"
    ADJUSTER_TO_PARTIES = "adjuster_to_parties"
    INTERNAL = "internal"


# --- v12: closed catalogue, policies as the main view, cobranza --------------
# docs/v12/01-data-model.md is the contract. Every v12 enum lives HERE (not in
# its model module) so the parallel lanes find the whole vocabulary in one
# place; the model modules import from this file.


class SuggestedEntry(StrEnum):
    """How a subtipo is usually entered (team CSV ``entrada_sugerida``)."""

    MARKET = "market"  # CSV "mercado"
    PORTAL = "portal"


class PlacementRoute(StrEnum):
    """How a colocación is worked (team ``via_prevista``)."""

    TRADITIONAL = "traditional"  # full market process
    DIGITAL = "digital"  # insurer portal quote
    MANUAL = "manual"  # typed in by the broker


class CaseEntryOrigin(StrEnum):
    """Where an account folder was BORN in the partial journey (team ``origen``).

    Deliberately NOT ``CaseOrigin``: that enum is the v3 ORIGIN AXIS
    (new / renewal / period_change — siblings in time) and is already the
    NOT NULL ``case_file.origin`` column. This is an orthogonal fact — which
    door the work came through — so it lives in ``case_file.entry_origin``.
    """

    FULL_JOURNEY = "full_journey"
    QUOTE_COMPARISON = "quote_comparison"
    POLICY_UPLOAD = "policy_upload"
    MANUAL_ENTRY = "manual_entry"
    PORTAL_RPA = "portal_rpa"
    MIGRATION = "migration"


class PolicyOrigin(StrEnum):
    """How a policy entered Radal."""

    READER = "reader"  # uploaded PDF, AI-read then confirmed
    MANUAL = "manual"  # vía Manual
    JOURNEY = "journey"  # issued at the end of a full journey
    PORTAL_RPA = "portal_rpa"
    MIGRATION = "migration"


class PolicyCurrency(StrEnum):
    """Currency a policy (and its cuotas) is denominated in. ISO-style codes."""

    UF = "UF"
    USD = "USD"


class PaymentCurrency(StrEnum):
    """Currency a payment was actually made in (CLP is converted to UF)."""

    UF = "UF"
    USD = "USD"
    CLP = "CLP"


class TerminationReason(StrEnum):
    NON_PAYMENT = "non_payment"
    INSURED_REQUEST = "insured_request"
    INSURER_REQUEST = "insurer_request"
    OTHER = "other"


class PartyRole(StrEnum):
    """Role of a person/company on a policy. Payer defaults to policyholder,
    policyholder defaults to insured (service rule)."""

    INSURED = "insured"
    POLICYHOLDER = "policyholder"
    PAYER = "payer"
    BENEFICIARY = "beneficiary"
    ADDITIONAL_INSURED = "additional_insured"


class CollectionPlanOrigin(StrEnum):
    POLICY = "policy"
    ENDORSEMENT = "endorsement"
    EXTENSION = "extension"


class CollectionPlanSource(StrEnum):
    READER = "reader"
    MANUAL = "manual"


class CollectionLevelPreference(StrEnum):
    """The broker's choice per plan; ``auto`` lets the resolver pick native."""

    AUTO = "auto"
    ASSISTED = "assisted"
    RECORD = "record"


class PaymentSource(StrEnum):
    MANUAL = "manual"
    INSURER_STATEMENT = "insurer_statement"
    INSURER_REPLY = "insurer_reply"
    INSURER_SHARE_LINK = "insurer_share_link"
    PROVIDER = "provider"
    PAYER_UPLOAD = "payer_upload"
    AGENT = "agent"


class EvidenceLevel(StrEnum):
    """How sure we are a payment happened. ORDERED weakest -> strongest."""

    DECLARED = "declared"
    REPORTED = "reported"
    INSURER_CONFIRMED = "insurer_confirmed"
    PROVIDER_CONFIRMED = "provider_confirmed"
    SETTLED = "settled"


class PaymentStatus(StrEnum):
    """A payment is never deleted; it is reversed."""

    ACTIVE = "active"
    REVERSED = "reversed"


class ReversalReason(StrEnum):
    ERROR = "error"
    DIRECT_DEBIT_REJECTED = "direct_debit_rejected"
    CHARGEBACK = "chargeback"
    OTHER = "other"


class MailSender(StrEnum):
    """Who sends broker mail: the Radal system mailbox or a connected account."""

    RADAL = "radal"
    BROKER_ACCOUNT = "broker_account"


class NoticeKind(StrEnum):
    PAYER_REMINDER = "payer_reminder"
    PAYER_OVERDUE = "payer_overdue"
    PAYMENT_LINK = "payment_link"
    INSURER_STATUS_INQUIRY = "insurer_status_inquiry"


class NoticeChannel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"  # open door only — never sent (Meta approval pending)


class NoticeStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    SKIPPED = "skipped"


class NotificationTray(StrEnum):
    TODO = "todo"
    FYI = "fyi"


class NotificationKind(StrEnum):
    INSTALLMENT_DUE = "installment_due"
    INSTALLMENT_OVERDUE = "installment_overdue"
    TERMINATION_RISK = "termination_risk"
    DIRECT_DEBIT_REJECTED = "direct_debit_rejected"
    PROMISE_BROKEN = "promise_broken"
    PLAN_MISSING = "plan_missing"
    INQUIRY_REPLY = "inquiry_reply"
    EVIDENCE_PENDING = "evidence_pending"
    PAYMENT_RECEIVED = "payment_received"
    NATIVE_COLLECTION_AVAILABLE = "native_collection_available"
    PORTAL_SEARCH_FAILED = "portal_search_failed"
    PORTAL_SEARCH_DONE = "portal_search_done"
    MAIL_ACCOUNT_ERROR = "mail_account_error"
    SENSITIVE_ACTION = "sensitive_action"
    #: 2b.5 FYI — a policy registered with no folder was attached to an insured
    #: already in the portfolio (21 chars: the column stays 27 + slack, no widening).
    POLICY_LINKED_INSURED = "policy_linked_insured"


class NotificationStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"
    CLOSED = "closed"


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class OnboardingStatus(StrEnum):
    """Radal-internal Forpay onboarding of an insurer. NEVER shown to brokers."""

    NOT_CONTACTED = "not_contacted"
    IN_CONVERSATION = "in_conversation"
    ONBOARDING = "onboarding"
    SANDBOX = "sandbox"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PaymentProviderName(StrEnum):
    FORPAY = "forpay"


class ProviderOwner(StrEnum):
    RADAL_MALL = "radal_mall"
    INSURER = "insurer"


class ProviderEnvironment(StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class ProviderAccountStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class PaymentChannel(StrEnum):
    WEBPAY = "webpay"
    ONECLICK = "oneclick"
    BANK_TRANSFER = "bank_transfer"
    DIRECT_DEBIT = "direct_debit"


class ChannelStatus(StrEnum):
    PENDING = "pending"
    SANDBOX = "sandbox"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class IntentStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class EventProcessing(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class MandateStatus(StrEnum):
    PENDING = "pending"
    BANK_CONFIRMATION = "bank_confirmation"
    ACTIVE = "active"
    REVOKED = "revoked"
    FAILED = "failed"


class MailProvider(StrEnum):
    RADAL_SYSTEM = "radal_system"
    CUSTOM = "custom"
    GOOGLE = "google"  # reserved — OAuth not built ("pronto")
    MICROSOFT = "microsoft"  # reserved — OAuth not built ("pronto")


class MailSecurity(StrEnum):
    SSL = "ssl"
    STARTTLS = "starttls"
    NONE = "none"


class MailAccountStatus(StrEnum):
    DRAFT = "draft"
    CONNECTED = "connected"
    ERROR = "error"
    PAUSED = "paused"


class MailDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class InboundClass(StrEnum):
    INSURER_STATUS_REPLY = "insurer_status_reply"
    INSURER_STATEMENT = "insurer_statement"
    CANCELLATION_NOTICE = "cancellation_notice"
    PAYER_REPLY = "payer_reply"
    BOUNCE = "bounce"
    UNRELATED = "unrelated"


class MailMessageStatus(StrEnum):
    """``skipped`` = recorded but not sent because ``MAIL_ENABLED`` is off."""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"


class InquiryStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    REPLIED = "replied"
    RESOLVED = "resolved"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceSource(StrEnum):
    INSURER_REPLY = "insurer_reply"
    INSURER_SHARE_LINK = "insurer_share_link"
    INSURER_STATEMENT = "insurer_statement"
    PAYER_UPLOAD = "payer_upload"


class ReportedStatus(StrEnum):
    PAID = "paid"
    UNPAID = "unpaid"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class EvidenceStatus(StrEnum):
    SUGGESTED = "suggested"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ShareEntity(StrEnum):
    POLICY = "policy"
    COLLECTION_PLAN = "collection_plan"
    INSTALLMENT = "installment"
    CASE_FILE = "case_file"
    STATUS_INQUIRY = "status_inquiry"


class ShareAudience(StrEnum):
    INSURED = "insured"
    INSURER = "insurer"


class CredentialStatus(StrEnum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"


class PortalRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PortalErrorKind(StrEnum):
    CREDENTIALS_MISSING = "credentials_missing"
    LOGIN_FAILED = "login_failed"
    LAYOUT_CHANGED = "layout_changed"
    TIMEOUT = "timeout"
    CAPTCHA = "captcha"
    NO_RESULTS = "no_results"
    PORTAL_UNAVAILABLE = "portal_unavailable"
    UNEXPECTED = "unexpected"


V12_ENUMS = [
    "SuggestedEntry",
    "PlacementRoute",
    "CaseEntryOrigin",
    "PolicyOrigin",
    "PolicyCurrency",
    "PaymentCurrency",
    "TerminationReason",
    "PartyRole",
    "CollectionPlanOrigin",
    "CollectionPlanSource",
    "CollectionLevelPreference",
    "PaymentSource",
    "EvidenceLevel",
    "PaymentStatus",
    "ReversalReason",
    "MailSender",
    "NoticeKind",
    "NoticeChannel",
    "NoticeStatus",
    "NotificationTray",
    "NotificationKind",
    "NotificationStatus",
    "JobStatus",
    "OnboardingStatus",
    "PaymentProviderName",
    "ProviderOwner",
    "ProviderEnvironment",
    "ProviderAccountStatus",
    "PaymentChannel",
    "ChannelStatus",
    "IntentStatus",
    "EventProcessing",
    "MandateStatus",
    "MailProvider",
    "MailSecurity",
    "MailAccountStatus",
    "MailDirection",
    "InboundClass",
    "MailMessageStatus",
    "InquiryStatus",
    "EvidenceSource",
    "ReportedStatus",
    "EvidenceStatus",
    "ShareEntity",
    "ShareAudience",
    "CredentialStatus",
    "PortalRunStatus",
    "PortalErrorKind",
]


__all__ = [
    "sql_enum",
    "StrEnum",
    "EntityType",
    "CoverageKind",
    "UserType",
    "PersonType",
    "Priority",
    # case files
    "CaseSection",
    "CaseFileKind",
    "CaseStage",
    "CaseFileStatus",
    "CaseOrigin",
    "LeadStatus",
    "PackKind",
    "PackStatus",
    "RecordExpedienteStatus",
    # post-sale
    "EndorsementKind",
    "EndorsementStatus",
    "PaymentMode",
    "CollectionPlanStatus",
    "InstallmentStatus",
    "WarrantySource",
    "WarrantyStatus",
    "ClaimItemKind",
    "ClaimRuling",
    "ProposalOutcome",
    "DocumentDirection",
] + V12_ENUMS
