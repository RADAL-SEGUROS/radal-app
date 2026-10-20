"""Number-based targeting of cuotas and policies for the agent (review round 3, R1).

The agent used to name a cuota only by its internal ``installment_id``. When the
cuota the person asked for was not among the ≤ 30 rows ``list_installments``
returned, the model *guessed* an id — round 2 showed "Cuota 68", round 3 proposed
paying cuota 5 of V-FL-0091447 when asked for cuota 2 of TRBF-0020126441. A one-click
"Confirmar" would have booked UF 131,69 on another policy.

The fix moves the targeting server-side: the cuota tools take what the broker says
— ``policy_number`` + ``installment_number`` — and this module resolves it inside
the caller's broker:

* the policy number matches **exactly** after normalisation (case, spaces, dashes,
  dots, slashes, underscores and a leading "N°" / "Póliza" are ignored; leading zeros
  are NOT — ``0020119904`` is not ``20119904``);
* nothing found, two policies sharing the number, a cuota number that does not
  exist or exists twice → :class:`TargetRefused` (a 422 whose ``detail`` is
  ``{code, message, …}`` in Spanish) — the proposal is refused and the agent relays
  the message; it is never a guess;
* an ``installment_id`` stays accepted, but when it comes WITH numbers they must
  name the same cuota, or the proposal is refused (``target_mismatch``) — the exact
  round-3 failure.

Pure lookups: nothing here writes. Every query filters ``broker_id`` (another
broker's policy simply does not exist here).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

__all__ = [
    "TARGET_REFUSAL_CODES",
    "TargetRefused",
    "normalize_policy_number",
    "policies_by_number",
    "resolve_policy",
    "resolve_installment",
    "resolve_installments",
]

#: The ``detail.code`` values this module raises (the agent folds them as
#: ``target_refused`` so the model relays the message instead of retrying).
TARGET_REFUSAL_CODES = frozenset(
    {
        "policy_not_found",
        "policy_ambiguous",
        "installment_not_found",
        "installment_ambiguous",
        "target_mismatch",
        "target_missing",
    }
)

#: Characters a policy number may be typed with or without.
_STRIP_CHARS = " \t\r\n-‐‑‒–—./_"
_PREFIX_RE = re.compile(
    r"^\s*(?:p[oó]liza\s*)?(?:n[°º]|nro\.?|n[uú]m(?:ero)?\.?|no\.)?\s*", re.IGNORECASE
)


class TargetRefused(HTTPException):
    """A 422 with a structured Spanish ``detail`` — the proposal is not made."""

    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": code, "message": message, **extra},
        )


def normalize_policy_number(value: Any) -> str:
    """``"N° TRBF 0020126441"`` / ``"trbf-0020126441"`` → ``"TRBF0020126441"``."""
    text = _PREFIX_RE.sub("", str(value or ""), count=1)
    return "".join(ch for ch in text.upper() if ch not in _STRIP_CHARS)


def _normalized_column(column: Any) -> Any:
    """The SQL twin of :func:`normalize_policy_number` (without the prefix rule —
    stored numbers carry no "N°")."""
    expr = func.upper(column)
    for ch in _STRIP_CHARS:
        if ch in "\t\r\n":
            continue
        expr = func.replace(expr, ch, "")
    return expr


def _policy_text(db: Session, policy: Any) -> str:
    from app.services.agent_display import day  # noqa: PLC0415

    insurer = getattr(policy, "insurer", None)
    name = (insurer.trade_name or insurer.legal_name) if insurer is not None else None
    parts = [f"N° {policy.policy_number}"]
    if name:
        parts.append(name)
    if policy.start_date or policy.end_date:
        parts.append(f"vigencia {day(policy.start_date)} a {day(policy.end_date)}")
    return " · ".join(parts)


def policies_by_number(db: Session, broker_id: int, number: Any) -> list[Any]:
    """Every policy of the broker whose number equals ``number`` once normalised."""
    from app.models.policy import Policy  # noqa: PLC0415
    from app.services.policy_registry import PLACEHOLDER_NUMBER_PREFIX  # noqa: PLC0415

    wanted = normalize_policy_number(number)
    if not wanted:
        return []
    return list(
        db.scalars(
            select(Policy)
            .where(
                Policy.broker_id == broker_id,
                Policy.policy_number.is_not(None),
                ~Policy.policy_number.startswith(PLACEHOLDER_NUMBER_PREFIX),
                _normalized_column(Policy.policy_number) == wanted,
            )
            .order_by(Policy.start_date.desc(), Policy.id.desc())
        ).all()
    )


def _not_found(number: Any) -> TargetRefused:
    return TargetRefused(
        "policy_not_found",
        f"No encontré la póliza N° {str(number).strip()} en la cartera de la corredora. "
        "Revisa el número con la persona; no propongo nada sobre otra póliza.",
        policy_number=str(number).strip(),
    )


def _ambiguous(db: Session, number: Any, policies: list[Any]) -> TargetRefused:
    options = "; ".join(_policy_text(db, p) for p in policies[:5])
    return TargetRefused(
        "policy_ambiguous",
        f"Hay {len(policies)} pólizas con el N° {str(number).strip()} ({options}). "
        "Pregunta a la persona de qué compañía o vigencia se trata antes de proponer algo.",
        policy_number=str(number).strip(),
        candidates=[
            {"policy_id": p.id, "label": _policy_text(db, p)} for p in policies[:5]
        ],
    )


def _installments_of(db: Session, broker_id: int, policy_ids: Iterable[int]) -> list[Any]:
    from app.models.collection import CollectionInstallment, CollectionPlan  # noqa: PLC0415

    ids = list(policy_ids)
    if not ids:
        return []
    return list(
        db.scalars(
            select(CollectionInstallment)
            .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
            .where(
                CollectionInstallment.broker_id == broker_id,
                CollectionPlan.broker_id == broker_id,
                CollectionPlan.policy_id.in_(ids),
            )
            .order_by(CollectionInstallment.number, CollectionInstallment.id)
        ).all()
    )


def _cuota_text(db: Session, broker_id: int, inst: Any) -> str:
    from app.services.agent_display import _cuota  # noqa: PLC0415

    return _cuota(db, broker_id, inst.id) or f"{inst.number}"


def resolve_policy(
    db: Session,
    broker_id: int,
    *,
    policy_id: int | None = None,
    policy_number: str | None = None,
) -> Any:
    """The ONE policy named by ``policy_number`` (preferred) and/or ``policy_id``.

    Both given → they must be the same policy (``target_mismatch``). Only an id → the
    broker-scoped lookup (``not_found`` 404, as before)."""
    from app.api.routers.policies import get_policy_or_404  # noqa: PLC0415

    by_id = get_policy_or_404(db, policy_id, broker_id) if policy_id else None
    if not policy_number:
        if by_id is None:
            raise TargetRefused(
                "target_missing", "Indica el N° de póliza (policy_number)."
            )
        return by_id
    matches = policies_by_number(db, broker_id, policy_number)
    if by_id is not None:
        if by_id.id not in {p.id for p in matches}:
            raise TargetRefused(
                "target_mismatch",
                f"La póliza indicada por id ({_policy_text(db, by_id)}) no es la póliza "
                f"N° {str(policy_number).strip()}. No propongo nada: confirma el número con "
                "la persona.",
            )
        return by_id
    if not matches:
        raise _not_found(policy_number)
    if len(matches) > 1:
        raise _ambiguous(db, policy_number, matches)
    return matches[0]


def resolve_installment(
    db: Session,
    broker_id: int,
    *,
    installment_id: int | None = None,
    policy_number: str | None = None,
    installment_number: int | None = None,
) -> Any:
    """The ONE cuota named by ``policy_number`` + ``installment_number`` and/or
    ``installment_id`` — refused (never guessed) when missing, ambiguous or when the
    id and the numbers disagree."""
    from app.api.routers.collections import get_installment_or_404  # noqa: PLC0415

    by_id = (
        get_installment_or_404(db, installment_id, broker_id) if installment_id else None
    )
    if by_id is not None:
        policy = by_id.collection_plan.policy if by_id.collection_plan else None
        wrong_policy = policy_number is not None and (
            policy is None
            or normalize_policy_number(policy.policy_number)
            != normalize_policy_number(policy_number)
        )
        wrong_number = installment_number is not None and int(by_id.number) != int(
            installment_number
        )
        if wrong_policy or wrong_number:
            asked = (
                f"la cuota {installment_number if installment_number is not None else by_id.number}"
                f" de la póliza N° {str(policy_number).strip() if policy_number else policy.policy_number}"
            )
            raise TargetRefused(
                "target_mismatch",
                f"La cuota indicada por id (cuota {_cuota_text(db, broker_id, by_id)}) no es "
                f"{asked}. No propongo nada sobre otra cuota: confirma la póliza y el número "
                "de cuota con la persona.",
            )
        return by_id

    if not policy_number or installment_number is None:
        raise TargetRefused(
            "target_missing",
            "Indica la cuota por su póliza y número: policy_number + installment_number.",
        )
    policies = policies_by_number(db, broker_id, policy_number)
    if not policies:
        raise _not_found(policy_number)
    insts = _installments_of(db, broker_id, [p.id for p in policies])
    hits = [i for i in insts if int(i.number) == int(installment_number)]
    if len(policies) > 1:
        owners = {i.collection_plan.policy_id for i in hits}
        if len(owners) != 1:
            raise _ambiguous(db, policy_number, policies)
    if not hits:
        numbers = sorted({int(i.number) for i in insts})
        if not numbers:
            have = "no tiene plan de pago cargado"
        else:
            have = f"tiene las cuotas {numbers[0]} a {numbers[-1]}"
        raise TargetRefused(
            "installment_not_found",
            f"La póliza N° {policies[0].policy_number} no tiene una cuota {installment_number} "
            f"({have}). Confirma el número de cuota con la persona.",
            policy_number=policies[0].policy_number,
            installment_number=int(installment_number),
        )
    if len(hits) > 1:
        raise TargetRefused(
            "installment_ambiguous",
            f"La póliza N° {policies[0].policy_number} tiene {len(hits)} cuotas número "
            f"{installment_number} (planes de pago distintos). Pide a la persona que la "
            "registre desde Cobranza.",
            policy_number=policies[0].policy_number,
            installment_number=int(installment_number),
        )
    return get_installment_or_404(db, hits[0].id, broker_id)


def resolve_installments(
    db: Session,
    broker_id: int,
    *,
    installment_ids: list[int] | None = None,
    policy_number: str | None = None,
    installment_numbers: list[int] | None = None,
    policy: Any = None,
) -> list[Any]:
    """Several cuotas of ONE policy, by number (preferred) and/or id; same rules.

    ``policy`` (already resolved) skips the number lookup. Ids and numbers given
    together must name the same set (``target_mismatch``)."""
    from app.api.routers.collections import get_installment_or_404  # noqa: PLC0415

    by_ids = [
        get_installment_or_404(db, i, broker_id) for i in dict.fromkeys(installment_ids or [])
    ]
    if installment_numbers:
        if policy is None:
            policy = resolve_policy(db, broker_id, policy_number=policy_number)
        insts = _installments_of(db, broker_id, [policy.id])
        by_number: list[Any] = []
        for number in dict.fromkeys(int(n) for n in installment_numbers):
            hits = [i for i in insts if int(i.number) == number]
            if not hits:
                raise TargetRefused(
                    "installment_not_found",
                    f"La póliza N° {policy.policy_number} no tiene una cuota {number}. "
                    "Confirma los números de cuota con la persona.",
                    policy_number=policy.policy_number,
                    installment_number=number,
                )
            if len(hits) > 1:
                raise TargetRefused(
                    "installment_ambiguous",
                    f"La póliza N° {policy.policy_number} tiene {len(hits)} cuotas número "
                    f"{number} (planes de pago distintos).",
                    policy_number=policy.policy_number,
                    installment_number=number,
                )
            by_number.append(hits[0])
        if by_ids and {i.id for i in by_ids} != {i.id for i in by_number}:
            raise TargetRefused(
                "target_mismatch",
                "Las cuotas indicadas por id no son las cuotas "
                f"{', '.join(str(n) for n in installment_numbers)} de la póliza "
                f"N° {policy.policy_number}. No propongo nada: confirma las cuotas con la persona.",
            )
        return [get_installment_or_404(db, i.id, broker_id) for i in by_number]
    if by_ids and policy_number:
        wanted = normalize_policy_number(policy_number)
        for inst in by_ids:
            owner = inst.collection_plan.policy if inst.collection_plan else None
            if owner is None or normalize_policy_number(owner.policy_number) != wanted:
                raise TargetRefused(
                    "target_mismatch",
                    f"La cuota {_cuota_text(db, broker_id, inst)} no es de la póliza "
                    f"N° {str(policy_number).strip()}. No propongo nada: confirma la póliza "
                    "con la persona.",
                )
    return by_ids
