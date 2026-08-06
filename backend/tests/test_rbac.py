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
