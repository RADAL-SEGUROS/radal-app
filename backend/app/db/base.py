"""Import Base + ALL models so metadata is complete for create_all().

``app.models`` imports every model module, which registers every table on
``Base.metadata``. The explicit re-exports below make the dependency obvious to
readers and to linters, and guarantee that importing this module alone is enough
to build the whole schema.
"""
from app.models.base_class import Base  # noqa: F401

# Importing the models package registers every table on Base.metadata.
import app.models  # noqa: F401,E402
from app.models import (  # noqa: F401,E402
    Activity,
    AgentMessage,
    AgentThread,
    Asset,
    Broker,
    Claim,
    Client,
    CmfLine,
    CoinsuranceShare,
    CoverageItem,
    Document,
    Extraction,
    Inspection,
    InspectionBoundary,
    InspectionRequest,
    InsuranceLine,
    InsuranceLineCmfCode,
    Insured,
    InsuredAccountRequest,
    Insurer,
    InsurerContact,
    NativeInsurerProfile,
    Note,
    Offering,
    Placement,
    Policy,
    PolicyLocation,
    Proposal,
    ProposalCoverage,
    QuoteLineItem,
    QuoteRequest,
    User,
)

__all__ = ["Base"]
