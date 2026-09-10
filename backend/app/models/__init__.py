"""SQLAlchemy models (v2) — ENGLISH identifiers everywhere.

Every model module is imported HERE so that importing this package registers all
tables on ``Base.metadata`` for ``create_all()``. See docs/v2-architecture.md §3
and docs/v2-data-modeling-decisions.md for the columns-vs-JSON-vs-child-table
rationale behind each shape.

Tenancy: every workspace table carries ``broker_id``. ``insured``, ``insurer``
and ``cmf_line`` are canonical (cross-broker) and deliberately have none.
"""
from app.models.base_class import Base, TimestampMixin, utcnow
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    CaseOrigin,
    CaseSection,
    CaseStage,
    ClaimItemKind,
    ClaimRuling,
    CollectionPlanStatus,
    CoverageKind,
    DocumentDirection,
    EndorsementKind,
    EndorsementStatus,
    EntityType,
    InstallmentStatus,
    LeadStatus,
    PackKind,
    PackStatus,
    PaymentMode,
    PersonType,
    Priority,
    ProposalOutcome,
    RecordExpedienteStatus,
    UserType,
    WarrantySource,
    WarrantyStatus,
    sql_enum,
)

# --- Identity & organisations ------------------------------------------------
from app.models.broker import Broker, BrokerStatus
from app.models.user import User
from app.models.insured import Insured
from app.models.insurer import (
    Insurer,
    InsurerContact,
    InsurerStatus,
    NativeInsurerProfile,
)

# --- Broker workspace --------------------------------------------------------
from app.models.account_group import (
    AccountGroup,
    AccountGroupIconKind,
    AccountGroupStatus,
)
from app.models.client import Client, ClientStatus
from app.models.asset import Asset, AssetStatus
from app.models.insurance_line import (
    CmfLine,
    CmfLineKind,
    InsuranceLine,
    InsuranceLineCmfCode,
)
from app.models.placement import Placement, PlacementStatus
from app.models.quote import QuoteLineItem, QuoteRequest, QuoteRequestStatus
from app.models.proposal import (
    Proposal,
    ProposalCoverage,
    ProposalOrigin,
    ProposalStatus,
)
from app.models.inspection import (
    Inspection,
    InspectionBoundary,
    InspectionRequest,
    InspectionRequestStatus,
    InspectionStatus,
)
from app.models.policy import (
    Claim,
    ClaimItem,
    ClaimStatus,
    CoinsuranceShare,
    CoverageItem,
    Policy,
    PolicyLocation,
    PolicyStatus,
)
from app.models.comparison import (
    Comparison,
    ComparisonEntry,
    ComparisonSource,
    ComparisonStatus,
)
from app.models.broker_proposal import BrokerProposal, BrokerProposalStatus
from app.models.offering import Offering, OfferingChannel, OfferingStatus
from app.models.document import Document, DocumentCategory
from app.models.activity import Activity, Note

# --- Case files (expedientes) + post-sale -------------------------------------
from app.models.case_file import CaseFile, CaseFileStageEvent, CasePack
from app.models.line_record_schema import LineRecordSchema
from app.models.record_expediente import RecordExpediente
from app.models.account_client import AccountClient, AccountClientRole
from app.models.sales_lead import SalesLead
from app.models.endorsement import Endorsement
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.warranty import Warranty

# --- Access & AI -------------------------------------------------------------
from app.models.access import (
    AccountRequestOrigin,
    AccountRequestStatus,
    InsuredAccountRequest,
)
from app.models.ai import (
    AgentMessage,
    AgentRole,
    AgentScope,
    AgentThread,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
)

__all__ = [
    # base
    "Base",
    "TimestampMixin",
    "utcnow",
    "sql_enum",
    # shared enums
    "EntityType",
    "CoverageKind",
    "UserType",
    "PersonType",
    "Priority",
    "CaseSection",
    "CaseFileKind",
    "CaseStage",
    "CaseFileStatus",
    "CaseOrigin",
    "LeadStatus",
    "PackKind",
    "PackStatus",
    "RecordExpedienteStatus",
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
    # identity & orgs
    "Broker",
    "BrokerStatus",
    "User",
    "Insured",
    "Insurer",
    "InsurerStatus",
    "NativeInsurerProfile",
    "InsurerContact",
    # workspace
    "AccountGroup",
    "AccountGroupStatus",
    "AccountGroupIconKind",
    "Client",
    "ClientStatus",
    "Asset",
    "AssetStatus",
    "CmfLine",
    "CmfLineKind",
    "InsuranceLine",
    "InsuranceLineCmfCode",
    "Placement",
    "PlacementStatus",
    "QuoteRequest",
    "QuoteRequestStatus",
    "QuoteLineItem",
    "Proposal",
    "ProposalOrigin",
    "ProposalStatus",
    "ProposalCoverage",
    "InspectionRequest",
    "InspectionRequestStatus",
    "Inspection",
    "InspectionStatus",
    "InspectionBoundary",
    "Policy",
    "PolicyStatus",
    "CoinsuranceShare",
    "PolicyLocation",
    "CoverageItem",
    "Claim",
    "ClaimStatus",
    "ClaimItem",
    # comparison + outbound propuesta (v8)
    "Comparison",
    "ComparisonEntry",
    "ComparisonSource",
    "ComparisonStatus",
    "BrokerProposal",
    "BrokerProposalStatus",
    # case files + post-sale
    "CaseFile",
    "CaseFileStageEvent",
    "CasePack",
    "LineRecordSchema",
    "RecordExpediente",
    "AccountClient",
    "AccountClientRole",
    "SalesLead",
    "Endorsement",
    "CollectionPlan",
    "CollectionInstallment",
    "Warranty",
    "Offering",
    "OfferingChannel",
    "OfferingStatus",
    "Document",
    "DocumentCategory",
    "Activity",
    "Note",
    # access & AI
    "InsuredAccountRequest",
    "AccountRequestOrigin",
    "AccountRequestStatus",
    "Extraction",
    "ExtractionKind",
    "ExtractionStatus",
    "AgentThread",
    "AgentScope",
    "AgentMessage",
    "AgentRole",
]
