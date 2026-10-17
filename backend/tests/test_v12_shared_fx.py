"""v12 shared service: UF / USD rates (app.services.fx). Hermetic: no network."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.currency import CurrencyRate
from app.services.fx import (
    MAX_LOOKBACK_DAYS,
    clp_to_uf,
    get_uf_rate,
    get_usd_rate,
    parse_mindicador,
    uf_to_clp,
    upsert_rates,
)

D = date(2026, 10, 16)


def _seed(db, rows):
    n = upsert_rates(db, rows)
    db.commit()
    return n


# --- lookups ---------------------------------------------------------------------

def test_exact_date_wins(db):
    _seed(db, [
        (D - timedelta(days=1), Decimal("39990.01"), Decimal("940.10"), "test"),
        (D, Decimal("40000.50"), Decimal("945.25"), "test"),
    ])
    assert get_uf_rate(db, D) == Decimal("40000.5000")
    assert get_usd_rate(db, D) == Decimal("945.2500")
    assert isinstance(get_uf_rate(db, D), Decimal)
    # a datetime is read as its date
    assert get_uf_rate(db, datetime(2026, 10, 16, 23, 59)) == Decimal("40000.5000")


def test_falls_back_to_latest_prior_within_40_days(db):
    friday = date(2026, 10, 9)
    _seed(db, [
        (friday - timedelta(days=3), Decimal("39900"), Decimal("930"), "test"),
        (friday, Decimal("39950"), Decimal("935"), "test"),
        (friday + timedelta(days=10), Decimal("40100"), Decimal("950"), "test"),  # future: ignored
    ])
    sunday = friday + timedelta(days=2)
    assert get_uf_rate(db, sunday) == Decimal("39950")
    assert get_usd_rate(db, sunday) == Decimal("935")
    assert get_uf_rate(db, friday + timedelta(days=MAX_LOOKBACK_DAYS)) == Decimal("40100")
    # older than the window -> None, never a stale rate
    _seed(db, [(date(2025, 1, 1), Decimal("38000"), None, "test")])
    assert get_uf_rate(db, date(2025, 1, 1) + timedelta(days=MAX_LOOKBACK_DAYS)) == Decimal("38000")
    assert get_uf_rate(db, date(2025, 1, 1) + timedelta(days=MAX_LOOKBACK_DAYS + 1)) is None
    assert get_uf_rate(db, date(2024, 12, 31)) is None


def test_each_rate_falls_back_independently(db):
    _seed(db, [
        (D - timedelta(days=2), Decimal("39000"), Decimal("900"), "test"),
        (D, Decimal("39100"), None, "test"),     # UF only today
    ])
    assert get_uf_rate(db, D) == Decimal("39100")
    assert get_usd_rate(db, D) == Decimal("900")   # skips today's NULL USD


def test_empty_table_is_none(db):
    assert get_uf_rate(db, D) is None
    assert get_usd_rate(db, D) is None


# --- conversions -------------------------------------------------------------------

def test_uf_to_clp_rounds_half_up():
    assert uf_to_clp(Decimal("1"), Decimal("39500.50")) == 39501
    assert uf_to_clp(Decimal("1"), Decimal("39500.49")) == 39500
    assert uf_to_clp(Decimal("12.3456"), Decimal("39485.65")) == 487474  # 487474.04...
    assert uf_to_clp(Decimal("0.5"), Decimal("3")) == 2                  # 1.5 -> 2
    assert uf_to_clp(Decimal("-0.5"), Decimal("3")) == -2                # away from zero
    assert uf_to_clp("2", "100.25") == 201
    assert uf_to_clp(0.1, 3) == 0
    assert isinstance(uf_to_clp(Decimal("1"), Decimal("1")), int)


def test_clp_to_uf_is_4dp_half_up():
    assert clp_to_uf(395005, Decimal("39500.50")) == Decimal("10.0000")
    assert clp_to_uf(100000, Decimal("39485.65")) == Decimal("2.5326")   # 2.532567...
    assert clp_to_uf(1, Decimal("20000")) == Decimal("0.0001")           # 0.00005 -> up
    assert clp_to_uf(0, Decimal("39000")) == Decimal("0.0000")
    assert clp_to_uf(1, Decimal("20000")).as_tuple().exponent == -4


def test_round_trip_is_stable_to_the_peso():
    rate = Decimal("39485.65")
    uf = clp_to_uf(1_250_000, rate)
    assert abs(uf_to_clp(uf, rate) - 1_250_000) <= 2


@pytest.mark.parametrize("rate", [None, 0, Decimal("0"), Decimal("-1"), "x", float("nan")])
def test_bad_rate_raises(rate):
    with pytest.raises(ValueError):
        uf_to_clp(Decimal("1"), rate)
    with pytest.raises(ValueError):
        clp_to_uf(1000, rate)


# --- upsert --------------------------------------------------------------------------

def test_upsert_inserts_updates_and_never_erases(db):
    assert _seed(db, [(D, Decimal("40000.12345"), None, "mindicador")]) == 1
    row = db.scalar(select(CurrencyRate).where(CurrencyRate.rate_date == D))
    assert row.uf == Decimal("40000.1235") and row.usd_observed is None

    # USD arrives later: UF kept, row counted as changed
    assert _seed(db, [(D, None, Decimal("945.5"), "mindicador")]) == 1
    db.expire_all()
    row = db.scalar(select(CurrencyRate).where(CurrencyRate.rate_date == D))
    assert row.uf == Decimal("40000.1235") and row.usd_observed == Decimal("945.5")

    # identical re-run: nothing changes (idempotent job)
    assert _seed(db, [(D, Decimal("40000.1235"), Decimal("945.5"), "mindicador")]) == 0
    # a correction counts and wins
    assert _seed(db, [(D, Decimal("40001"), None, "manual")]) == 1
    db.expire_all()
    row = db.scalar(select(CurrencyRate).where(CurrencyRate.rate_date == D))
    assert row.uf == Decimal("40001") and row.source == "manual"
    assert db.scalar(select(CurrencyRate.id).where(CurrencyRate.rate_date != D)) is None


def test_upsert_merges_same_date_and_skips_empty_rows(db):
    n = _seed(db, [
        (D, Decimal("40000"), None, "mindicador"),
        (D, None, Decimal("950"), "mindicador"),
        (D - timedelta(days=1), None, None, "mindicador"),
        (D - timedelta(days=2), Decimal("0"), Decimal("-3"), "mindicador"),
    ])
    assert n == 1
    rows = db.scalars(select(CurrencyRate)).all()
    assert len(rows) == 1
    assert rows[0].uf == Decimal("40000") and rows[0].usd_observed == Decimal("950")
    assert upsert_rates(db, []) == 0


# --- mindicador parsing -----------------------------------------------------------------

LATEST = {
    "version": "1.7.0",
    "autor": "mindicador.cl",
    "fecha": "2026-10-16T11:00:00.000Z",
    "uf": {"codigo": "uf", "nombre": "Unidad de fomento (UF)", "unidad_medida": "Pesos",
           "fecha": "2026-10-16T03:00:00.000Z", "valor": 39485.65},
    "dolar": {"codigo": "dolar", "nombre": "Dólar observado", "unidad_medida": "Pesos",
              "fecha": "2026-10-15T03:00:00.000Z", "valor": 945.32},
    "euro": {"codigo": "euro", "fecha": "2026-10-16T03:00:00.000Z", "valor": 1020.1},
}

SERIES_UF = {
    "version": "1.7.0",
    "autor": "mindicador.cl",
    "codigo": "uf",
    "nombre": "Unidad de fomento (UF)",
    "unidad_medida": "Pesos",
    "serie": [
        {"fecha": "2026-07-02T04:00:00.000Z", "valor": 39300.1},   # winter offset
        {"fecha": "2026-07-01T04:00:00.000Z", "valor": 39290.55},
        {"fecha": "bad", "valor": 1},
        {"fecha": "2026-06-30T04:00:00.000Z", "valor": None},
        {"fecha": "2026-06-29T00:00:00.000Z", "valor": "39280,1"},  # unparseable -> skipped
    ],
}


def test_parse_latest_payload():
    rows = parse_mindicador(LATEST)
    assert rows == [
        (date(2026, 10, 15), None, Decimal("945.32"), "mindicador"),
        (date(2026, 10, 16), Decimal("39485.65"), None, "mindicador"),
    ]


def test_parse_latest_same_date_merges():
    payload = dict(LATEST, dolar=dict(LATEST["dolar"], fecha="2026-10-16T03:00:00.000Z"))
    assert parse_mindicador(payload) == [
        (date(2026, 10, 16), Decimal("39485.65"), Decimal("945.32"), "mindicador"),
    ]


def test_parse_series_payload_sorted_and_clean():
    rows = parse_mindicador(SERIES_UF)
    assert rows == [
        (date(2026, 7, 1), Decimal("39290.55"), None, "mindicador"),
        (date(2026, 7, 2), Decimal("39300.1"), None, "mindicador"),
    ]
    dolar = parse_mindicador({"codigo": "dolar", "serie": [{"fecha": "2026-07-01T04:00:00.000Z", "valor": 930}]})
    assert dolar == [(date(2026, 7, 1), None, Decimal("930"), "mindicador")]
    assert parse_mindicador({"codigo": "euro", "serie": [{"fecha": "2026-07-01", "valor": 1}]}) == []


@pytest.mark.parametrize("payload", [{}, {"uf": None}, {"serie": "x"}, [], None, "oops"])
def test_parse_garbage_is_empty(payload):
    assert parse_mindicador(payload) == []


def test_parse_then_upsert_then_lookup(db):
    _seed(db, parse_mindicador(LATEST) + parse_mindicador(SERIES_UF))
    assert get_uf_rate(db, date(2026, 10, 18)) == Decimal("39485.65")
    assert get_usd_rate(db, date(2026, 10, 16)) == Decimal("945.32")
    assert get_uf_rate(db, date(2026, 7, 2)) == Decimal("39300.1")
