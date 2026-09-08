"""Config-driven RBAC for Radal v2 — the SINGLE SOURCE OF TRUTH for the matrix.

All identifiers here are ENGLISH (roles, user types, modules, actions, grants).
Spanish only ever appears in the UI, via i18n — never in code.

Structure
---------
``ROLES``: dict keyed by role. Each role carries:
  - ``"user_type"``: the actor family it belongs to
    (``platform`` | ``broker`` | ``insurer`` | ``insured``), matching
    ``user.user_type`` in the data model.
  - one key per MODULE, whose value is a dict of ACTION -> grant.

Grant values are plain strings so the matrix stays human-editable and
spreadsheet-friendly:
    - ``"yes"``     -> allowed
    - ``"no"``      -> denied
    - ``"partial"`` -> allowed WITH a restriction (scope / fields / status).
                       The enforcement layer treats "partial" as ALLOWED at the
                       coarse gate and defers the fine-grained restriction to the
                       router/service. See ``is_allowed()`` / ``PARTIAL_IS_ALLOWED``.

Enforcement lives in ``app.core.permissions.require_permission(module, action)``,
which reads ``current_user.role`` and looks it up here.

Multi-tenancy is NOT expressed in this matrix. Tenant scoping (``broker_id``) is
always applied on top, in the repository/query layer — a "yes" here never means
"across brokers".

The ``Reports`` module is declared but OUT OF SCOPE for this pass (all "no"
everywhere) so the matrix stays complete and forward-compatible, and so the UI
can render it visibly disabled ("pronto") rather than as a dead control.
"""
from __future__ import annotations

# --- Canonical vocabularies --------------------------------------------------

# Actor families — mirrors ``user.user_type`` in the data model.
USER_TYPES: list[str] = ["platform", "broker", "insurer", "insured"]

# user_type -> the roles that belong to it.
USER_TYPE_ROLES: dict[str, list[str]] = {
    "platform": [
        "platform_admin",
    ],
    "broker": [
        "broker_admin",
        "broker_executive",
        "broker_inspector",
        "broker_technician",
        # Process profiles (v3 groups & accounts, spec §6). Additive: the four
        # roles above are unchanged, so no existing user moves.
        "broker_commercial",
        "broker_collections",
        "broker_claims",
    ],
    "insured": [
        "insured_admin",
        "insured_user",
    ],
    "insurer": [
        "insurer_admin",
        "insurer_underwriter",
        "insurer_executive",
    ],
}

MODULES: list[str] = [
    "Dashboard",
    "Clients",
    "Assets",
    # The expediente (case file) and the pre-client lead pipeline.
    "CaseFiles",
    "Leads",
    # The broker-private group above the expediente (v3 groups & accounts).
    "Groups",
    "Placements",
    "Quotes",
    "Proposals",
    "Inspections",
    "Insurers",
    "Offerings",
    "Documents",
    "Policies",
    # Post-sale, switched on with the case-file pass.
    "Endorsements",
    "Collections",
    "Claims",
    "Reports",  # OUT OF SCOPE this pass
    "Users",
    "Settings",
]

ACTIONS: list[str] = [
    "View",
    "Create",
    "Edit",
    "Delete",
    "Comment",
    "Upload",
    "Submit",
    "Approve",
    "Manage",
]

# Grant string constants.
GRANT_YES = "yes"
GRANT_NO = "no"
GRANT_PARTIAL = "partial"

GRANTS: list[str] = [GRANT_YES, GRANT_NO, GRANT_PARTIAL]

# When True, "partial" passes the coarse-grained require_permission() gate and
# the router applies the specific restriction. Flip to False to make "partial"
# behave as "no" at the gate (fail-closed) if you prefer.
PARTIAL_IS_ALLOWED = True


def _mod(**actions: str) -> dict[str, str]:
    """Build a module row, defaulting every unspecified ACTION to "no"."""
    row = {a: GRANT_NO for a in ACTIONS}
    row.update(actions)
    return row


# Convenience module rows reused across roles.
_ALL_YES = {a: GRANT_YES for a in ACTIONS}
_ALL_NO = {a: GRANT_NO for a in ACTIONS}
_OUT_OF_SCOPE = dict(_ALL_NO)  # Reports


def _full_role(user_type: str, *, out_of_scope: tuple[str, ...] = ("Reports",)) -> dict:
    """Every module fully granted, except the ones still out of scope."""
    role: dict = {"user_type": user_type}
    for module in MODULES:
        role[module] = dict(_ALL_NO) if module in out_of_scope else dict(_ALL_YES)
    return role


# --- The matrix --------------------------------------------------------------

ROLES: dict[str, dict] = {
    # ============================ PLATFORM ==============================
    # Radal internal staff. Manages the native-insurer catalog, approves
    # insured account requests, global admin. Not scoped to a single broker.
    "platform_admin": _full_role("platform"),

    # ============================= BROKER ===============================
    # The tenant. Primary focus of this build.
    "broker_admin": _full_role("broker"),

    "broker_executive": {
        "user_type": "broker",
        "Dashboard": _mod(View="yes", Comment="yes"),
        "Clients": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Assets": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes"),
        # Owns the expediente end to end, minus pack generation (Manage) and
        # the technician's Approve.
        "CaseFiles": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Leads": _mod(View="yes", Create="yes", Edit="yes", Comment="yes"),
        "Groups": _mod(View="yes", Create="yes", Edit="yes", Comment="yes"),
        "Placements": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Quotes": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Proposals": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        # Requests inspections and reads reports; cannot author/sign one.
        "Inspections": _mod(View="yes", Create="yes", Edit="partial", Comment="yes", Upload="yes", Submit="yes"),
        # May add an EXTERNAL insurer discovered on an uploaded proposal;
        # native-partner records stay platform-managed.
        "Insurers": _mod(View="yes", Create="yes", Edit="partial", Comment="yes"),
        "Offerings": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Documents": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Policies": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes"),
        "Endorsements": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        # Reads and comments the ledger; the technician/admin edit it.
        "Collections": _mod(View="yes", Comment="yes", Upload="yes"),
        "Claims": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": _mod(View="partial"),
    },

    "broker_inspector": {
        "user_type": "broker",
        "Dashboard": _mod(View="yes"),
        "Clients": dict(_ALL_NO),
        # Needs the asset sheet to inspect it, but does not own the record.
        "Assets": _mod(View="yes", Comment="yes"),
        # Only the expedientes that have an inspection assigned to them — the
        # router narrows the queryset; "partial" passes the coarse gate.
        "CaseFiles": _mod(View="partial"),
        "Leads": dict(_ALL_NO),
        # Only the groups containing a case they can see.
        "Groups": _mod(View="partial"),
        "Placements": _mod(View="partial", Comment="yes"),
        "Quotes": dict(_ALL_NO),
        "Proposals": dict(_ALL_NO),
        "Inspections": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        "Insurers": dict(_ALL_NO),
        "Offerings": dict(_ALL_NO),
        # Only documents attached to the assets/inspections assigned to them.
        "Documents": _mod(View="partial", Create="partial", Upload="partial"),
        # Reads the policy behind the risk it inspects; never edits it.
        "Policies": _mod(View="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": _mod(View="yes"),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": dict(_ALL_NO),
    },

    "broker_technician": {
        "user_type": "broker",
        "Dashboard": _mod(View="yes", Comment="yes"),
        "Clients": _mod(View="yes", Edit="partial", Comment="yes", Upload="yes"),
        "Assets": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes"),
        # Owns the technical side of the expediente, including sign-off.
        "CaseFiles": _mod(
            View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes",
            Submit="yes", Approve="yes",
        ),
        "Leads": _mod(View="yes"),
        "Groups": _mod(View="yes", Comment="yes"),
        "Placements": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes", Approve="partial"),
        "Quotes": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes"),
        # Owns proposal standardization: confirms AI extractions, runs the comparator.
        "Proposals": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes", Approve="yes"),
        "Inspections": _mod(View="yes", Comment="yes", Upload="yes", Approve="partial"),
        "Insurers": _mod(View="yes", Create="yes", Edit="partial", Comment="yes"),
        "Offerings": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="yes", Approve="yes"),
        "Documents": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Submit="partial"),
        "Policies": _mod(View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes"),
        "Endorsements": _mod(
            View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes",
            Submit="yes", Approve="yes",
        ),
        "Collections": _mod(View="yes", Comment="yes"),
        "Claims": _mod(
            View="yes", Create="yes", Edit="yes", Comment="yes", Upload="yes", Approve="yes"
        ),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": _mod(View="partial"),
    },

    # ------------------------- PROCESS PROFILES -------------------------
    # Narrow desks inside one brokerage. Each one is a SLICE of the tenant, not
    # a second tenant: ``broker_id`` scoping still applies on top, and the
    # ``partial`` CaseFiles.View grants are narrowed by exactly one mechanism —
    # ``app.core.case_scope.CASE_VIEW_SCOPE`` — shared by ``_visible()`` and
    # ``get_case_or_404()`` so the packs and documents routers cannot leak a
    # case the role's own list hides.
    #
    # The team's definitive permission matrix is still unfilled (spec §9.4);
    # these grants implement spec §6 verbatim and are meant to be edited HERE,
    # never in a router.

    # ``broker_commercial`` is DERIVED from ``broker_executive`` just below the
    # matrix (it is the same 19x9 grid; copying it here would drift silently).

    # The collections desk: owns the cobranza ledger and nothing else. Sees the
    # collection cases plus the account folders they hang off (without the
    # parent the cuota has no expediente to sit in), and reads the policy it
    # bills. No clients, no quotes, no proposals, no claims.
    "broker_collections": {
        "user_type": "broker",
        "Dashboard": _mod(View="yes", Comment="yes"),
        "Clients": dict(_ALL_NO),
        "Assets": dict(_ALL_NO),
        # Narrowed by CASE_VIEW_SCOPE to collection cases + their parents.
        "CaseFiles": _mod(View="partial"),
        "Leads": dict(_ALL_NO),
        "Groups": _mod(View="yes"),
        "Placements": dict(_ALL_NO),
        "Quotes": dict(_ALL_NO),
        "Proposals": dict(_ALL_NO),
        "Inspections": dict(_ALL_NO),
        "Insurers": dict(_ALL_NO),
        "Offerings": dict(_ALL_NO),
        # Only the documents of the cases the scope lets through.
        "Documents": _mod(View="partial"),
        "Policies": _mod(View="yes"),
        "Endorsements": _mod(View="yes"),
        "Collections": dict(_ALL_YES),
        "Claims": dict(_ALL_NO),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": dict(_ALL_NO),
    },

    # The claims desk: owns the siniestro end to end, including the ruling
    # (Approve closes a claim). Edits the policy it settles against; never
    # touches the collection ledger.
    "broker_claims": {
        "user_type": "broker",
        "Dashboard": _mod(View="yes", Comment="yes"),
        "Clients": dict(_ALL_NO),
        "Assets": dict(_ALL_NO),
        # Narrowed by CASE_VIEW_SCOPE to claim cases + their parents.
        "CaseFiles": _mod(View="partial"),
        "Leads": dict(_ALL_NO),
        "Groups": _mod(View="yes"),
        "Placements": dict(_ALL_NO),
        "Quotes": dict(_ALL_NO),
        "Proposals": dict(_ALL_NO),
        "Inspections": dict(_ALL_NO),
        "Insurers": dict(_ALL_NO),
        "Offerings": dict(_ALL_NO),
        # A denuncio IS a document; uploading evidence is the desk's daily job,
        # still narrowed to the cases the scope lets through.
        "Documents": _mod(View="partial", Create="partial", Upload="partial"),
        "Policies": _mod(View="yes", Edit="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": dict(_ALL_YES),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": dict(_ALL_NO),
    },

    # ============================= INSURED ==============================
    # Mostly passive. Account exists only after a manually approved claim on
    # the RUT. "partial" everywhere = restricted to their own RUT's records,
    # with broker-internal fields (commission, internal notes) gated later.
    "insured_admin": {
        "user_type": "insured",
        "Dashboard": _mod(View="yes"),
        "Clients": _mod(View="partial", Comment="yes"),
        "Assets": _mod(View="partial", Comment="yes", Upload="partial"),
        # Case files, leads and groups are broker-internal this pass.
        "CaseFiles": dict(_ALL_NO),
        "Leads": dict(_ALL_NO),
        "Groups": dict(_ALL_NO),
        "Placements": _mod(View="partial", Comment="yes"),
        "Quotes": _mod(View="partial", Comment="yes"),
        "Proposals": _mod(View="partial", Comment="yes"),
        "Inspections": _mod(View="partial", Comment="yes"),
        "Insurers": dict(_ALL_NO),
        "Offerings": _mod(View="partial", Comment="yes"),
        "Documents": _mod(View="partial", Comment="yes", Upload="partial"),
        "Policies": _mod(View="partial", Comment="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": _mod(View="partial", Create="partial", Comment="yes", Upload="yes", Submit="partial"),
        "Reports": dict(_OUT_OF_SCOPE),
        # Manages only the users of their own insured organization.
        "Users": _mod(View="yes", Create="yes", Edit="yes", Submit="yes", Approve="yes", Manage="partial"),
        "Settings": _mod(View="partial", Edit="partial", Upload="yes"),
    },

    "insured_user": {
        "user_type": "insured",
        "Dashboard": _mod(View="yes"),
        "Clients": _mod(View="partial", Comment="yes"),
        "Assets": _mod(View="partial", Comment="yes"),
        # Case files, leads and groups are broker-internal this pass.
        "CaseFiles": dict(_ALL_NO),
        "Leads": dict(_ALL_NO),
        "Groups": dict(_ALL_NO),
        "Placements": _mod(View="partial", Comment="yes"),
        "Quotes": _mod(View="partial", Comment="yes"),
        "Proposals": _mod(View="partial", Comment="yes"),
        "Inspections": _mod(View="partial", Comment="yes"),
        "Insurers": dict(_ALL_NO),
        "Offerings": _mod(View="partial", Comment="yes"),
        "Documents": _mod(View="partial", Comment="yes", Upload="partial"),
        "Policies": _mod(View="partial", Comment="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": _mod(View="partial", Comment="yes", Upload="yes"),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": dict(_ALL_NO),
    },

    # ============================= INSURER ==============================
    # Portal scoping is DEFERRED (see v2-architecture.md §7): an insurer on a
    # closed deal sees everything; others see only their own proposal plus an
    # anonymized insured. That is what "partial" encodes here.
    "insurer_admin": {
        "user_type": "insurer",
        "Dashboard": _mod(View="yes"),
        "Clients": _mod(View="partial", Comment="yes"),
        "Assets": _mod(View="partial", Comment="yes"),
        # Case files, leads and groups are broker-internal this pass.
        "CaseFiles": dict(_ALL_NO),
        "Leads": dict(_ALL_NO),
        "Groups": dict(_ALL_NO),
        "Placements": _mod(View="partial", Comment="yes"),
        "Quotes": _mod(View="partial", Comment="yes"),
        "Proposals": _mod(View="partial", Create="partial", Edit="partial", Comment="yes", Upload="yes", Submit="yes", Approve="partial"),
        "Inspections": _mod(View="partial", Comment="yes"),
        # Their own insurer record and its contacts.
        "Insurers": _mod(View="partial", Edit="partial", Comment="yes", Upload="yes", Manage="partial"),
        "Offerings": dict(_ALL_NO),
        "Documents": _mod(View="partial", Comment="yes", Upload="partial"),
        "Policies": _mod(View="partial", Comment="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": _mod(View="partial", Comment="yes"),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": _mod(View="yes", Create="yes", Edit="yes", Submit="yes", Approve="yes", Manage="partial"),
        "Settings": _mod(View="partial", Edit="partial", Upload="yes"),
    },

    "insurer_underwriter": {
        "user_type": "insurer",
        "Dashboard": _mod(View="yes"),
        "Clients": _mod(View="partial", Comment="yes"),
        "Assets": _mod(View="partial", Comment="yes"),
        # Case files, leads and groups are broker-internal this pass.
        "CaseFiles": dict(_ALL_NO),
        "Leads": dict(_ALL_NO),
        "Groups": dict(_ALL_NO),
        # Approves at the pre-underwriting gate.
        "Placements": _mod(View="partial", Comment="yes", Approve="partial"),
        "Quotes": _mod(View="partial", Comment="yes"),
        "Proposals": _mod(View="partial", Create="yes", Edit="partial", Comment="yes", Upload="yes", Submit="yes", Approve="yes"),
        "Inspections": _mod(View="partial", Comment="yes"),
        "Insurers": _mod(View="partial"),
        "Offerings": dict(_ALL_NO),
        "Documents": _mod(View="partial", Comment="yes", Upload="partial"),
        "Policies": _mod(View="partial", Comment="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": _mod(View="partial", Comment="yes"),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": dict(_ALL_NO),
    },

    "insurer_executive": {
        "user_type": "insurer",
        "Dashboard": _mod(View="yes"),
        "Clients": _mod(View="partial", Comment="yes"),
        "Assets": _mod(View="partial", Comment="yes"),
        # Case files, leads and groups are broker-internal this pass.
        "CaseFiles": dict(_ALL_NO),
        "Leads": dict(_ALL_NO),
        "Groups": dict(_ALL_NO),
        "Placements": _mod(View="partial", Comment="yes"),
        "Quotes": _mod(View="partial", Comment="yes"),
        # Submits proposals but cannot bind them — that is the underwriter's call.
        "Proposals": _mod(View="partial", Create="yes", Edit="partial", Comment="yes", Upload="yes", Submit="yes"),
        "Inspections": _mod(View="partial", Comment="yes"),
        "Insurers": _mod(View="partial"),
        "Offerings": dict(_ALL_NO),
        "Documents": _mod(View="partial", Comment="yes", Upload="partial"),
        "Policies": _mod(View="partial", Comment="yes"),
        "Endorsements": dict(_ALL_NO),
        "Collections": dict(_ALL_NO),
        "Claims": _mod(View="partial", Comment="yes"),
        "Reports": dict(_OUT_OF_SCOPE),
        "Users": dict(_ALL_NO),
        "Settings": dict(_ALL_NO),
    },
}


# ``broker_commercial`` IS the executive matrix (spec §6). Deriving it instead of
# copying keeps the two in lockstep until the team deliberately splits them —
# a copied 19x9 grid drifts silently, a derivation cannot.
ROLES["broker_commercial"] = {
    key: (value if key == "user_type" else dict(value))
    for key, value in ROLES["broker_executive"].items()
}

# --- Lookup helpers (used by the enforcement layer) --------------------------

def user_type_for(role: str) -> str | None:
    """Return the user_type for a role, or None if the role is unknown."""
    entry = ROLES.get(role)
    return entry.get("user_type") if entry else None


def roles_for_user_type(user_type: str) -> list[str]:
    """Return the roles valid for a given user_type (empty if unknown)."""
    return list(USER_TYPE_ROLES.get(user_type, []))


def grant_for(role: str, module: str, action: str) -> str:
    """Return the raw grant ("yes" | "no" | "partial") for (role, module, action).

    Unknown role / module / action fail closed as "no".
    """
    entry = ROLES.get(role)
    if not entry:
        return GRANT_NO
    mod = entry.get(module)
    if not isinstance(mod, dict):
        return GRANT_NO
    return mod.get(action, GRANT_NO)


def is_allowed(role: str, module: str, action: str) -> bool:
    """Coarse-grained gate used by require_permission().

    "yes" -> True; "no" -> False; "partial" -> PARTIAL_IS_ALLOWED (default True,
    with the fine-grained restriction enforced by the router/service).
    """
    grant = grant_for(role, module, action)
    if grant == GRANT_YES:
        return True
    if grant == GRANT_PARTIAL:
        return PARTIAL_IS_ALLOWED
    return False


def is_partial(role: str, module: str, action: str) -> bool:
    """True when the grant is "partial" (router must apply an extra restriction)."""
    return grant_for(role, module, action) == GRANT_PARTIAL


__all__ = [
    "USER_TYPES",
    "USER_TYPE_ROLES",
    "MODULES",
    "ACTIONS",
    "GRANTS",
    "ROLES",
    "GRANT_YES",
    "GRANT_NO",
    "GRANT_PARTIAL",
    "PARTIAL_IS_ALLOWED",
    "user_type_for",
    "roles_for_user_type",
    "grant_for",
    "is_allowed",
    "is_partial",
]
