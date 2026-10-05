from django.urls import path

from product_pulse.api import views

P = "projects/<int:project_id>"

urlpatterns = [
    path("auth/login", views.LoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/me", views.MeView.as_view()),
    path("schema/query", views.QuerySchemaView.as_view()),
    path("projects", views.ProjectListView.as_view()),
    path(f"{P}/events", views.EventListView.as_view()),
    path(f"{P}/properties", views.PropertyListView.as_view()),
    path(f"{P}/query", views.QueryView.as_view()),
    path(f"{P}/insights", views.InsightListView.as_view()),
    path(f"{P}/insights/<int:insight_id>", views.InsightDetailView.as_view()),
    path(f"{P}/insights/<int:insight_id>/result", views.InsightResultView.as_view()),
    path(f"{P}/dashboards", views.DashboardListView.as_view()),
    path(f"{P}/dashboards/<int:dashboard_id>", views.DashboardDetailView.as_view()),
]
