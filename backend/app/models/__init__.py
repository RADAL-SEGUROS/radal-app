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
    CoverageKind,
    EntityType,
    PersonType,
    Priority,
    UserType,
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
    ClaimStatus,
    CoinsuranceShare,
    CoverageItem,
    Policy,
    PolicyLocation,
    PolicyStatus,
)
from app.models.offering import Offering, OfferingChannel, OfferingStatus
from app.models.document import Document, DocumentCategory
from app.models.activity import Activity, Note

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
