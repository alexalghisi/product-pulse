"""Relational state: projects, people with access to them, persons and saved work.

Events live in ClickHouse. Everything here is small, transactional and edited by
people (or agents acting for them), which is what Postgres is for.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from typing import Any, ClassVar

from django.conf import settings
from django.db import models


def _project_token() -> str:
    return "pp_" + secrets.token_urlsafe(24)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class Project(models.Model):
    name = models.CharField(max_length=200)
    # Public write-only token embedded in client SDKs. It can send events, nothing else.
    api_token = models.CharField(max_length=64, unique=True, default=_project_token)
    created_at = models.DateTimeField(auto_now_add=True)
    members: models.ManyToManyField[Any, Any] = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Membership", related_name="projects"
    )

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["user", "project"], name="uniq_membership")
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.project_id}"


class PersonalAPIKey(models.Model):
    """A read/write key for the API and the MCP server, bound to one user and one project.

    Binding the key to a project (rather than letting every call name a project) keeps
    agent tool calls short and means a leaked key exposes one project, not all of them.
    """

    PREFIX_LEN = 12

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    label = models.CharField(max_length=100)
    prefix = models.CharField(max_length=PREFIX_LEN, unique=True)
    secret_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.label} ({self.prefix}...)"

    @classmethod
    def issue(cls, *, user: Any, project: Project, label: str) -> tuple[PersonalAPIKey, str]:
        """Create a key and return it together with the plaintext, which is never stored."""
        plaintext = "ppk_" + secrets.token_urlsafe(32)
        key = cls.objects.create(
            user=user,
            project=project,
            label=label,
            prefix=plaintext[: cls.PREFIX_LEN],
            secret_hash=hash_secret(plaintext),
        )
        return key, plaintext

    @classmethod
    def verify(cls, plaintext: str) -> PersonalAPIKey | None:
        key = (
            cls.objects.select_related("user", "project")
            .filter(prefix=plaintext[: cls.PREFIX_LEN])
            .first()
        )
        if key is None or not hmac.compare_digest(key.secret_hash, hash_secret(plaintext)):
            return None
        return key


class Person(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True)
    # Set once the person has been the target of $identify. Two identified persons are
    # never merged: that would join two real users because one device was shared.
    is_identified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.uuid)


class PersonDistinctId(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    distinct_id = models.CharField(max_length=400)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="distinct_ids")
    # Bumped every time the id moves to another person. ClickHouse keeps the row with the
    # highest version, so queries see the latest mapping without a mutation.
    version = models.BigIntegerField(default=0)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["project", "distinct_id"], name="uniq_distinct_id")
        ]

    def __str__(self) -> str:
        return self.distinct_id


class Insight(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="insights")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    # A validated InsightQuery (see product_pulse.query.models), stored as JSON.
    query = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar = ["-updated_at"]

    def __str__(self) -> str:
        return self.name


class Dashboard(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="dashboards")
    name = models.CharField(max_length=200)
    insights: models.ManyToManyField[Any, Any] = models.ManyToManyField(
        Insight, through="DashboardTile", related_name="dashboards"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class DashboardTile(models.Model):
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE)
    insight = models.ForeignKey(Insight, on_delete=models.CASCADE)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar = ["position"]
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["dashboard", "insight"], name="uniq_tile")
        ]

    def __str__(self) -> str:
        return f"{self.dashboard_id}#{self.position}"
