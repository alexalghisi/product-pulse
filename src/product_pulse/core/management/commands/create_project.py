import secrets
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser

from product_pulse.core.models import Membership, PersonalAPIKey, Project


class Command(BaseCommand):
    help = "Create a project, a user with access to it and a personal API key."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name")
        parser.add_argument("--user", default="admin")
        parser.add_argument("--password", default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=options["user"])
        if created or options["password"]:
            user.set_password(options["password"] or secrets.token_urlsafe(16))
            user.save()
        project = Project.objects.create(name=options["name"])
        Membership.objects.create(user=user, project=project)
        _, key = PersonalAPIKey.issue(user=user, project=project, label="cli")
        self.stdout.write(f"project id:      {project.pk}")
        self.stdout.write(f"project token:   {project.api_token}   (for /capture)")
        self.stdout.write(f"personal key:    {key}   (for the API and the MCP server)")
