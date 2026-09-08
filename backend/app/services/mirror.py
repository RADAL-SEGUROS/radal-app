"""Mirror-diff: the issuance proposal (07) against the issued policy (08).

Not an LLM feature (spec §7.6). It is a deterministic, field-by-field comparison
of the two extraction payloads:

* the ``issuance_proposal`` the broker sent to the winning insurer, and
* the ``policy`` the insurer actually issued.

**Confirmed payloads win.** Per §7.6 each side prefers the extraction a human
confirmed; when none is confirmed yet, the newest SUCCEEDED parse is used as a
documented fallback and the response flags which one served
(``proposal_source`` / ``policy_source``: ``confirmed | latest_succeeded``), so
the UI can label a provisional diff as such instead of hiding it.

Alignment follows the registry's field list where it can (``spec_for(category)``
exposes the Pydantic schema, whose ``model_fields`` name the payload keys), and
falls back to :data:`MIRROR_FIELDS` — an explicit, hand-checked map — for the
things whose names differ on the two documents. Coverages align on their number,
deductibles on the peril key, money on the five premium fields.

Rule 4 of §3.2 applies: we NEVER reconcile across documents. Each side keeps its
own values and the difference is surfaced as a review item, which the broker may
then queue as ``endorsement(status=draft)``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import Extraction, ExtractionStatus
from app.models.document import DocumentCategory
from app.models.endorsement import Endorsement
from app.models.enums import EndorsementKind, EndorsementStatus
from app.models.policy import Policy
from app.schemas.proposal import MONEY_TOLERANCE

__all__ = [
    "MirrorDiffRow",
    "MIRROR_FIELDS",
    "MirrorSources",
    "load_sources",
    "mirror_diff",
    "queue_endorsements",
]

#: Severities, ordered worst first — the UI sorts on this.
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


@dataclass(frozen=True)
class MirrorDiffRow:
    """One discrepancy between what was proposed and what was issued."""

    path: str
    expected: Any
    found: Any
    severity: str
    suggested_endorsement_kind: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _FieldRule:
    """How one scalar field is aligned across the two payloads."""

    path: str
    proposal_keys: tuple[str, ...]
    policy_keys: tuple[str, ...]
    severity: str = SEVERITY_MEDIUM
    endorsement_kind: str | None = None
    numeric: bool = False


#: The hand-checked alignment. ``proposal_keys``/``policy_keys`` are tried in
#: order, so a registry rename does not silently drop a comparison.
MIRROR_FIELDS: tuple[_FieldRule, ...] = (
    # --- the five premium fields (money aligns on these, §7.6) --------------
    _FieldRule(
        "premium.net_taxable_uf",
        ("net_taxable", "net_premium_taxable_uf", "net_taxable_uf"),
        ("net_taxable", "net_premium_taxable_uf", "net_taxable_uf"),
        SEVERITY_HIGH, None, True,
    ),
    _FieldRule(
        "premium.net_exempt_uf",
        ("net_exempt", "net_premium_exempt_uf", "net_exempt_uf"),
        ("net_exempt", "net_premium_exempt_uf", "net_exempt_uf"),
        SEVERITY_HIGH, None, True,
    ),
    _FieldRule(
        "premium.net_total_uf",
        ("net_total", "net_premium_total_uf", "net_total_uf"),
        ("net_total", "net_premium_total_uf", "net_total_uf"),
        SEVERITY_HIGH, None, True,
    ),
    _FieldRule("premium.vat_uf", ("vat_uf",), ("vat_uf",), SEVERITY_HIGH, None, True),
    _FieldRule(
        "premium.gross_uf",
        ("gross_uf", "gross_premium_uf"),
        ("gross_uf", "gross_premium_uf"),
        SEVERITY_HIGH, None, True,
    ),
    # --- the insured amount and the period ----------------------------------
    _FieldRule(
        "total_insured_amount_uf",
        ("total_insured_amount_uf",),
        ("total_insured_amount_uf",),
        SEVERITY_HIGH,
        EndorsementKind.SUM_INSURED_INCREASE.value,
        True,
    ),
    _FieldRule(
        "aggregate_limit_uf",
        ("aggregate_limit_uf",),
        ("aggregate_limit_uf",),
        SEVERITY_MEDIUM, None, True,
    ),
    _FieldRule(
        "period_start_at",
        ("period_start", "period_start_at"),
        ("period_start_at", "period_start"),
        SEVERITY_HIGH,
    ),
    _FieldRule(
        "period_end_at",
        ("period_end", "period_end_at"),
        ("period_end_at", "period_end"),
        SEVERITY_HIGH,
    ),
    # --- technical identity --------------------------------------------------
    _FieldRule("cover_mode", ("cover_mode",), ("coverage_modality", "cover_mode"), SEVERITY_MEDIUM),
    _FieldRule("indemnity_limit", ("indemnity_limit",), ("indemnity_limit",), SEVERITY_MEDIUM),
    _FieldRule("indemnity_basis", ("indemnity_basis",), ("valuation_basis", "indemnity_basis"), SEVERITY_LOW),
    _FieldRule("territory", ("territory",), ("territory",), SEVERITY_LOW),
    _FieldRule(
        "secured_creditor_amount_uf",
        ("secured_creditor_amount_uf",),
        ("secured_creditor_amount_uf",),
        SEVERITY_MEDIUM,
        EndorsementKind.PLEDGE_UPDATE.value,
        True,
    ),
    _FieldRule(
        "commission_pct",
        ("commission_pct", "broker_commission_pct"),
        ("commission_pct", "broker_commission_pct"),
        SEVERITY_LOW, None, True,
    ),
)

#: Keys under which each side keeps its coverage / deductible collections.
_COVERAGE_KEYS = ("coverages_to_issue", "coverages", "covered_coverages")
_DEDUCTIBLE_KEYS = ("deductibles_to_issue", "deductibles")
_WARRANTY_KEYS = ("warranties", "warranties_to_issue")


# =============================================================================
# Payload access helpers
# =============================================================================

def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, "", [], {}):
            return payload[key]
        # Premium blocks are sometimes nested under "premium"/"premium_summary".
        for container in ("premium", "premium_summary", "money", "totals"):
            block = payload.get(container)
            if isinstance(block, dict) and block.get(key) not in (None, "", [], {}):
                return block[key]
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace("−", "-").replace(" ", "")
        if not text:
            return None
        # Chilean formatting: "." thousands, "," decimal.
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return " ".join(str(value).split()).strip().lower()


def _same(expected: Any, found: Any, *, numeric: bool) -> bool:
    if numeric:
        left, right = _to_decimal(expected), _to_decimal(found)
        if left is not None and right is not None:
            return abs(left - right) <= MONEY_TOLERANCE
    left_text, right_text = _norm_text(expected), _norm_text(found)
    if left_text and right_text:
        # Datetimes vs dates: compare the day when both look like timestamps.
        return left_text[:10] == right_text[:10] if _looks_temporal(left_text) else left_text == right_text
    return left_text == right_text


def _looks_temporal(text: str) -> bool:
    return len(text) >= 10 and text[4] == "-" and text[7] == "-"


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coverage_number(item: dict[str, Any]) -> str:
    for key in ("n", "number", "no", "code", "item_number"):
        if item.get(key) not in (None, ""):
            return str(item[key]).strip()
    return _norm_text(item.get("name") or item.get("coverage") or "")


def _coverage_text(item: dict[str, Any]) -> str:
    for key in ("offered_condition", "limit", "requested_limit", "condition", "text", "value"):
        if item.get(key) not in (None, ""):
            return str(item[key])
    return str(item.get("name") or "")


def _peril_key(item: dict[str, Any]) -> str:
    for key in ("peril", "risk", "coverage", "name"):
        if item.get(key) not in (None, ""):
            return _norm_text(item[key])
    return ""


def _deductible_text(item: dict[str, Any]) -> str:
    for key in ("offered", "value", "deductible", "text", "requested"):
        if item.get(key) not in (None, ""):
            return str(item[key])
    return _norm_text(item)


# =============================================================================
# Sources
# =============================================================================

#: How a mirror side was sourced — surfaced in the API so the UI can label a
#: diff computed from an unconfirmed parse as provisional.
SOURCE_CONFIRMED = "confirmed"
SOURCE_LATEST_SUCCEEDED = "latest_succeeded"


@dataclass
class MirrorSources:
    """The two payloads plus the extraction rows they came from.

    ``proposal_source`` / ``policy_source`` say which payload was used:
    ``"confirmed"`` (a human signed off on it — the spec's §7.6 input) or
    ``"latest_succeeded"`` (the documented fallback: the newest successful
    parse, so the diff still renders while the review is pending).
    """

    proposal_payload: dict[str, Any]
    policy_payload: dict[str, Any]
    proposal_extraction_id: int | None = None
    policy_extraction_id: int | None = None
    missing: list[str] | None = None
    proposal_source: str | None = None
    policy_source: str | None = None


def _is_confirmed(row: Extraction) -> bool:
    """A human signed off: ``record_confirmation`` stamped the audit blob."""
    raw = row.raw_output if isinstance(row.raw_output, dict) else {}
    return isinstance(raw.get("confirmation"), dict)


def _latest_extraction(
    db: Session, *, broker_id: int, category: DocumentCategory,
    case_file_id: int | None, document_id: int | None,
) -> tuple[Extraction | None, str | None]:
    """The best usable extraction of a category for this case / document.

    CONFIRMED payloads win (spec §7.6 compares the two *confirmed* documents);
    when none is confirmed yet the newest SUCCEEDED parse is the documented
    fallback, flagged as such in the returned source tag. Document-scoped rows
    outrank case-scoped rows at equal confirmation level.
    """
    stmt = select(Extraction).where(
        Extraction.broker_id == broker_id,
        Extraction.category == category,
        Extraction.status == ExtractionStatus.SUCCEEDED,
    )
    candidates: list[Extraction] = []
    if document_id is not None:
        candidates.extend(
            db.scalars(
                stmt.where(Extraction.document_id == document_id).order_by(
                    Extraction.id.desc()
                )
            ).all()
        )
    if case_file_id is not None:
        seen = {row.id for row in candidates}
        candidates.extend(
            row
            for row in db.scalars(
                stmt.where(Extraction.case_file_id == case_file_id).order_by(
                    Extraction.id.desc()
                )
            ).all()
            if row.id not in seen
        )
    for row in candidates:
        if _is_confirmed(row):
            return row, SOURCE_CONFIRMED
    if candidates:
        return candidates[0], SOURCE_LATEST_SUCCEEDED
    return None, None


def load_sources(db: Session, *, policy: Policy) -> MirrorSources:
    """Resolve the 07 and 08 payloads for a policy, confirmed-first."""
    missing: list[str] = []
    proposal_row, proposal_source = _latest_extraction(
        db,
        broker_id=policy.broker_id,
        category=DocumentCategory.ISSUANCE_PROPOSAL,
        case_file_id=policy.case_file_id,
        document_id=None,
    )
    policy_row, policy_source = _latest_extraction(
        db,
        broker_id=policy.broker_id,
        category=DocumentCategory.POLICY,
        case_file_id=policy.case_file_id,
        document_id=policy.source_document_id,
    )
    if proposal_row is None:
        missing.append("issuance_proposal")
    if policy_row is None:
        missing.append("policy")
    return MirrorSources(
        proposal_payload=dict(proposal_row.parsed or {}) if proposal_row else {},
        policy_payload=dict(policy_row.parsed or {}) if policy_row else {},
        proposal_extraction_id=proposal_row.id if proposal_row else None,
        policy_extraction_id=policy_row.id if policy_row else None,
        missing=missing,
        proposal_source=proposal_source,
        policy_source=policy_source,
    )


def _registry_extra_fields() -> tuple[str, ...]:
    """Scalar field names both registry schemas share, minus the ones we map.

    Best effort: the registry is impl-ai's file and may not be importable yet,
    in which case the hand-checked :data:`MIRROR_FIELDS` carry the comparison on
    their own.
    """
    try:  # pragma: no cover - depends on the AI package landing
        from app.schemas.extraction.registry import spec_for
    except Exception:  # noqa: BLE001
        return ()
    try:
        proposal_spec = spec_for(DocumentCategory.ISSUANCE_PROPOSAL)
        policy_spec = spec_for(DocumentCategory.POLICY)
    except Exception:  # noqa: BLE001
        return ()
    mapped = {key for rule in MIRROR_FIELDS for key in rule.proposal_keys}
    shared = set(getattr(proposal_spec.schema, "model_fields", {})) & set(
        getattr(policy_spec.schema, "model_fields", {})
    )
    skip_prefixes = ("coverages", "deductibles", "warranties", "exclusions")
    return tuple(
        sorted(
            name
            for name in shared
            if name not in mapped and not name.startswith(skip_prefixes)
        )
    )


# =============================================================================
# The diff
# =============================================================================

def mirror_diff(db: Session, *, policy: Policy) -> list[MirrorDiffRow]:
    """Compare the confirmed 07 against the confirmed 08 and return the deltas."""
    sources = load_sources(db, policy=policy)
    rows = diff_payloads(sources.proposal_payload, sources.policy_payload)
    return rows


def diff_payloads(
    proposal: dict[str, Any], policy_payload: dict[str, Any]
) -> list[MirrorDiffRow]:
    """Pure comparison of two payloads — the unit-testable core."""
    rows: list[MirrorDiffRow] = []
    if not proposal or not policy_payload:
        return rows

    # --- scalars -------------------------------------------------------------
    for rule in MIRROR_FIELDS:
        expected = _first(proposal, rule.proposal_keys)
        found = _first(policy_payload, rule.policy_keys)
        if expected is None and found is None:
            continue
        if _same(expected, found, numeric=rule.numeric):
            continue
        kind = rule.endorsement_kind
        if rule.path == "total_insured_amount_uf":
            left, right = _to_decimal(expected), _to_decimal(found)
            if left is not None and right is not None and right < left:
                kind = EndorsementKind.SUM_INSURED_DECREASE.value
        rows.append(
            MirrorDiffRow(
                path=rule.path,
                expected=_readable(expected),
                found=_readable(found),
                severity=rule.severity,
                suggested_endorsement_kind=kind,
            )
        )

    # --- registry-driven extras ---------------------------------------------
    for name in _registry_extra_fields():
        expected, found = proposal.get(name), policy_payload.get(name)
        if isinstance(expected, (list, dict)) or isinstance(found, (list, dict)):
            continue
        if expected is None and found is None:
            continue
        if _same(expected, found, numeric=False):
            continue
        rows.append(
            MirrorDiffRow(
                path=name,
                expected=_readable(expected),
                found=_readable(found),
                severity=SEVERITY_LOW,
                suggested_endorsement_kind=None,
            )
        )

    # --- coverages, aligned on their number ---------------------------------
    rows.extend(
        _diff_collection(
            proposal, policy_payload, _COVERAGE_KEYS, _coverage_number,
            _coverage_text, "coverages", SEVERITY_MEDIUM, None,
        )
    )
    # --- deductibles, aligned on the peril key ------------------------------
    rows.extend(
        _diff_collection(
            proposal, policy_payload, _DEDUCTIBLE_KEYS, _peril_key,
            _deductible_text, "deductibles", SEVERITY_MEDIUM,
            EndorsementKind.DEDUCTIBLE_REDUCTION.value,
        )
    )
    # --- warranties, aligned on their code ----------------------------------
    rows.extend(
        _diff_collection(
            proposal, policy_payload, _WARRANTY_KEYS,
            lambda item: _norm_text(item.get("code") or item.get("n") or item.get("title")),
            lambda item: str(item.get("requirement") or item.get("text") or item.get("title") or ""),
            "warranties", SEVERITY_LOW, None,
        )
    )
    return rows


def _diff_collection(
    proposal: dict[str, Any],
    policy_payload: dict[str, Any],
    keys: tuple[str, ...],
    key_of,
    text_of,
    label: str,
    severity: str,
    endorsement_kind: str | None,
) -> list[MirrorDiffRow]:
    left = {key_of(i): i for i in _as_list(_first(proposal, keys)) if key_of(i)}
    right = {key_of(i): i for i in _as_list(_first(policy_payload, keys)) if key_of(i)}
    if not left and not right:
        return []

    rows: list[MirrorDiffRow] = []
    for key in left:
        if key not in right:
            rows.append(
                MirrorDiffRow(
                    path=f"{label}[{key}]",
                    expected=text_of(left[key]),
                    found=None,
                    severity=SEVERITY_HIGH,
                    suggested_endorsement_kind=endorsement_kind,
                )
            )
            continue
        expected, found = text_of(left[key]), text_of(right[key])
        if _norm_text(expected) != _norm_text(found):
            rows.append(
                MirrorDiffRow(
                    path=f"{label}[{key}]",
                    expected=expected,
                    found=found,
                    severity=severity,
                    suggested_endorsement_kind=endorsement_kind,
                )
            )
    for key in right:
        if key not in left:
            rows.append(
                MirrorDiffRow(
                    path=f"{label}[{key}]",
                    expected=None,
                    found=text_of(right[key]),
                    severity=severity,
                    suggested_endorsement_kind=endorsement_kind,
                )
            )
    return rows


def _readable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return str(value)[:500]
    return value


# =============================================================================
# Queueing the diff as endorsement drafts
# =============================================================================

def queue_endorsements(
    db: Session,
    *,
    policy: Policy,
    rows: list[MirrorDiffRow],
    user_id: int | None,
    case_file_id: int | None = None,
) -> list[Endorsement]:
    """Turn diff rows into ``endorsement(status=draft)``. The caller commits."""
    from sqlalchemy import func

    highest = db.scalar(
        select(func.max(Endorsement.sequence_no)).where(
            Endorsement.broker_id == policy.broker_id,
            Endorsement.policy_id == policy.id,
        )
    )
    sequence = int(highest or 0)
    created: list[Endorsement] = []
    for row in rows:
        sequence += 1
        kind = EndorsementKind.OTHER
        if row.suggested_endorsement_kind:
            try:
                kind = EndorsementKind(row.suggested_endorsement_kind)
            except ValueError:  # pragma: no cover - defensive
                kind = EndorsementKind.OTHER
        endorsement = Endorsement(
            broker_id=policy.broker_id,
            policy_id=policy.id,
            case_file_id=case_file_id or policy.case_file_id,
            sequence_no=sequence,
            kind=kind,
            status=EndorsementStatus.DRAFT,
            motive=(
                f"Validación espejo: {row.path} — propuesta '{row.expected}' vs "
                f"póliza '{row.found}'"
            ),
            contractual_basis="mirror_validation",
            effect={
                "source": "mirror_diff",
                "path": row.path,
                "expected": row.expected,
                "found": row.found,
                "severity": row.severity,
                "queued_by_user_id": user_id,
            },
            # A queued draft is a SUGGESTION: never pre-confirmed, whoever asked.
            is_confirmed=False,
        )
        db.add(endorsement)
        created.append(endorsement)
    db.flush()
    return created
