from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import Membership, PersonalAPIKey, Project

from .dataset import load, noise_events, reference_events

pytestmark = [pytest.mark.django_db, pytest.mark.clickhouse]

JUNE = {"date_from": "2026-06-01", "date_to": "2026-06-30"}


@pytest.fixture
def user(project: Project) -> User:
    user = User.objects.create_user("ana", password="pw-123456")
    Membership.objects.create(user=user, project=project)
    return user


@pytest.fixture
def api(
    user: User, project: Project, other_project: Project, clickhouse: ClickHouseClient
) -> APIClient:
    load(clickhouse, reference_events(project.pk))
    load(clickhouse, noise_events(other_project.pk))
    _, key = PersonalAPIKey.issue(user=user, project=project, label="test")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
    return client


def url(project: Project, path: str) -> str:
    return f"/api/projects/{project.pk}/{path}"


def test_key_sees_only_its_project(
    api: APIClient, project: Project, other_project: Project
) -> None:
    assert [p["id"] for p in api.get("/api/projects").json()] == [project.pk]
    assert api.get(url(other_project, "events")).status_code == 404
    query = {"kind": "trend", "series": [{"event": "$pageview"}], "date_range": JUNE}
    assert api.post(url(other_project, "query"), query, format="json").status_code == 404


def test_bad_or_missing_credentials(project: Project) -> None:
    client = APIClient()
    assert client.get(url(project, "events")).status_code in (401, 403)
    client.credentials(HTTP_AUTHORIZATION="Bearer ppk_not-a-real-key")
    assert client.get(url(project, "events")).status_code == 401


def test_session_login_respects_membership_and_csrf(
    user: User, project: Project, other_project: Project, clickhouse: ClickHouseClient
) -> None:
    client = APIClient(enforce_csrf_checks=True)
    assert client.get("/api/auth/me").json() == {"authenticated": False}
    csrf = client.cookies["csrftoken"].value
    response = client.post(
        "/api/auth/login",
        {"username": "ana", "password": "pw-123456"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["projects"] == [{"id": project.pk, "name": project.name}]
    assert client.get(url(other_project, "events")).status_code == 404
    insight = {"name": "x", "query": {"kind": "trend", "series": [{"event": "a"}]}}
    assert (
        client.post(url(project, "insights"), insight, format="json").status_code == 403
    )  # no CSRF


def test_events_and_properties(api: APIClient, project: Project) -> None:
    events = {e["name"]: e["count"] for e in api.get(url(project, "events")).json()}
    assert events["$pageview"] == 7 and events["signed_up"] == 4
    props = api.get(url(project, "properties"), {"event": "$pageview"}).json()
    assert props[0]["key"] == "$browser"
    assert set(props[0]["examples"]) == {"Chrome", "Firefox", "Safari"}
    assert props[0]["seen_in"] == pytest.approx(6 / 7, abs=0.001)


def test_query_runs_and_unknown_names_get_suggestions(api: APIClient, project: Project) -> None:
    ok = api.post(
        url(project, "query"),
        {"kind": "trend", "series": [{"event": "$pageview"}], "date_range": JUNE},
        format="json",
    )
    assert ok.status_code == 200 and ok.json()["series"][0]["total"] == 7

    typo = api.post(
        url(project, "query"), {"kind": "trend", "series": [{"event": "signup"}]}, format="json"
    )
    assert typo.status_code == 400
    assert typo.json()["error"] == "unknown_event"
    assert "signed_up" in typo.json()["suggestions"]

    bad_prop: dict[str, Any] = {
        "kind": "trend",
        "series": [{"event": "$pageview"}],
        "breakdown": "browser",
    }
    body = api.post(url(project, "query"), bad_prop, format="json").json()
    assert body["error"] == "unknown_property" and body["suggestions"] == ["$browser"]

    invalid = api.post(
        url(project, "query"), {"kind": "funnel", "steps": [{"event": "a"}]}, format="json"
    )
    assert invalid.status_code == 400 and "steps" in invalid.json()["message"]


def test_insights_and_dashboards(api: APIClient, project: Project) -> None:
    query = {
        "kind": "funnel",
        "steps": [{"event": "signed_up"}, {"event": "project_created"}],
        "conversion_window": 1,
        "date_range": JUNE,
    }
    created = api.post(
        url(project, "insights"), {"name": "Activation", "query": query}, format="json"
    )
    assert created.status_code == 201
    insight_id = created.json()["id"]
    assert created.json()["query"]["conversion_window_unit"] == "day"  # stored normalized

    result = api.get(url(project, f"insights/{insight_id}/result")).json()
    assert [s["persons"] for s in result["overall"]] == [3, 2]

    patched = api.patch(
        url(project, f"insights/{insight_id}"), {"name": "Activation (1d)"}, format="json"
    )
    assert patched.json()["name"] == "Activation (1d)"
    assert [i["id"] for i in api.get(url(project, "insights")).json()] == [insight_id]

    dash = api.post(
        url(project, "dashboards"), {"name": "Growth", "insight_ids": [insight_id]}, format="json"
    )
    assert dash.status_code == 201
    assert [i["name"] for i in dash.json()["insights"]] == ["Activation (1d)"]
    missing = api.post(
        url(project, "dashboards"), {"name": "x", "insight_ids": [999999]}, format="json"
    )
    assert missing.status_code == 404

    assert api.delete(url(project, f"insights/{insight_id}")).status_code == 204
    assert api.get(url(project, f"insights/{insight_id}")).status_code == 404


def test_insight_from_another_project_is_not_reachable(
    api: APIClient, project: Project, other_project: Project
) -> None:
    from product_pulse.core.models import Insight

    foreign = Insight.objects.create(
        project=other_project, name="secret", query={"kind": "trend", "series": [{"event": "a"}]}
    )
    assert api.get(url(project, f"insights/{foreign.pk}")).status_code == 404
    assert api.get(url(project, f"insights/{foreign.pk}/result")).status_code == 404


def test_query_schema_is_published(api: APIClient) -> None:
    schema = api.get("/api/schema/query").json()
    assert set(schema["discriminator"]["mapping"]) == {"trend", "funnel", "retention"}
