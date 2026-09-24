"""Governance rules and project context entries: CRUD APIs, see acc.api.routers.governance
and the context endpoints nested in acc.api.routers.projects."""

from typing import Any

import httpx
import pytest

from tests.integration.test_auth import assert_problem

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

RULES = "/api/v1/governance-rules"


async def create_rule(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {
        "category": "security",
        "title": "Test rule",
        "statement": "Do the thing.",
        "effect": "deny",
        **overrides,
    }
    response = await client.post(RULES, json=body)
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


class TestListAndCreate:
    async def test_seed_rules_are_listed(self, signed_in: httpx.AsyncClient) -> None:
        response = await signed_in.get(RULES)
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 13  # from acc.commands.seed.GOVERNANCE_RULES
        assert all(r["enabled"] for r in items)

    async def test_filter_by_category(self, signed_in: httpx.AsyncClient) -> None:
        items = (await signed_in.get(RULES, params={"category": "tools"})).json()["items"]
        assert items
        assert all(r["category"] == "tools" for r in items)

    async def test_filter_by_enabled(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in, title="Disable me")
        await signed_in.post(f"{RULES}/{rule['id']}/disable")
        disabled = (await signed_in.get(RULES, params={"enabled": False})).json()["items"]
        assert [r["id"] for r in disabled] == [rule["id"]]

    async def test_create_sets_defaults(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in)
        assert rule["priority"] == 100
        assert rule["applies_to"] == []
        assert rule["enabled"] is True

    async def test_duplicate_title_is_409(self, signed_in: httpx.AsyncClient) -> None:
        await create_rule(signed_in, title="Only once")
        response = await signed_in.post(
            RULES,
            json={
                "category": "code",
                "title": "Only once",
                "statement": "x",
                "effect": "advisory",
            },
        )
        assert_problem(response, 409)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"category": "not-a-category"},
            {"effect": "not-an-effect"},
            {"title": ""},
            {"statement": ""},
        ],
        ids=["category", "effect", "empty_title", "empty_statement"],
    )
    async def test_rejects_invalid(
        self, signed_in: httpx.AsyncClient, overrides: dict[str, Any]
    ) -> None:
        body = {
            "category": "security",
            "title": "x",
            "statement": "x",
            "effect": "deny",
            **overrides,
        }
        assert_problem(await signed_in.post(RULES, json=body), 422)


class TestGetPatchToggle:
    async def test_get_unknown_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(
            await signed_in.get(f"{RULES}/00000000-0000-0000-0000-000000000000"), 404
        )

    async def test_patch_edits_editable_fields_only(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in)
        response = await signed_in.patch(
            f"{RULES}/{rule['id']}", json={"statement": "Updated statement.", "effect": "allow"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["statement"] == "Updated statement."
        assert body["effect"] == "allow"
        assert body["category"] == rule["category"]  # category is not editable

    async def test_patch_rejects_category_field(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in)
        response = await signed_in.patch(f"{RULES}/{rule['id']}", json={"category": "code"})
        assert_problem(response, 422)

    async def test_patch_empty_body_is_422(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in)
        assert_problem(await signed_in.patch(f"{RULES}/{rule['id']}", json={}), 422)

    async def test_enable_disable_round_trip(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in)
        disabled = (await signed_in.post(f"{RULES}/{rule['id']}/disable")).json()
        assert disabled["enabled"] is False
        enabled = (await signed_in.post(f"{RULES}/{rule['id']}/enable")).json()
        assert enabled["enabled"] is True

    async def test_double_disable_is_a_noop(self, signed_in: httpx.AsyncClient) -> None:
        rule = await create_rule(signed_in)
        await signed_in.post(f"{RULES}/{rule['id']}/disable")
        response = await signed_in.post(f"{RULES}/{rule['id']}/disable")
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_unknown_rule_actions_are_404(self, signed_in: httpx.AsyncClient) -> None:
        missing = "00000000-0000-0000-0000-000000000000"
        assert_problem(await signed_in.patch(f"{RULES}/{missing}", json={"effect": "deny"}), 404)
        assert_problem(await signed_in.post(f"{RULES}/{missing}/enable"), 404)
        assert_problem(await signed_in.post(f"{RULES}/{missing}/disable"), 404)


class TestAccessControl:
    async def test_requires_a_session(self, client: httpx.AsyncClient) -> None:
        assert_problem(await client.get(RULES), 401)

    async def test_mutations_require_same_origin(self, signed_in: httpx.AsyncClient) -> None:
        response = await signed_in.post(
            RULES,
            json={"category": "code", "title": "csrf", "statement": "x", "effect": "advisory"},
            headers={"origin": "https://evil.example"},
        )
        assert_problem(response, 403)


class TestProjectContext:
    CONTEXT = "/api/v1/projects/acc/context"

    async def create_entry(self, client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
        body = {"kind": "gotcha", "title": "WSL path caveat", "body": "Use UNC paths.", **overrides}
        response = await client.post(self.CONTEXT, json=body)
        assert response.status_code == 201, response.text
        out: dict[str, Any] = response.json()
        return out

    async def test_empty_project_has_no_entries(self, signed_in: httpx.AsyncClient) -> None:
        response = await signed_in.get(self.CONTEXT)
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_unknown_project_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(await signed_in.get("/api/v1/projects/ghost/context"), 404)
        assert_problem(
            await signed_in.post(
                "/api/v1/projects/ghost/context",
                json={"kind": "gotcha", "title": "x", "body": "x"},
            ),
            404,
        )

    async def test_create_list_patch_delete_round_trip(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        entry = await self.create_entry(signed_in)
        listed = (await signed_in.get(self.CONTEXT)).json()["items"]
        assert [e["id"] for e in listed] == [entry["id"]]

        patched = await signed_in.patch(
            f"{self.CONTEXT}/{entry['id']}", json={"body": "Updated body."}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["body"] == "Updated body."
        assert patched.json()["title"] == entry["title"]  # untouched field preserved

        deleted = await signed_in.delete(f"{self.CONTEXT}/{entry['id']}")
        assert deleted.status_code == 204
        assert (await signed_in.get(self.CONTEXT)).json()["items"] == []

    async def test_patch_unknown_entry_is_404(self, signed_in: httpx.AsyncClient) -> None:
        missing = "00000000-0000-0000-0000-000000000000"
        assert_problem(
            await signed_in.patch(f"{self.CONTEXT}/{missing}", json={"body": "x"}), 404
        )

    async def test_delete_unknown_entry_is_404(self, signed_in: httpx.AsyncClient) -> None:
        missing = "00000000-0000-0000-0000-000000000000"
        assert_problem(await signed_in.delete(f"{self.CONTEXT}/{missing}"), 404)

    async def test_rejects_invalid_kind(self, signed_in: httpx.AsyncClient) -> None:
        response = await signed_in.post(
            self.CONTEXT, json={"kind": "not-a-kind", "title": "x", "body": "x"}
        )
        assert_problem(response, 422)

    async def test_entries_are_isolated_per_project(self, signed_in: httpx.AsyncClient) -> None:
        await signed_in.post(
            "/api/v1/projects", json={"slug": "other", "name": "Other", "key_prefix": "OTH"}
        )
        await self.create_entry(signed_in)
        await signed_in.post(
            "/api/v1/projects/other/context",
            json={"kind": "glossary", "title": "Other project entry", "body": "x"},
        )
        acc_items = (await signed_in.get(self.CONTEXT)).json()["items"]
        other_items = (await signed_in.get("/api/v1/projects/other/context")).json()["items"]
        assert [e["title"] for e in acc_items] == ["WSL path caveat"]
        assert [e["title"] for e in other_items] == ["Other project entry"]
