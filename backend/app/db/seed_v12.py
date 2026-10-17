"""v12 data step: the closed ramo catalogue + cobranza defaults. Idempotent.

    python -m scripts.seed_v12            # dry run: report what would change
    python -m scripts.seed_v12 --apply    # commit

The schema comes from ``scripts/migrate_case_files.py --pass v12``; this module
only writes DATA, and only fills what is missing or out of step with the seed:

1. ``seed_catalog(db)`` — upserts the 18 GLOBAL ``insurance_line`` rows by
   ``code`` from ``app/db/seed_assets/catalogo_ramos.csv`` (team CSV + the
   ``PRO-05 Robo`` row the team's HTML has and the CSV lacked), reusing an
   existing global row with the same name when it has no code yet; then upserts
   every ``insurance_subtype`` by its unique ``code`` with the CMF mapping of
   docs/v12/01-data-model.md §1.
2. ``seed_collection_defaults(db)`` — one ``insurer_collection_profile`` per
   insurer (``not_contacted``) and one ``collection_settings`` per broker, each
   only where missing (both are also created lazily by the app).

The logic lives in ``app/`` (not ``scripts/``) because the backend image ships
``app/`` only and ``app.db.seed_dev`` calls it; ``scripts/seed_v12.py`` is the
operator CLI over these functions. Nothing is ever deleted: a subtipo a
platform admin retired (``is_active=False``) stays retired.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.broker import Broker
from app.models.catalog import InsuranceSubtype
from app.models.collection_settings import CollectionSettings
from app.models.enums import PlacementRoute, SuggestedEntry
from app.models.insurance_line import InsuranceLine
from app.models.insurer import Insurer
from app.models.insurer_collection import InsurerCollectionProfile

SEED_ASSETS = Path(__file__).resolve().parent / "seed_assets"
CATALOG_CSV = SEED_ASSETS / "catalogo_ramos.csv"
NOTICE_TEMPLATES_JSON = SEED_ASSETS / "notice_templates_es.json"

# CSV ``entrada_sugerida`` -> (entry, route).
_ENTRY = {
    "mercado": (SuggestedEntry.MARKET, PlacementRoute.TRADITIONAL),
    "portal": (SuggestedEntry.PORTAL, PlacementRoute.DIGITAL),
}

# CMF generales ramo codes per subtipo (docs/v12/01-data-model.md §1). A key
# ending in ``-*`` covers every subtipo of that ramo. Life (``VID-*``) reports
# under the life list, which is not seeded: an empty list, not a guess.
_CMF_BY_PREFIX: dict[str, list[int]] = {
    "VEH-*": [10, 16],
    "AP-*": [31],
    "SAL-*": [30],
    "ASV-*": [36],
    "VID-*": [],
}
_CMF_BY_CODE: dict[str, list[int]] = {
    "PRO-01": [1, 3],
    "PRO-02": [1, 3, 4, 6],
    "PRO-03": [2, 5],
    "PRO-04": [7],
    "PRO-05": [8],
    "CAS-01": [11],
    "CAS-02": [12],
    "RC-01": [13],
    "RC-02": [14],
    "RC-03": [14],
    "RC-07": [14],
    "RC-04": [15],
    "RC-05": [15],
    "RC-06": [15],
    "RC-08": [15],
    "TRA-01": [17],
    "TRA-02": [18, 19],
    "ING-01": [21],
    "ING-03": [21],
    "ING-02": [20],
    "ING-04": [22],
    "ING-05": [23],
    "GYC-01": [24],
    "GYC-02": [25],
    "GYC-03": [27],
    "GYC-04": [28],
    "OTR-02": [26],
    "SOA-01": [32],
    "OTR-01": [33],
    "OTR-03": [34],
    "AGR-01": [35],
    "HOG-01": [1, 3, 4, 8, 9, 13],
    "CON-01": [1, 3, 4, 8, 9, 13],
    "PYM-01": [1, 3, 4, 8, 9, 13],
    "CIB-01": [50],
    "OTR-04": [50],
}


def cmf_codes_for(subtype_code: str) -> list[str]:
    """CMF codes (as strings) for a subtipo code. Raises on an unmapped code."""
    if subtype_code in _CMF_BY_CODE:
        codes = _CMF_BY_CODE[subtype_code]
    else:
        prefix = subtype_code.split("-", 1)[0] + "-*"
        if prefix not in _CMF_BY_PREFIX:
            raise KeyError(f"no CMF mapping for subtipo {subtype_code}")
        codes = _CMF_BY_PREFIX[prefix]
    return [str(code) for code in codes]


@dataclass(frozen=True)
class CatalogRow:
    line_code: str
    line_name: str
    subtype_code: str
    subtype_name: str
    entry: str  # raw CSV value: mercado | portal


def load_catalog_rows(path: Path = CATALOG_CSV) -> list[CatalogRow]:
    """Parse the catalogue CSV (UTF-8 with BOM, CRLF — the team's export)."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            CatalogRow(
                line_code=row["ramo_codigo"].strip(),
                line_name=row["ramo"].strip(),
                subtype_code=row["subtipo_codigo"].strip(),
                subtype_name=row["subtipo"].strip(),
                entry=row["entrada_sugerida"].strip().lower(),
            )
            for row in csv.DictReader(handle)
            if (row.get("subtipo_codigo") or "").strip()
        ]
    for row in rows:
        if row.entry not in _ENTRY:
            raise ValueError(f"{row.subtype_code}: unknown entrada_sugerida {row.entry!r}")
        cmf_codes_for(row.subtype_code)  # fail loudly on an unmapped subtipo
    return rows


def _empty_stats() -> dict[str, int]:
    return {
        "lines_created": 0,
        "lines_updated": 0,
        "subtypes_created": 0,
        "subtypes_updated": 0,
        "profiles_created": 0,
        "settings_created": 0,
    }


def _assign(obj, **values) -> bool:  # noqa: ANN001
    """Set attributes that differ; return whether anything changed."""
    changed = False
    for key, value in values.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def seed_catalog(db: Session, rows: list[CatalogRow] | None = None) -> dict[str, int]:
    """Upsert the global ramos + subtipos. Flushes, never commits."""
    rows = rows if rows is not None else load_catalog_rows()
    stats = _empty_stats()

    # Ramo order = order of first appearance in the CSV (the team's order).
    line_order: dict[str, tuple[int, str]] = {}
    for row in rows:
        if row.line_code not in line_order:
            line_order[row.line_code] = (len(line_order) + 1, row.line_name)

    lines: dict[str, InsuranceLine] = {}
    for code, (sort_order, name) in line_order.items():
        line = db.scalar(
            select(InsuranceLine).where(
                InsuranceLine.broker_id.is_(None), InsuranceLine.code == code
            )
        )
        if line is None:
            # Reuse a global row of the same name that predates the catalogue.
            line = db.scalar(
                select(InsuranceLine)
                .where(
                    InsuranceLine.broker_id.is_(None),
                    InsuranceLine.code.is_(None),
                    func.lower(InsuranceLine.name) == name.lower(),
                )
                .order_by(InsuranceLine.id)
                .limit(1)
            )
            if line is not None:
                _assign(line, code=code, sort_order=sort_order, is_active=True)
                stats["lines_updated"] += 1
        else:
            if _assign(line, name=name, sort_order=sort_order):
                stats["lines_updated"] += 1
        if line is None:
            line = InsuranceLine(
                broker_id=None, name=name, code=code, sort_order=sort_order, is_active=True
            )
            db.add(line)
            stats["lines_created"] += 1
        lines[code] = line
    db.flush()

    position: dict[str, int] = {}
    for row in rows:
        position[row.line_code] = position.get(row.line_code, 0) + 1
        entry, route = _ENTRY[row.entry]
        values = {
            "insurance_line_id": lines[row.line_code].id,
            "name": row.subtype_name,
            "suggested_entry": entry,
            "suggested_route": route,
            "cmf_codes": cmf_codes_for(row.subtype_code),
            "sort_order": position[row.line_code],
        }
        subtype = db.scalar(
            select(InsuranceSubtype).where(InsuranceSubtype.code == row.subtype_code)
        )
        if subtype is None:
            db.add(InsuranceSubtype(code=row.subtype_code, is_active=True, **values))
            stats["subtypes_created"] += 1
        elif _assign(subtype, **values):
            stats["subtypes_updated"] += 1
    db.flush()
    return stats


def seed_collection_defaults(db: Session) -> dict[str, int]:
    """Missing insurer profiles (``not_contacted``) + broker settings. Flushes only."""
    stats = _empty_stats()
    have_profile = set(db.scalars(select(InsurerCollectionProfile.insurer_id)))
    for insurer_id in db.scalars(select(Insurer.id).order_by(Insurer.id)):
        if insurer_id not in have_profile:
            db.add(InsurerCollectionProfile(insurer_id=insurer_id))
            stats["profiles_created"] += 1
    have_settings = set(db.scalars(select(CollectionSettings.broker_id)))
    for broker_id in db.scalars(select(Broker.id).order_by(Broker.id)):
        if broker_id not in have_settings:
            db.add(CollectionSettings(broker_id=broker_id))
            stats["settings_created"] += 1
    db.flush()
    return stats


def run(db: Session) -> dict[str, int]:
    """The whole v12 data step. Flushes; the caller commits (or rolls back)."""
    stats = seed_catalog(db)
    for key, value in seed_collection_defaults(db).items():
        stats[key] += value
    return stats


__all__ = [
    "CATALOG_CSV",
    "NOTICE_TEMPLATES_JSON",
    "CatalogRow",
    "cmf_codes_for",
    "load_catalog_rows",
    "run",
    "seed_catalog",
    "seed_collection_defaults",
]
