"""The Σ-cuotas ledger rule's endorsement term — ONE implementation.

Σ ``collection_installment.gross_amount_uf`` must equal the policy's gross
premium + the endorsement deltas that are real but NOT yet folded into it.
Both doors that write a plan use this: the collections router (manual plans,
cuota edits) and ``extraction_commit`` (cuotas read from an uploaded policy),
so the two Σ checks cannot drift again.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.endorsement import Endorsement
from app.models.enums import EndorsementStatus


def endorsement_delta_total(db: Session, *, broker_id: int, policy_id: int) -> Decimal:
    """Σ of the deltas that are real but NOT yet folded into the policy premium.

    ``issued`` means the carrier's folio exists; ``applied`` means Radal has
    already moved ``policy.total_premium_uf`` by that delta (``POST
    /endorsements/{id}/issue`` does both in one transaction). Only the former is
    added on top of the policy's premium — otherwise the corpus invariant would
    count the same movement twice. Draft / proposed / rejected / cancelled
    deltas are not real and never count.
    """
    total = db.scalar(
        select(func.coalesce(func.sum(Endorsement.total_premium_delta_uf), 0)).where(
            Endorsement.broker_id == broker_id,
            Endorsement.policy_id == policy_id,
            Endorsement.status == EndorsementStatus.ISSUED,
        )
    )
    return Decimal(str(total)) if total is not None else Decimal("0")


__all__ = ["endorsement_delta_total"]
