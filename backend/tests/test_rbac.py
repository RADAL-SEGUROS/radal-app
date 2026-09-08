"""RBAC: a low-privilege role is blocked from Manage actions.

``broker_executive`` is the low-privilege role under test. The matrix in
``app.core.roles_config`` grants it View/Create on the operational modules but
``no`` on ``Users.Manage``, so the two Manage-gated endpoints
(``PATCH /users/{id}/role`` and ``PATCH /users/{id}/status``) must answer 403.

The gate is asserted at BOTH levels — the pure matrix and the live HTTP route —
so a router that forgets its dependency cannot pass on the matrix alone.
"""
from __future__ import annotations

import pytest

from app.core.permissions import has_permission
from app.core.roles_config import ACTIONS, MODULES
from tests.conftest import API

LOW_PRIVILEGE = "broker_executive"


class TestMatrix:
    def test_executive_has_no_users_manage(self) -> None:
        assert has_permission(LOW_PRIVILEGE, "Users", "Manage") is False

    def test_admin_has_users_manage(self) -> None:
        assert has_permission("broker_admin", "Users", "Manage") is True

    def test_executive_can_still_do_its_job(self) -> None:
        # The point is a NARROWER role, not a broken one.
        assert has_permission(LOW_PRIVILEGE, "Clients", "View") is True
        assert has_permission(LOW_PRIVILEGE, "Quotes", "Create") is True
        assert has_permission(LOW_PRIVILEGE, "Proposals", "Create") is True

    def test_executive_cannot_approve_or_delete(self) -> None:
        assert has_permission(LOW_PRIVILEGE, "Proposals", "Approve") is False
        assert has_permission(LOW_PRIVILEGE, "Clients", "Delete") is False

    def test_inspector_is_the_narrowest_broker_role(self) -> None:
        assert has_permission("broker_inspector", "Clients", "View") is False
        assert has_permission("broker_inspector", "Inspections", "View") is True

    def test_unknown_role_is_denied_everywhere(self) -> None:
        for module in MODULES:
            for action in ACTIONS:
                assert has_permission("not_a_role", module, action) is False

    def test_no_role_is_denied_everywhere(self) -> None:
        assert has_permission(None, "Users", "Manage") is False


class TestManageIsBlockedOverHttp:
    def test_executive_cannot_change_a_users_role(
        self, client, world, headers_a_exec
    ) -> None:
        response = client.patch(
            f"{API}/users/{world.a.admin.id}/role",
            json={"role": "broker_inspector"},
            headers=headers_a_exec,
        )
        assert response.status_code == 403, response.text

    def test_executive_cannot_change_a_users_status(
        self, client, world, headers_a_exec
    ) -> None:
        response = client.patch(
            f"{API}/users/{world.a.admin.id}/status",
            json={"is_active": False},
            headers=headers_a_exec,
        )
        assert response.status_code == 403, response.text

    def test_the_target_user_is_unchanged_after_the_refusal(
        self, client, world, headers_a_exec, headers_a, db
    ) -> None:
        client.patch(
            f"{API}/users/{world.a.admin.id}/role",
            json={"role": "broker_inspector"},
            headers=headers_a_exec,
        )
        db.expire_all()
        body = client.get(f"{API}/users/{world.a.admin.id}", headers=headers_a).json()
        assert body["role"] == "broker_admin"
        assert body["is_active"] is True

    def test_admin_can_do_what_the_executive_could_not(
        self, client, world, headers_a
    ) -> None:
        # Proves the 403s above are the RBAC gate, not a broken endpoint.
        response = client.patch(
            f"{API}/users/{world.a.executive.id}/role",
            json={"role": "broker_technician"},
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "broker_technician"


class TestOtherDeniedActions:
    def test_executive_cannot_approve_a_proposal(
        self, client, world, headers_a_exec
    ) -> None:
        proposal_id = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": world.a.quote.id,
                "insurer_id": world.insurer.id,
                "source_document_id": world.a.document.id,
            },
            headers=headers_a_exec,
        ).json()["id"]

        # Create was allowed; accept (Approve) must not be.
        response = client.post(f"{API}/proposals/{proposal_id}/accept", headers=headers_a_exec)
        assert response.status_code == 403, response.text

    def test_executive_cannot_delete_a_client(
        self, client, world, headers_a_exec
    ) -> None:
        response = client.delete(
            f"{API}/clients/{world.a.client.id}", headers=headers_a_exec
        )
        assert response.status_code == 403, response.text

    def test_executive_cannot_list_users(self, client, world, headers_a_exec) -> None:
        response = client.get(f"{API}/users", headers=headers_a_exec)
        assert response.status_code == 403, response.text


class TestPermissionsEndpoint:
    def test_permissions_endpoint_reports_the_denial(
        self, client, world, headers_a_exec
    ) -> None:
        response = client.get(f"{API}/auth/permissions", headers=headers_a_exec)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["role"] == LOW_PRIVILEGE
        assert body["matrix"]["Users"]["Manage"] == "no"
        assert body["allowed"]["Users"]["Manage"] is False

    def test_permissions_endpoint_matches_the_matrix_for_the_admin(
        self, client, world, headers_a
    ) -> None:
        body = client.get(f"{API}/auth/permissions", headers=headers_a).json()
        assert body["role"] == "broker_admin"
        assert body["allowed"]["Users"]["Manage"] is True

    @pytest.mark.parametrize("module", ["Reports"])
    def test_out_of_scope_module_is_denied_for_everyone(self, module) -> None:
        for role in ("broker_admin", "broker_executive", "broker_technician"):
            for action in ACTIONS:
                assert has_permission(role, module, action) is False


# =============================================================================
# Process profiles (v3 groups & accounts, spec §6)
# =============================================================================
#
# Three additive broker desks. Each is a SLICE of one tenant, never a second
# tenant: ``broker_id`` scoping still applies on top, and the ``partial``
# CaseFiles.View grant is narrowed by exactly one mechanism —
# ``app.core.case_scope.CASE_VIEW_SCOPE`` — shared by ``_visible()``,
# ``get_case_or_404()``, the packs router and the documents router.

PROCESS_ROLES = ("broker_commercial", "broker_collections", "broker_claims")


def _process_headers(client, db, world, role: str) -> dict[str, str]:
    """Log in as a freshly minted process-profile user of broker A."""
    from tests.conftest import auth_headers, ensure_role_user

    label = role.removeprefix("broker_")
    user = ensure_role_user(db, world.a, role, label)
    return auth_headers(client, user.email)


class TestProcessProfiles:
    """The matrix rows themselves — read straight out of ``roles_config``."""

    @pytest.mark.parametrize("role", PROCESS_ROLES)
    def test_the_role_exists_and_is_a_broker_role(self, role) -> None:
        from app.core.roles_config import ROLES, USER_TYPE_ROLES, user_type_for

        assert role in ROLES
        assert user_type_for(role) == "broker"
        assert role in USER_TYPE_ROLES["broker"]

    @pytest.mark.parametrize("role", PROCESS_ROLES)
    def test_every_module_has_a_row(self, role) -> None:
        """A missing module row would fail closed silently; make it loud."""
        from app.core.roles_config import ACTIONS as ALL_ACTIONS
        from app.core.roles_config import ROLES

        for module in MODULES:
            row = ROLES[role][module]
            assert isinstance(row, dict), f"{role}.{module} is not a module row"
            assert set(row) == set(ALL_ACTIONS), f"{role}.{module} misses an action"

    def test_the_four_original_broker_roles_are_untouched(self) -> None:
        """This pass is additive: nobody's existing grants move."""
        from app.core.roles_config import USER_TYPE_ROLES

        for role in ("broker_admin", "broker_executive", "broker_inspector", "broker_technician"):
            assert role in USER_TYPE_ROLES["broker"]
        assert has_permission("broker_executive", "Users", "Manage") is False
        assert has_permission("broker_admin", "Users", "Manage") is True

    # --- broker_commercial: the executive matrix, plus Groups ----------------

    def test_commercial_is_the_executive_matrix(self) -> None:
        from app.core.roles_config import ACTIONS as ALL_ACTIONS
        from app.core.roles_config import grant_for

        for module in MODULES:
            for action in ALL_ACTIONS:
                assert grant_for("broker_commercial", module, action) == grant_for(
                    "broker_executive", module, action
                ), f"{module}.{action} drifted from broker_executive"

    def test_commercial_owns_groups(self) -> None:
        for action in ("View", "Create", "Edit", "Comment"):
            assert has_permission("broker_commercial", "Groups", action) is True
        assert has_permission("broker_commercial", "Groups", "Delete") is False

    # --- broker_collections: the cobranza desk -------------------------------

    def test_collections_owns_the_ledger(self) -> None:
        from app.core.roles_config import ACTIONS as ALL_ACTIONS

        for action in ALL_ACTIONS:
            assert has_permission("broker_collections", "Collections", action) is True

    def test_collections_reads_the_policy_it_bills(self) -> None:
        assert has_permission("broker_collections", "Policies", "View") is True
        assert has_permission("broker_collections", "Policies", "Edit") is False
        assert has_permission("broker_collections", "Endorsements", "View") is True
        assert has_permission("broker_collections", "Endorsements", "Create") is False

    def test_collections_sees_groups_and_a_narrowed_case_list(self) -> None:
        from app.core.roles_config import grant_for

        assert has_permission("broker_collections", "Groups", "View") is True
        assert grant_for("broker_collections", "CaseFiles", "View") == "partial"
        assert grant_for("broker_collections", "Documents", "View") == "partial"

    @pytest.mark.parametrize("module", ["Claims", "Quotes", "Proposals", "Leads", "Clients"])
    def test_collections_is_denied_the_commercial_modules(self, module) -> None:
        from app.core.roles_config import ACTIONS as ALL_ACTIONS

        for action in ALL_ACTIONS:
            assert has_permission("broker_collections", module, action) is False

    # --- broker_claims: the siniestros desk ----------------------------------

    def test_claims_owns_the_claim_including_the_ruling(self) -> None:
        from app.core.roles_config import ACTIONS as ALL_ACTIONS

        for action in ALL_ACTIONS:
            assert has_permission("broker_claims", "Claims", action) is True
        # Approve is what closes a claim (POST /claims/{id}/close).
        assert has_permission("broker_claims", "Claims", "Approve") is True

    def test_claims_edits_the_policy_it_settles_against(self) -> None:
        assert has_permission("broker_claims", "Policies", "View") is True
        assert has_permission("broker_claims", "Policies", "Edit") is True
        assert has_permission("broker_claims", "Policies", "Delete") is False

    def test_claims_never_touches_the_ledger(self) -> None:
        from app.core.roles_config import ACTIONS as ALL_ACTIONS

        for action in ALL_ACTIONS:
            assert has_permission("broker_claims", "Collections", action) is False

    def test_claims_sees_groups_and_a_narrowed_case_list(self) -> None:
        from app.core.roles_config import grant_for

        assert has_permission("broker_claims", "Groups", "View") is True
        assert grant_for("broker_claims", "CaseFiles", "View") == "partial"

    @pytest.mark.parametrize("role", PROCESS_ROLES)
    def test_reports_stays_out_of_scope(self, role) -> None:
        for action in ACTIONS:
            assert has_permission(role, "Reports", action) is False

    @pytest.mark.parametrize("role", ("broker_collections", "broker_claims"))
    def test_a_desk_never_manages_users(self, role) -> None:
        assert has_permission(role, "Users", "Manage") is False
        assert has_permission(role, "Settings", "Manage") is False


class TestProcessProfilesOverHttp:
    """The same matrix, proved through the live routes."""

    def test_collections_reaches_the_ledger_but_not_the_claims_desk(
        self, client, db, world
    ) -> None:
        headers = _process_headers(client, db, world, "broker_collections")
        assert client.get(f"{API}/collections", headers=headers).status_code == 200
        assert client.get(f"{API}/policies", headers=headers).status_code == 200
        assert client.get(f"{API}/endorsements", headers=headers).status_code == 200
        for path in ("/claims", "/quotes", "/proposals", "/leads", "/clients"):
            assert client.get(f"{API}{path}", headers=headers).status_code == 403, path

    def test_claims_reaches_the_claims_desk_but_not_the_ledger(
        self, client, db, world
    ) -> None:
        headers = _process_headers(client, db, world, "broker_claims")
        assert client.get(f"{API}/claims", headers=headers).status_code == 200
        assert client.get(f"{API}/policies", headers=headers).status_code == 200
        for path in ("/collections", "/endorsements", "/quotes", "/leads"):
            assert client.get(f"{API}{path}", headers=headers).status_code == 403, path

    def test_commercial_works_like_the_executive(self, client, db, world) -> None:
        headers = _process_headers(client, db, world, "broker_commercial")
        assert client.get(f"{API}/clients", headers=headers).status_code == 200
        assert client.get(f"{API}/account-groups", headers=headers).status_code == 200
        assert client.get(f"{API}/leads", headers=headers).status_code == 200
        # ...and is refused exactly where the executive is.
        assert client.get(f"{API}/users", headers=headers).status_code == 403

    @pytest.mark.parametrize("role", PROCESS_ROLES)
    def test_every_desk_can_open_its_group_list(self, client, db, world, role) -> None:
        headers = _process_headers(client, db, world, role)
        assert client.get(f"{API}/account-groups", headers=headers).status_code == 200

    @pytest.mark.parametrize("role", PROCESS_ROLES)
    def test_the_permissions_endpoint_reports_the_same_matrix(
        self, client, db, world, role
    ) -> None:
        from app.core.roles_config import grant_for

        headers = _process_headers(client, db, world, role)
        body = client.get(f"{API}/auth/permissions", headers=headers).json()
        assert body["role"] == role
        for module in MODULES:
            for action in ACTIONS:
                assert body["matrix"][module][action] == grant_for(role, module, action)


class TestCaseViewScope:
    """One narrowing mechanism, applied everywhere a case can be reached."""

    def test_the_registry_only_holds_partial_roles(self) -> None:
        from app.core.case_scope import CASE_VIEW_SCOPE
        from app.core.roles_config import grant_for

        for role in CASE_VIEW_SCOPE:
            assert grant_for(role, "CaseFiles", "View") == "partial", role

    def test_a_full_grant_is_never_narrowed(self) -> None:
        from app.core.case_scope import case_view_scope

        for role in ("broker_admin", "broker_executive", "broker_technician", "broker_commercial"):
            assert case_view_scope(role) is None, role

    def test_a_partial_role_without_a_scope_fails_closed(self) -> None:
        """A half-configured profile must see NOTHING, never everything."""
        from app.core.case_scope import CASE_VIEW_SCOPE, case_view_scope
        from app.core.roles_config import ROLES, _mod

        ROLES["broker_test_profile"] = {"user_type": "broker", "CaseFiles": _mod(View="partial")}
        try:
            scope = case_view_scope("broker_test_profile")
            assert scope is not None
            assert "broker_test_profile" not in CASE_VIEW_SCOPE
        finally:
            ROLES.pop("broker_test_profile")

    def test_collections_sees_its_cobranza_and_the_parent_folder(
        self, client, db, world, insurer_policy
    ) -> None:
        from app.models.enums import CaseFileKind, CaseStage
        from tests.conftest import make_case_file

        account = make_case_file(db, world.a, n=1)
        cobranza = make_case_file(
            db, world.a, n=2, placement_id=None, kind=CaseFileKind.COLLECTION,
            stage=CaseStage.COLLECTION_SCHEDULED, parent_case_file_id=account.id,
            policy_id=insurer_policy.id,
        )
        siniestro = make_case_file(
            db, world.a, n=3, placement_id=None, kind=CaseFileKind.CLAIM,
            stage=CaseStage.CLAIM_REPORTED, parent_case_file_id=account.id,
            policy_id=insurer_policy.id,
        )
        db.commit()

        headers = _process_headers(client, db, world, "broker_collections")
        listing = client.get(f"{API}/case-files", headers=headers).json()
        visible = {item["id"] for item in listing["items"]}
        assert cobranza.id in visible
        assert account.id in visible          # the folder the cuota sits in
        assert siniestro.id not in visible    # another desk's case

        assert client.get(f"{API}/case-files/{cobranza.id}", headers=headers).status_code == 200
        assert client.get(f"{API}/case-files/{siniestro.id}", headers=headers).status_code == 404

    def test_claims_sees_its_siniestro_and_not_the_cobranza(
        self, client, db, world, insurer_policy
    ) -> None:
        from app.models.enums import CaseFileKind, CaseStage
        from tests.conftest import make_case_file

        account = make_case_file(db, world.a, n=1)
        cobranza = make_case_file(
            db, world.a, n=2, placement_id=None, kind=CaseFileKind.COLLECTION,
            stage=CaseStage.COLLECTION_SCHEDULED, parent_case_file_id=account.id,
            policy_id=insurer_policy.id,
        )
        siniestro = make_case_file(
            db, world.a, n=3, placement_id=None, kind=CaseFileKind.CLAIM,
            stage=CaseStage.CLAIM_REPORTED, parent_case_file_id=account.id,
            policy_id=insurer_policy.id,
        )
        db.commit()

        headers = _process_headers(client, db, world, "broker_claims")
        visible = {
            item["id"] for item in client.get(f"{API}/case-files", headers=headers).json()["items"]
        }
        assert siniestro.id in visible
        assert account.id in visible
        assert cobranza.id not in visible
        assert client.get(f"{API}/case-files/{cobranza.id}", headers=headers).status_code == 404

    def test_the_narrowing_reaches_packs_and_documents(
        self, client, db, world, insurer_policy
    ) -> None:
        """``get_case_or_404(user)`` is why a hidden case leaks nowhere."""
        from app.models.document import Document, DocumentCategory
        from app.models.enums import CaseFileKind, CaseStage, EntityType
        from tests.conftest import make_case_file

        account = make_case_file(db, world.a, n=1)
        siniestro = make_case_file(
            db, world.a, n=3, placement_id=None, kind=CaseFileKind.CLAIM,
            stage=CaseStage.CLAIM_REPORTED, parent_case_file_id=account.id,
            policy_id=insurer_policy.id,
        )
        db.flush()
        hidden_doc = Document(
            broker_id=world.a.broker_id,
            entity_type=EntityType.CASE_FILE,
            entity_id=siniestro.id,
            case_file_id=siniestro.id,
            s3_key=f"documents/case_file/{siniestro.id}/claim_notice-1.pdf",
            bucket="test",
            original_name="denuncio.pdf",
            category=DocumentCategory.CLAIM_NOTICE,
        )
        db.add(hidden_doc)
        db.commit()

        headers = _process_headers(client, db, world, "broker_collections")
        # The packs surface of an invisible case is a 404, not its pack list.
        assert (
            client.get(f"{API}/case-files/{siniestro.id}/packs", headers=headers).status_code
            == 404
        )
        # ...and so is every document filed inside it.
        assert client.get(f"{API}/documents/{hidden_doc.id}", headers=headers).status_code == 404
        assert (
            client.get(f"{API}/documents/{hidden_doc.id}/download", headers=headers).status_code
            == 404
        )
        listed = client.get(f"{API}/documents", headers=headers).json()
        assert hidden_doc.id not in {item["id"] for item in listed["items"]}
        assert (
            client.get(
                f"{API}/documents?case_file_id={siniestro.id}", headers=headers
            ).json()["items"]
            == []
        )
