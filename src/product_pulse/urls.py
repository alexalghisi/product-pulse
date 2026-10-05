from django.urls import include, path

from product_pulse.ingestion.views import CaptureView

urlpatterns = [
    path("capture", CaptureView.as_view(), name="capture"),
    path("api/", include("product_pulse.api.urls")),
]
