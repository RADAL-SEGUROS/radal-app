"""Shared building blocks for every registry extraction schema.

Three concerns live here, and nothing else:

1. **Chilean number parsing.** ``"UF 17.920"`` -> ``17920``, ``"UF 12,99"`` ->
   ``12.99``: ``.`` is the thousands separator, ``,`` the decimal, and negative
   amounts may arrive with the Unicode minus ``U+2212``. :func:`parse_uf` and
   :func:`parse_pct` are the only place that knowledge lives.

2. **The premium block.** ``net = taxable + exempt`` · ``vat = 0.19 x taxable``
   (NOT on net — earthquake cover is VAT-exempt) · ``total = net + vat``.
   At SUGGEST time a mismatch is recorded as a *warning* on the payload, never
   silently corrected and never fatal; 422 belongs to the confirm step
   (spec §3.2 rule 2).

3. **Prose survives verbatim.** Every structured row keeps a free-text field
   next to its parsed numbers. In the two corpus cases that decided coverage
   (the 60-day manifestation window, the 4-hour franchise) the exact wording
   *is* the coverage, so it is never thrown away (spec §3.2 rule 3).

Datetimes, not dates, are used for policy periods, endorsement effect, coverage
termination and claim occurrence: the noon convention and the hourly franchises
are contractual (spec §3.2 rule 7).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

__all__ = [
    "ExtractionModel",
    "RootExtraction",
    "parse_uf",
    "parse_pct",
    "parse_int",
    "parse_policy_datetime",
    "parse_event_datetime",
    "parse_flexible_date",
    "UF",
    "Pct",
    "Rate",
    "Int",
    "PolicyDateTime",
    "EventDateTime",
    "FlexDate",
    "Str",
    "PartyRef",
    "PersonRef",
    "InsurerRefX",
    "Row",
    "AmountRow",
    "LocationRow",
    "CoverageLine",
    "DeductibleLine",
    "ExclusionLine",
    "WarrantyLine",
    "InstallmentRow",
    "ClaimRow",
    "PremiumBlock",
    "PeriodBlock",
    "SignerBlock",
    "VAT_RATE",
    "MONEY_TOLERANCE",
]

VAT_RATE = Decimal("0.19")
# UF is quoted to 2 decimals in the market; the columns keep 4.
MONEY_TOLERANCE = Decimal("0.05")

_NULLISH = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "n/d",
    "nd",
    "s/i",
    "sin informacion",
    "sin información",
    "no aplica",
    "no informado",
    "null",
    "none",
    "ninguno",
}

# U+2212 MINUS SIGN, en dash and em dash all appear as minus in the corpus PDFs.
_MINUS_CHARS = "−–—"
_CURRENCY_RE = re.compile(r"(?i)\b(uf|u\.f\.|clp|usd|\$|pesos|unidades\s+de\s+fomento)\b|[$]")
_NUMBER_RE = re.compile(r"-?\d[\d.,]*")

_SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


# --- Numbers -----------------------------------------------------------------


def _clean_numeric(value: str) -> str | None:
    """Strip currency marks and normalise the sign, returning the digits blob."""
    text = value.strip()
    if text.lower() in _NULLISH:
        return None
    for char in _MINUS_CHARS:
        text = text.replace(char, "-")
    negative = text.startswith("(") and text.endswith(")")
    text = _CURRENCY_RE.sub(" ", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace("%", " ").replace("\u2030", " ")
    text = re.sub(r"\s+", "", text)
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    blob = match.group(0)
    if negative and not blob.startswith("-"):
        blob = "-" + blob
    return blob


def _to_decimal(blob: str) -> Decimal | None:
    """Interpret a Chilean-formatted number: ``.`` thousands, ``,`` decimal."""
    sign = -1 if blob.startswith("-") else 1
    body = blob.lstrip("+-")
    if not body:
        return None

    if "," in body and "." in body:
        # "1.234,56" — dots are thousands, the comma is the decimal point.
        body = body.replace(".", "").replace(",", ".")
    elif "," in body:
        head, _, tail = body.rpartition(",")
        # "1,234,567" is a thousands-grouped number; "12,99" is a decimal.
        body = body.replace(",", "") if len(tail) == 3 and "," in head else body.replace(",", ".")
    elif "." in body:
        head, _, tail = body.rpartition(".")
        # "17.920" is seventeen thousand nine hundred and twenty, not 17.92.
        if len(tail) == 3 and (head.replace(".", "").isdigit() and head != ""):
            body = body.replace(".", "")
    try:
        return Decimal(body) * sign
    except (InvalidOperation, ValueError):
        return None


def parse_uf(value: Any) -> Decimal | None:
    """Coerce a UF amount written the Chilean way into a :class:`Decimal`.

    ``"UF 17.920"`` -> ``17920`` · ``"UF 12,99"`` -> ``12.99`` ·
    ``"−1.234,56"`` -> ``-1234.56``. Anything that is not a number at all
    (prose, ``"no aplica"``, an empty cell) becomes ``None`` — the reviewer sees
    the gap instead of a fabricated figure.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, dict):
        for key in ("amount_uf", "amount", "uf", "value", "monto", "valor"):
            if key in value:
                return parse_uf(value[key])
        return None
    if not isinstance(value, str):
        return None
    blob = _clean_numeric(value)
    return None if blob is None else _to_decimal(blob)


def parse_pct(value: Any) -> Decimal | None:
    """Coerce a percentage (0-100) or a per-mille rate. ``"1,32 por mil"`` -> ``1.32``."""
    return parse_uf(value)


def parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    parsed = parse_uf(value)
    if parsed is None:
        return None
    try:
        return int(parsed)
    except (InvalidOperation, ValueError, OverflowError):
        return None


# --- Dates and datetimes -----------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%d-%m-%y",
    "%d/%m/%y",
)
_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
)
_SPANISH_DATE_RE = re.compile(
    r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+(?:de[l]?\s+)?(\d{4})", re.IGNORECASE
)
_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})\s*(?:hrs?\.?|horas)?", re.IGNORECASE)


def _parse_date_part(text: str) -> date | None:
    cleaned = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    match = _SPANISH_DATE_RE.search(cleaned)
    if match:
        month = _SPANISH_MONTHS.get(match.group(2).lower())
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    # Last resort: an ISO-ish prefix inside a longer string.
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", cleaned)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    dmy = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", cleaned)
    if dmy:
        try:
            return date(int(dmy.group(3)), int(dmy.group(2)), int(dmy.group(1)))
        except ValueError:
            return None
    return None


def parse_flexible_date(value: Any) -> date | None:
    """Accept ISO, ``dd-mm-yyyy``, ``dd/mm/yyyy`` and ``"3 de marzo de 2026"``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or value.strip().lower() in _NULLISH:
        return None
    return _parse_date_part(value)


def _parse_datetime(value: Any, *, default_time: time) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, default_time)
    if not isinstance(value, str) or value.strip().lower() in _NULLISH:
        return None

    cleaned = value.strip().replace("Z", "")
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

    day = _parse_date_part(cleaned)
    if day is None:
        return None
    clock = _TIME_RE.search(cleaned)
    if clock:
        try:
            return datetime.combine(day, time(int(clock.group(1)), int(clock.group(2))))
        except ValueError:
            return datetime.combine(day, default_time)
    return datetime.combine(day, default_time)


def parse_policy_datetime(value: Any) -> datetime | None:
    """Contractual instants: policy periods, endorsement effect, coverage end.

    A bare date defaults to **12:00** because the Chilean market's cover period
    runs noon-to-noon and every policy in the corpus prints
    ``"desde las 12:00 horas del ..."``. The document's own time always wins.
    """
    return _parse_datetime(value, default_time=time(12, 0))


def parse_event_datetime(value: Any) -> datetime | None:
    """Real-world instants: claim occurrence, notice, first inspection.

    A bare date defaults to midnight — inventing a noon here would fabricate the
    hourly franchise the adjuster reports argue about.
    """
    return _parse_datetime(value, default_time=time(0, 0))


def _strip_str(value: Any) -> Any:
    """Keep prose verbatim, but collapse the ``None``-ish placeholders."""
    if isinstance(value, str):
        text = value.strip()
        return None if text.lower() in _NULLISH else text
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return value


UF = Annotated[Decimal | None, BeforeValidator(parse_uf)]
Pct = Annotated[Decimal | None, BeforeValidator(parse_pct)]
Rate = Annotated[Decimal | None, BeforeValidator(parse_pct)]
Int = Annotated[int | None, BeforeValidator(parse_int)]
PolicyDateTime = Annotated[datetime | None, BeforeValidator(parse_policy_datetime)]
EventDateTime = Annotated[datetime | None, BeforeValidator(parse_event_datetime)]
FlexDate = Annotated[date | None, BeforeValidator(parse_flexible_date)]
Str = Annotated[str | None, BeforeValidator(_strip_str)]


# --- Base models -------------------------------------------------------------


class ExtractionModel(BaseModel):
    """Base for every extraction shape.

    ``extra="allow"``: the corpus documents are not uniform, and a value the
    schema did not anticipate is worth strictly more to the reviewer as an extra
    key than as a dropped field.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        str_strip_whitespace=True,
        from_attributes=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_explicit_nulls(cls, data: Any) -> Any:
        """Treat an explicit ``null`` as "not stated" and fall back to the default.

        The prompt tells the model to answer ``null`` for anything the document
        does not state, and it obliges — including for the nested objects and
        lists. Without this, ``{"pledge_update": null}`` would fail validation
        and cost the reviewer the whole rest of the payload.
        """
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data


class RootExtraction(ExtractionModel):
    """What every category schema inherits: self-reported quality signals."""

    confidence: Pct = Field(
        default=None,
        description="0-100: how completely the document supported the fields you filled",
    )
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="Anything ambiguous, contradictory or unreadable in the source",
    )


# --- Shared fragments --------------------------------------------------------


class PartyRef(ExtractionModel):
    """A company as the document names it. RUT is normalised at confirm time."""

    legal_name: Str = None
    trade_name: Str = None
    rut: Str = None
    address: Str = None
    commune: Str = None
    city: Str = None
    activity: Str = None
    contact_name: Str = None
    contact_email: Str = None
    contact_phone: Str = None


class InsurerRefX(PartyRef):
    """An insurer. Matching is by normalised ``cmf_code`` / ``rut`` — NEVER by name."""

    cmf_code: Str = None


class PersonRef(ExtractionModel):
    name: Str = None
    role: Str = None
    rut: Str = None
    email: Str = None
    phone: Str = None
    registry: Str = Field(default=None, description="CMF/professional registry number")


class SignerBlock(ExtractionModel):
    name: Str = None
    role: Str = None
    company: Str = None
    email: Str = None
    phone: Str = None
    signed_on: FlexDate = None


class Row(ExtractionModel):
    """An open row of a table the document lays out freely.

    Extra keys are preserved (``extra="allow"``), so a matrix with columns the
    schema never heard of still reaches the reviewer intact.
    """

    label: Str = None
    detail: Str = None
    value: Str = None
    amount_uf: UF = None


class AmountRow(ExtractionModel):
    """One money line: the item, the verbatim basis, the parsed amount."""

    item: Str = None
    basis: Str = None
    amount_uf: UF = None
    pct: Pct = None
    note: Str = None


class LocationRow(ExtractionModel):
    number: Int = None
    name: Str = None
    address: Str = None
    commune: Str = None
    city: Str = None
    region: Str = None
    use: Str = None
    construction: Str = None
    insured_amount_uf: UF = None
    note: Str = None


class CoverageLine(ExtractionModel):
    """One coverage line. ``text`` is the verbatim wording — the coverage IS the wording."""

    number: Str = Field(default=None, description="The document's item number, e.g. '3' or '3.a'")
    name: Str = None
    text: Str = Field(default=None, description="Verbatim wording, copied from the document")
    requested_limit: Str = None
    offered_condition: Str = None
    limit_uf: UF = None
    sublimit: Str = None
    is_sublimit: bool | None = None
    note: Str = None


class DeductibleLine(ExtractionModel):
    """One peril's deductible, parsed AND verbatim.

    Fire is typically ``% of the loss``; earthquake ``% of the insured amount``
    of the affected item, with a UF minimum. The document decides — never assume.
    """

    peril: Str = None
    basis: Str = Field(
        default=None, description="loss | insured_amount | fixed | days"
    )
    pct: Pct = None
    min_uf: UF = None
    max_uf: UF = None
    days: Int = None
    hours: Int = None
    requested: Str = Field(default=None, description="Verbatim requested wording")
    offered: Str = Field(default=None, description="Verbatim offered wording")
    text: Str = Field(default=None, description="Verbatim deductible clause")


class ExclusionLine(ExtractionModel):
    """An exclusion and, critically, any carve-back — both verbatim."""

    number: Str = None
    text: Str = None
    carve_back: Str = None


class WarrantyLine(ExtractionModel):
    """An R-n / G-n / M-n requirement threaded through the whole case."""

    code: Str = None
    title: Str = None
    requirement: Str = Field(default=None, description="Verbatim requirement text")
    category: Str = Field(default=None, description="The inspection's A/B/C/D letter")
    deadline_days: Int = None
    due_date: FlexDate = None
    is_permanent: bool | None = None
    is_suspensive: bool | None = None
    status: Str = None
    budget_uf: UF = None
    actual_cost_uf: UF = None
    verification: Str = None


class InstallmentRow(ExtractionModel):
    number: Int = None
    coupon_number: Str = None
    due_date: FlexDate = None
    gross_amount_uf: UF = None
    net_premium_uf: UF = None
    commission_uf: UF = None
    status: Str = None
    paid_on: FlexDate = None
    days_late: Int = None
    note: Str = None


class ClaimRow(ExtractionModel):
    period: Str = None
    claim_number: Str = None
    occurred_on: FlexDate = None
    description: Str = None
    amount_uf: UF = None
    coverage: Str = None
    status: Str = None


class PeriodBlock(ExtractionModel):
    """A cover period. Datetimes, because the noon convention is contractual."""

    start_at: PolicyDateTime = None
    end_at: PolicyDateTime = None
    text: Str = Field(default=None, description="Verbatim period wording")


class PremiumBlock(ExtractionModel):
    """The Chilean premium structure, cross-checked but never silently corrected.

    ``net = taxable + exempt`` · ``vat = 0.19 x taxable`` (earthquake cover is
    VAT-exempt, so VAT is NEVER computed on ``net``) · ``total = net + vat``.

    Missing components are derived when the arithmetic allows; components that
    are all present but contradict each other are reported in
    :attr:`inconsistencies`. The suggestion still reaches the human — the
    confirm step is where a contradiction becomes a 422.
    """

    taxable_premium_uf: UF = None
    exempt_premium_uf: UF = None
    net_premium_uf: UF = None
    vat_uf: UF = None
    total_premium_uf: UF = None
    inconsistencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reconcile(self) -> "PremiumBlock":
        problems: list[str] = []
        taxable, exempt = self.taxable_premium_uf, self.exempt_premium_uf
        net, vat, total = self.net_premium_uf, self.vat_uf, self.total_premium_uf

        if net is None and taxable is not None and exempt is not None:
            net = taxable + exempt
        elif taxable is None and net is not None and exempt is not None:
            taxable = net - exempt
        elif exempt is None and net is not None and taxable is not None:
            exempt = net - taxable
        elif None not in (net, taxable, exempt) and abs(net - (taxable + exempt)) > MONEY_TOLERANCE:
            problems.append(f"net ({net}) != taxable ({taxable}) + exempt ({exempt})")

        if net is None and taxable is not None and exempt is None:
            net = taxable

        expected_vat = taxable * VAT_RATE if taxable is not None else None
        if vat is None:
            vat = expected_vat
        elif expected_vat is not None and abs(vat - expected_vat) > MONEY_TOLERANCE:
            problems.append(f"vat ({vat}) != 0.19 x taxable ({expected_vat})")

        if total is None and net is not None and vat is not None:
            total = net + vat
        elif None not in (total, net, vat) and abs(total - (net + vat)) > MONEY_TOLERANCE:
            problems.append(f"total ({total}) != net ({net}) + vat ({vat})")

        object.__setattr__(self, "taxable_premium_uf", taxable)
        object.__setattr__(self, "exempt_premium_uf", exempt)
        object.__setattr__(self, "net_premium_uf", net)
        object.__setattr__(self, "vat_uf", vat)
        object.__setattr__(self, "total_premium_uf", total)
        if problems:
            object.__setattr__(self, "inconsistencies", [*self.inconsistencies, *problems])
        return self
