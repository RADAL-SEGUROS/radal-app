"""``payment_plan`` (09 plan de pago) — insurer -> broker.

Confirming it writes ``collection_plan`` + ``collection_installment``. The sum
rule (Σ instalments == gross premium + Σ endorsement deltas) is enforced by the
collections schema layer at confirm time, not here.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    InstallmentRow,
    Int,
    PartyRef,
    Pct,
    RootExtraction,
    Str,
)

__all__ = ["PaymentPlanExtraction", "EndorsementPaymentPlan"]


class EndorsementPaymentPlan(ExtractionModel):
    """A plan issued for an endorsement rather than the base policy."""

    endorsement_number: Str = None
    total_premium_uf: UF = None
    installment_count: Int = None
    note: Str = None


class PaymentPlanExtraction(RootExtraction):
    payment_plan_number: Str = None
    policy_number: Str = None
    insurer: Str = None
    policyholder: PartyRef = Field(default_factory=PartyRef)
    responsible_payer: PartyRef = Field(default_factory=PartyRef)

    total_premium_uf: UF = None
    payment_mode: Str = Field(
        default=None,
        description="coupon_book | direct_debit | card_debit | transfer | single_charge",
    )
    installment_count: Int = None
    installments: list[InstallmentRow] = Field(default_factory=list)
    monthly_interest_rate_pct: Pct = None

    uf_conversion_clause: Str = Field(default=None, description="Verbatim UF clause")
    nonpayment_clause: Str = Field(
        default=None, description="Verbatim art. 528 / non-payment clause"
    )
    forms_part_of_policy: bool | None = None
    endorsement_payment_plan: EndorsementPaymentPlan = Field(
        default_factory=EndorsementPaymentPlan
    )
    bank: Str = None
    account_number: Str = None
