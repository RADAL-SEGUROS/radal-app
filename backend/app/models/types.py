"""Shared column types.

Money is always **UF** (Unidad de Fomento), never CLP, and always exact numeric —
never float. Percentages are expressed 0-100 (never 0-1). Rates are per-mille
(``pormil``), which is how the Chilean market quotes property premiums.
"""
from __future__ import annotations

from sqlalchemy import JSON, Numeric

# UF amounts: up to 10 integer digits, 4 decimals (UF is quoted to 2, we keep
# head-room for computed intermediates such as VAT).
UF = Numeric(14, 4)

# Percentages, 0-100 (e.g. commission_pct = 15.0, pml_pct = 65.0).
PCT = Numeric(6, 3)

# Per-mille rates (tasa por mil), e.g. 1.32.
RATE = Numeric(9, 4)

# Inspection scores, 0-100.
SCORE = Numeric(6, 2)

# Areas in m2.
AREA = Numeric(14, 2)

# Distances in km.
DISTANCE_KM = Numeric(8, 2)

# Portable JSON: TEXT-backed on SQLite, native JSON on MySQL 5.7+/8.
JSONType = JSON

__all__ = ["UF", "PCT", "RATE", "SCORE", "AREA", "DISTANCE_KM", "JSONType"]
