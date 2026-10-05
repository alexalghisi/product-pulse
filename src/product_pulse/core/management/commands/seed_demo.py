import time
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser

from product_pulse.clickhouse.client import default_client
from product_pulse.core.models import Membership, PersonalAPIKey, Project
from product_pulse.ingestion.persons import PersonResolver
from product_pulse.ingestion.processor import EventProcessor
from product_pulse.seed.generator import Generator, SeedConfig


class Command(BaseCommand):
    help = "Create a demo project and fill it with synthetic product data (about 2M events)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", default="Demo")
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--visitors-per-day", type=int, default=1100)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--user", default="demo")
        parser.add_argument("--password", default="demo")

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(username=options["user"])
        user.set_password(options["password"])
        user.save()
        project = Project.objects.create(name=options["name"])
        Membership.objects.create(user=user, project=project)
        _, key = PersonalAPIKey.issue(user=user, project=project, label="seed")

        generator = Generator(
            project.pk,
            SeedConfig(
                days=options["days"],
                visitors_per_day=options["visitors_per_day"],
                seed=options["seed"],
            ),
        )
        processor = EventProcessor(PersonResolver(), default_client())
        total, generating, writing = 0, 0.0, 0.0
        clock = time.perf_counter()
        for day, events in enumerate(generator.days(), start=1):
            generated = time.perf_counter()
            generating += generated - clock
            processor.process(events)
            clock = time.perf_counter()
            writing += clock - generated
            total += len(events)
            if day % 10 == 0 or day == options["days"]:
                self.stdout.write(f"  day {day:>3}: {total:>9,} events")
        self.stdout.write(
            f"wrote {total:,} events in {writing:.1f}s through the writer "
            f"({total / writing:,.0f} events/s; generating took {generating:.1f}s)"
        )
        self.stdout.write(f"project id:    {project.pk}")
        self.stdout.write(f"project token: {project.api_token}")
        self.stdout.write(f"personal key:  {key}")
        self.stdout.write(f"login:         {options['user']} / {options['password']}")
