"""UF / USD rates: lookups, conversions and the mindicador.cl parser (v12, 01 §3).

Shared service (phase 2). ``currency_rate`` is a PLATFORM table (no
``broker_id``): one row per business date, CLP per 1 UF and CLP per 1 USD
(dólar observado). The ``fx.daily`` job (lane B2) fetches
``settings.FX_SOURCE_URL`` and feeds :func:`parse_mindicador` →
:func:`upsert_rates`; this module never opens a socket.

Lookups (:func:`get_uf_rate`, :func:`get_usd_rate`) take the exact date, else
the latest PRIOR date that carries the rate, within :data:`MAX_LOOKBACK_DAYS`
(weekends, holidays, a job that missed a day). Older than that is ``None`` —
a caller must refuse to convert rather than use a stale rate.

Money: Decimal only, never float. CLP is an integer peso amount rounded half-up;
UF is quantised to 4 decimals (``Numeric(14,4)``), half-up.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.currency import CurrencyRate

#: How far back a lookup may reach for the latest prior rate.
MAX_LOOKBACK_DAYS = 40

#: ``source`` written by :func:`parse_mindicador`.
MINDICADOR_SOURCE = "mindicador"

_UF_Q = Decimal("0.0001")
_CLP_Q = Decimal("1")

#: ``(rate_date, uf | None, usd | None, source)``.
RateRow = Tuple[date, Optional[Decimal], Optional[Decimal], Optional[str]]


# --- helpers -----------------------------------------------------------------

def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _dec(value: Any) -> Decimal:
    """Exact Decimal from int/str/Decimal (floats go through ``str`` to avoid binary noise)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def _positive_rate(value: Any) -> Decimal | None:
    """A parsed rate, or ``None`` when absent / not a positive finite number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = _dec(value)
    except ValueError:
        return None
    if not rate.is_finite() or rate <= 0:
        return None
    return rate


def _rate_or_raise(rate: Any) -> Decimal:
    parsed = _positive_rate(rate)
    if parsed is None:
        raise ValueError("rate must be a positive number")
    return parsed


def _lookup(db: Session, column, on: date | datetime) -> Decimal | None:  # noqa: ANN001
    day = _as_date(on)
    value = db.scalar(
        select(column)
        .where(
            column.is_not(None),
            CurrencyRate.rate_date <= day,
            CurrencyRate.rate_date >= day - timedelta(days=MAX_LOOKBACK_DAYS),
        )
        .order_by(CurrencyRate.rate_date.desc())
        .limit(1)
    )
    return Decimal(value) if value is not None else None


# --- lookups -------------------------------------------------------------------

def get_uf_rate(db: Session, on: date | datetime) -> Decimal | None:
    """CLP per 1 UF on ``on`` (exact date, else latest prior within 40 days), or ``None``."""
    return _lookup(db, CurrencyRate.uf, on)


def get_usd_rate(db: Session, on: date | datetime) -> Decimal | None:
    """CLP per 1 USD (dólar observado) on ``on``, same fallback rule, or ``None``."""
    return _lookup(db, CurrencyRate.usd_observed, on)


# --- conversions ---------------------------------------------------------------

def uf_to_clp(amount_uf: Decimal | int | str | float, rate: Decimal | int | str | float) -> int:
    """UF → integer CLP, rounded half-up (away from zero on .5). ``ValueError`` on a bad rate."""
    clp = _dec(amount_uf) * _rate_or_raise(rate)
    return int(clp.quantize(_CLP_Q, rounding=ROUND_HALF_UP))


def clp_to_uf(amount_clp: Decimal | int | str | float, rate: Decimal | int | str | float) -> Decimal:
    """CLP → UF quantised to 4 decimals, half-up. ``ValueError`` on a bad rate."""
    uf = _dec(amount_clp) / _rate_or_raise(rate)
    return uf.quantize(_UF_Q, rounding=ROUND_HALF_UP)


# --- writes --------------------------------------------------------------------

def upsert_rates(db: Session, rows: Iterable[RateRow]) -> int:
    """Insert or update ``currency_rate`` rows; returns how many rows were created or changed.

    Rows for the same date are merged (later non-``None`` values win). A
    ``None`` uf/usd never erases a stored value — a USD-only row leaves the UF
    alone. Values are stored at the column scale (UF 4 dp, USD 4 dp). Rows with
    neither rate are ignored. Flushes; the caller commits.
    """
    merged: dict[date, list[Any]] = {}
    for row in rows:
        rate_date, uf, usd, source = row
        day = _as_date(rate_date)
        uf_v = _positive_rate(uf)
        usd_v = _positive_rate(usd)
        if uf_v is None and usd_v is None:
            continue
        slot = merged.setdefault(day, [None, None, None])
        if uf_v is not None:
            slot[0] = uf_v.quantize(_UF_Q, rounding=ROUND_HALF_UP)
        if usd_v is not None:
            slot[1] = usd_v.quantize(_UF_Q, rounding=ROUND_HALF_UP)
        if source:
            slot[2] = str(source)[:32]
    if not merged:
        return 0

    existing = {
        r.rate_date: r
        for r in db.scalars(
            select(CurrencyRate).where(CurrencyRate.rate_date.in_(list(merged)))
        )
    }
    changed = 0
    for day in sorted(merged):
        uf_v, usd_v, source = merged[day]
        current = existing.get(day)
        if current is None:
            db.add(CurrencyRate(rate_date=day, uf=uf_v, usd_observed=usd_v, source=source))
            changed += 1
            continue
        dirty = False
        if uf_v is not None and (current.uf is None or Decimal(current.uf) != uf_v):
            current.uf = uf_v
            dirty = True
        if usd_v is not None and (
            current.usd_observed is None or Decimal(current.usd_observed) != usd_v
        ):
            current.usd_observed = usd_v
            dirty = True
        if dirty:
            if source:
                current.source = source
            changed += 1
    db.flush()
    return changed


# --- mindicador.cl -------------------------------------------------------------

def _parse_fecha(value: Any) -> date | None:
    """mindicador ``fecha`` → business date.

    mindicador encodes America/Santiago midnight in UTC (``2026-10-16T03:00:00.000Z``,
    ``…T04:00:00.000Z`` in winter). UTC is always ahead of Santiago, so the
    calendar date written in the string IS the Santiago date: take it verbatim
    (converting to local time would shift a ``T00:00Z`` value a day back).
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


_INDICATOR_SLOT = {"uf": 0, "dolar": 1}


def parse_mindicador(payload: dict) -> list[RateRow]:
    """Rows from a mindicador.cl payload, sorted by date.

    Supports the latest-values payload of ``/api`` (``{"uf": {"fecha", "valor"},
    "dolar": {...}, ...}``) and the series payloads of ``/api/uf/{yyyy}`` /
    ``/api/dolar/{yyyy}`` (``{"codigo": "uf", "serie": [{"fecha", "valor"}, ...]}``).
    Entries with a missing/invalid date or a non-positive value are skipped;
    anything that is not a dict yields ``[]``.
    """
    if not isinstance(payload, dict):
        return []
    acc: dict[date, list[Decimal | None]] = {}

    def put(indicator: str, entry: Any) -> None:
        slot = _INDICATOR_SLOT.get(indicator)
        if slot is None or not isinstance(entry, dict):
            return
        day = _parse_fecha(entry.get("fecha"))
        value = _positive_rate(entry.get("valor"))
        if day is None or value is None:
            return
        acc.setdefault(day, [None, None])[slot] = value

    serie = payload.get("serie")
    if isinstance(serie, list):
        indicator = str(payload.get("codigo") or "").strip().lower()
        for entry in serie:
            put(indicator, entry)
    else:
        for indicator in _INDICATOR_SLOT:
            put(indicator, payload.get(indicator))

    return [(day, uf, usd, MINDICADOR_SOURCE) for day, (uf, usd) in sorted(acc.items())]


__all__ = [
    "MAX_LOOKBACK_DAYS",
    "MINDICADOR_SOURCE",
    "RateRow",
    "clp_to_uf",
    "get_uf_rate",
    "get_usd_rate",
    "parse_mindicador",
    "uf_to_clp",
    "upsert_rates",
]
