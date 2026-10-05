"""REST API. Thin: authentication and access here, everything else in AnalyticsService.

Bodies are validated with the same pydantic models the MCP tools use, and errors share
one shape: `{"error": code, "message": ..., "suggestions": [...]}`.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.db.models import QuerySet
from django.http import Http404
from django.middleware.csrf import get_token
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from product_pulse.analytics.service import (
    QUERY_ADAPTER,
    AnalyticsError,
    AnalyticsService,
    InsightOut,
    parse_query,
)
from product_pulse.clickhouse.client import ClickHouseError, default_client
from product_pulse.core.models import Dashboard, DashboardTile, PersonalAPIKey, Project


def error(code: str, message: str, status: int, suggestions: list[str] | None = None) -> Response:
    return Response(
        {"error": code, "message": message, "suggestions": suggestions or []}, status=status
    )


def accessible_projects(request: Request) -> QuerySet[Project]:
    if isinstance(request.auth, PersonalAPIKey):
        return Project.objects.filter(pk=request.auth.project_id)
    if not request.user.is_authenticated:
        return Project.objects.none()
    return Project.objects.filter(membership__user_id=request.user.pk)


def project_for(request: Request, project_id: int) -> Project:
    # 404 rather than 403 for projects you can't see, so ids can't be probed.
    project = accessible_projects(request).filter(pk=project_id).first()
    if project is None:
        raise Http404
    return project


class ProjectView(APIView):
    """Base for /api/projects/<id>/...: resolves the project and maps service errors."""

    def service(self, request: Request, project_id: int) -> AnalyticsService:
        return AnalyticsService(project_for(request, project_id).pk, default_client())

    def handle_exception(self, exc: Exception) -> Response:
        if isinstance(exc, AnalyticsError):
            status = 404 if exc.code == "not_found" else 400
            return error(exc.code, exc.message, status, exc.suggestions)
        if isinstance(exc, ClickHouseError):
            return error("query_failed", "The query could not be run, try again shortly.", 503)
        return super().handle_exception(exc)


# -- auth -------------------------------------------------------------------------------


class LoginBody(BaseModel):
    username: str
    password: str


class LoginView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        try:
            body = LoginBody.model_validate(request.data)
        except ValidationError:
            return error("invalid_body", "Send username and password.", 400)
        user = authenticate(request._request, username=body.username, password=body.password)
        if user is None:
            return error("invalid_credentials", "Wrong username or password.", 400)
        login(request._request, user)
        return Response({"username": user.get_username()})


class LogoutView(APIView):
    def post(self, request: Request) -> Response:
        logout(request._request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        get_token(request._request)  # sets the CSRF cookie the SPA sends back
        if not request.user or not request.user.is_authenticated:
            return Response({"authenticated": False})
        projects = [{"id": p.pk, "name": p.name} for p in accessible_projects(request)]
        return Response(
            {"authenticated": True, "username": request.user.get_username(), "projects": projects}
        )


# -- discovery and queries -------------------------------------------------------------


class ProjectListView(APIView):
    def get(self, request: Request) -> Response:
        return Response(
            [
                {"id": p.pk, "name": p.name, "api_token": p.api_token}
                for p in accessible_projects(request).order_by("pk")
            ]
        )


class EventListView(ProjectView):
    def get(self, request: Request, project_id: int) -> Response:
        events = self.service(request, project_id).list_events()
        return Response([e.model_dump(mode="json") for e in events])


class PropertyListView(ProjectView):
    def get(self, request: Request, project_id: int) -> Response:
        event = request.query_params.get("event")
        if not event:
            return error("missing_event", "Pass ?event=<event name>.", 400)
        properties = self.service(request, project_id).event_properties(event)
        return Response([p.model_dump(mode="json") for p in properties])


class QueryView(ProjectView):
    def post(self, request: Request, project_id: int) -> Response:
        service = self.service(request, project_id)
        result = service.run(parse_query(request.data))
        return Response(result.model_dump(mode="json"))


class QuerySchemaView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        return Response(QUERY_ADAPTER.json_schema())


# -- insights and dashboards ------------------------------------------------------------


class InsightBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    query: dict[str, Any]


class InsightPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    query: dict[str, Any] | None = None


def _body[M: BaseModel](model: type[M], data: object) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"])
        raise AnalyticsError("invalid_body", f"{location}: {first['msg']}") from exc


class InsightListView(ProjectView):
    def get(self, request: Request, project_id: int) -> Response:
        insights = self.service(request, project_id).list_insights()
        return Response([i.model_dump(mode="json") for i in insights])

    def post(self, request: Request, project_id: int) -> Response:
        body = _body(InsightBody, request.data)
        insight = self.service(request, project_id).create_insight(
            name=body.name,
            description=body.description,
            query=parse_query(body.query),
            user=request.user,
        )
        return Response(insight.model_dump(mode="json"), status=201)


class InsightDetailView(ProjectView):
    def get(self, request: Request, project_id: int, insight_id: int) -> Response:
        insight = self.service(request, project_id).get_insight(insight_id)
        return Response(InsightOut.from_model(insight).model_dump(mode="json"))

    def patch(self, request: Request, project_id: int, insight_id: int) -> Response:
        service = self.service(request, project_id)
        insight = service.get_insight(insight_id)
        body = _body(InsightPatch, request.data)
        if body.query is not None:
            query = parse_query(body.query)
            service.validate(query)
            insight.query = query.model_dump(mode="json")
        if body.name is not None:
            insight.name = body.name
        if body.description is not None:
            insight.description = body.description
        insight.save()
        return Response(InsightOut.from_model(insight).model_dump(mode="json"))

    def delete(self, request: Request, project_id: int, insight_id: int) -> Response:
        self.service(request, project_id).get_insight(insight_id).delete()
        return Response(status=204)


class InsightResultView(ProjectView):
    def get(self, request: Request, project_id: int, insight_id: int) -> Response:
        result = self.service(request, project_id).insight_result(insight_id)
        return Response(result.model_dump(mode="json"))


class DashboardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    insight_ids: list[int] = Field(default_factory=list, max_length=50)


def _dashboard_json(dashboard: Dashboard) -> dict[str, Any]:
    tiles = DashboardTile.objects.filter(dashboard=dashboard).select_related("insight")
    return {
        "id": dashboard.pk,
        "name": dashboard.name,
        "insights": [InsightOut.from_model(t.insight).model_dump(mode="json") for t in tiles],
    }


class DashboardListView(ProjectView):
    def get(self, request: Request, project_id: int) -> Response:
        project = project_for(request, project_id)
        return Response(
            [_dashboard_json(d) for d in Dashboard.objects.filter(project=project).order_by("pk")]
        )

    def post(self, request: Request, project_id: int) -> Response:
        project = project_for(request, project_id)
        body = _body(DashboardBody, request.data)
        insights = {i.pk: i for i in project.insights.filter(pk__in=body.insight_ids)}
        missing = [i for i in body.insight_ids if i not in insights]
        if missing:
            raise AnalyticsError("not_found", f"No insights {missing} in this project.")
        with transaction.atomic():
            dashboard = Dashboard.objects.create(project=project, name=body.name)
            DashboardTile.objects.bulk_create(
                DashboardTile(dashboard=dashboard, insight=insights[i], position=n)
                for n, i in enumerate(body.insight_ids)
            )
        return Response(_dashboard_json(dashboard), status=201)


class DashboardDetailView(ProjectView):
    def get(self, request: Request, project_id: int, dashboard_id: int) -> Response:
        project = project_for(request, project_id)
        dashboard = Dashboard.objects.filter(project=project, pk=dashboard_id).first()
        if dashboard is None:
            raise AnalyticsError("not_found", f"No dashboard {dashboard_id} in this project.")
        return Response(_dashboard_json(dashboard))
