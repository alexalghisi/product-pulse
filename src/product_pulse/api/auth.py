"""Personal API key authentication for the REST API (`Authorization: Bearer ppk_...`)."""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from product_pulse.core.models import PersonalAPIKey


class PersonalAPIKeyAuthentication(BaseAuthentication):
    keyword = "bearer"

    def authenticate(self, request: Request) -> tuple[Any, PersonalAPIKey] | None:
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != self.keyword or not token.startswith("ppk_"):
            return None
        key = PersonalAPIKey.verify(token.strip())
        if key is None:
            raise AuthenticationFailed("invalid personal API key")
        if key.last_used_at is None or (timezone.now() - key.last_used_at).total_seconds() > 60:
            PersonalAPIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key.user, key

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'
