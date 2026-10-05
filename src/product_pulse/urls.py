from django.urls import path

from product_pulse.ingestion.views import CaptureView

urlpatterns = [
    path("capture", CaptureView.as_view(), name="capture"),
]
