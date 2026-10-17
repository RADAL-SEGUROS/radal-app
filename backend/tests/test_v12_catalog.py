"""v12 lane B1 — the closed ramo/subtipo catalogue (02 §1).

* ``GET /catalog/lines`` / ``GET /catalog/subtypes/{id}``: global rows only,
  active only, catalogue order, any authenticated user;
* the ramo locks: no broker path creates / edits / deletes a ramo any more
  (403 with the Spanish reason), reads keep working;
* account creation accepts ``insurance_subtype_id`` (active, 422 otherwise) and
  derives the ramo, the suggested route and ``entry_origin=full_journey``.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.case_file import CaseFile
from app.models.catalog import InsuranceSubtype
from app.models.enums import CaseEntryOrigin, PlacementRoute
from app.models.insurance_line import InsuranceLine
from app.services.catalog import CLOSED_CATALOGUE_DETAIL
from tests.conftest import API


def _subtype(db, code: str) -> InsuranceSubtype:
    return db.scalars(select(InsuranceSubtype).where(InsuranceSubtype.code == code)).one()


# --- GET /catalog/lines -----------------------------------------------------------

def test_lines_are_the_global_catalogue_in_order(client, world, db, catalog, headers_a):
    response = client.get(f"{API}/catalog/lines", headers=headers_a)
    assert response.status_code == 200, response.text
    lines = response.json()
    assert len(lines) == 18
    codes = [line["code"] for line in lines]
    assert len(set(codes)) == 18 and all(codes)
    # Ordered by sort_order — the server owns the cascade order.
    orders = [line["sort_order"] for line in lines]
    assert orders == sorted(orders)
    # The broker's own legacy ramo ("Incendio y Sismo Alfa") is NOT catalogue.
    names = {line["name"] for line in lines}
    assert world.a.line.name not in names and world.b.line.name not in names
    pro = next(line for line in lines if line["code"] == "PRO")
    sub_codes = [s["code"] for s in pro["subtypes"]]
    assert "PRO-02" in sub_codes and "PRO-05" in sub_codes
    pro02 = next(s for s in pro["subtypes"] if s["code"] == "PRO-02")
    assert pro02["suggested_entry"] == "market"
    assert pro02["suggested_route"] == "traditional"
    assert pro02["cmf_codes"]
    veh = next(line for line in lines if line["code"] == "VEH")
    routes = {s["code"]: s["suggested_route"] for s in veh["subtypes"]}
    assert routes["VEH-01"] == "digital"  # portal -> digital
    assert routes["VEH-05"] == "traditional"  # flota goes to market


def test_inactive_subtypes_and_lines_are_hidden(client, world, db, catalog, headers_a):
    retired = _subtype(db, "PRO-05")
    retired.is_active = False
    line = db.scalars(select(InsuranceLine).where(InsuranceLine.code == "CIB")).one()
    line.is_active = False
    db.commit()

    lines = client.get(f"{API}/catalog/lines", headers=headers_a).json()
    assert len(lines) == 17
    pro = next(item for item in lines if item["code"] == "PRO")
    assert "PRO-05" not in [s["code"] for s in pro["subtypes"]]

    assert client.get(f"{API}/catalog/subtypes/{retired.id}", headers=headers_a).status_code == 404


def test_subtype_detail_and_unknown(client, world, db, catalog, headers_a, headers_b):
    sub = _subtype(db, "VEH-01")
    for headers in (headers_a, headers_b):
        response = client.get(f"{API}/catalog/subtypes/{sub.id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == "VEH-01"
        assert body["line_code"] == "VEH"
        assert body["suggested_route"] == "digital"
    assert client.get(f"{API}/catalog/subtypes/999999", headers=headers_a).status_code == 404


def test_catalog_needs_authentication(client, world, catalog):
    assert client.get(f"{API}/catalog/lines").status_code == 401
    assert client.get(f"{API}/catalog/subtypes/1").status_code == 401


def test_low_privilege_roles_read_the_catalogue(client, world, catalog, headers_a_inspector):
    assert client.get(f"{API}/catalog/lines", headers=headers_a_inspector).status_code == 200


# --- Locks: no broker creates a ramo ------------------------------------------------

def test_broker_ramo_writes_are_locked(client, world, db, headers_a):
    for method, path in (
        ("post", f"{API}/line-record-schemas"),
        ("put", f"{API}/line-record-schemas/1"),
        ("delete", f"{API}/line-record-schemas/1"),
    ):
        kwargs = {"headers": headers_a}
        if method != "delete":
            kwargs["json"] = {"name": "Mi ramo"}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, (method, response.text)
        assert response.json()["detail"] == CLOSED_CATALOGUE_DETAIL
    # Reads keep working.
    assert client.get(f"{API}/line-record-schemas", headers=headers_a).status_code == 200


# --- Account creation with a subtipo ------------------------------------------------

def _account_body(world, **extra):
    return {
        "kind": "account",
        "client_id": world.a.client.id,
        "period_start": "2026-03-01",
        "period_end": "2027-03-01",
        **extra,
    }


def test_account_create_derives_line_route_and_origin(client, world, db, catalog, headers_a):
    sub = _subtype(db, "PRO-02")
    response = client.post(
        f"{API}/case-files",
        json=_account_body(world, insurance_subtype_id=sub.id),
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["insurance_subtype_id"] == sub.id
    assert body["insurance_line_id"] == sub.insurance_line_id
    assert body["route"] == PlacementRoute.TRADITIONAL.value
    assert body["entry_origin"] == CaseEntryOrigin.FULL_JOURNEY.value
    assert body["origin"] == "new"
    assert body["insurance_subtype_code"] == "PRO-02"
    assert body["stages"][0]["state"] == "current"

    portal = _subtype(db, "VEH-01")
    second = client.post(
        f"{API}/case-files",
        json=_account_body(world, insurance_subtype_id=portal.id, route="manual"),
        headers=headers_a,
    )
    assert second.status_code == 201, second.text
    # An explicit route wins over the subtipo's suggestion.
    assert second.json()["route"] == "manual"


def test_account_create_rejects_inactive_unknown_or_mismatched_subtype(
    client, world, db, catalog, headers_a
):
    retired = _subtype(db, "PRO-05")
    retired.is_active = False
    db.commit()
    for subtype_id in (retired.id, 999999):
        response = client.post(
            f"{API}/case-files",
            json=_account_body(world, insurance_subtype_id=subtype_id),
            headers=headers_a,
        )
        assert response.status_code == 422, response.text

    sub = _subtype(db, "PRO-01")
    mismatch = client.post(
        f"{API}/case-files",
        json=_account_body(world, insurance_subtype_id=sub.id, insurance_line_id=world.a.line.id),
        headers=headers_a,
    )
    assert mismatch.status_code == 422, mismatch.text
    assert db.scalars(select(CaseFile)).all() == []


def test_account_create_without_subtype_keeps_working(client, world, headers_a):
    response = client.post(
        f"{API}/case-files",
        json=_account_body(world, insurance_line_id=world.a.line.id),
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    assert response.json()["insurance_subtype_id"] is None
    assert response.json()["route"] is None
