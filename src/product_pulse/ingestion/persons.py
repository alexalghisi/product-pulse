"""distinct_id -> person resolution, including merges on $identify.

Rules, kept deliberately small:

* Every new distinct_id gets its own anonymous person.
* `$identify` with `$anon_distinct_id` links the anonymous id to the identified one.
  If both already belong to different persons, the anonymous person is merged into
  the identified one: its distinct_ids move over with a bumped version.
* Two persons that have both been identified are never merged. That usually means a
  shared device, and joining two real users is worse than not linking a session.

Resolution for a project runs under a transaction-scoped advisory lock, so two
workers handling the same project cannot both create a person for one new id.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import connection, transaction

from product_pulse.core.models import Person, PersonDistinctId
from product_pulse.ingestion.events import ILLEGAL_DISTINCT_IDS, CapturedEvent

logger = logging.getLogger(__name__)

IDENTIFY = "$identify"
_LOCK_NAMESPACE = 7_301


@dataclass(frozen=True, slots=True)
class DistinctIdRow:
    project_id: int
    distinct_id: str
    person_id: str
    version: int

    def to_clickhouse(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "distinct_id": self.distinct_id,
            "person_id": self.person_id,
            "version": self.version,
        }


def _anon_id(event: CapturedEvent) -> str | None:
    if event.event != IDENTIFY:
        return None
    anon = event.properties.get("$anon_distinct_id")
    if not isinstance(anon, str):
        return None
    anon = anon.strip()
    if not anon or anon == event.distinct_id or anon.lower() in ILLEGAL_DISTINCT_IDS:
        return None
    return anon


class _ProjectResolution:
    def __init__(self, project_id: int) -> None:
        self.project_id = project_id
        self.known: dict[str, PersonDistinctId] = {}
        # One Person instance per row id, so a flag set while handling one event is seen
        # by every distinct_id of that person later in the batch.
        self._persons: dict[int, Person] = {}

    def _person(self, person: Person) -> Person:
        return self._persons.setdefault(person.pk, person)

    def load(self, distinct_ids: Iterable[str]) -> None:
        rows = PersonDistinctId.objects.select_related("person").filter(
            project_id=self.project_id, distinct_id__in=list(distinct_ids)
        )
        for row in rows:
            row.person = self._person(row.person)
            self.known[row.distinct_id] = row

    def create_anonymous(self, distinct_ids: Sequence[str]) -> None:
        new_ids = [d for d in dict.fromkeys(distinct_ids) if d not in self.known]
        if not new_ids:
            return
        persons = Person.objects.bulk_create([Person(project_id=self.project_id) for _ in new_ids])
        rows = PersonDistinctId.objects.bulk_create(
            [
                PersonDistinctId(project_id=self.project_id, distinct_id=d, person=p)
                for d, p in zip(new_ids, persons, strict=True)
            ]
        )
        for row in rows:
            row.person = self._person(row.person)
            self.known[row.distinct_id] = row

    def _attach(self, distinct_id: str, person: Person) -> None:
        row = PersonDistinctId.objects.create(
            project_id=self.project_id, distinct_id=distinct_id, person=self._person(person)
        )
        self.known[distinct_id] = row

    def _merge(self, source: Person, target: Person) -> None:
        moved = list(PersonDistinctId.objects.filter(person=source))
        for row in moved:
            row.person = target
            row.version += 1
        PersonDistinctId.objects.bulk_update(moved, ["person", "version"])
        source.delete()
        for row in moved:
            self.known[row.distinct_id] = row

    def identify(self, distinct_id: str, anon_id: str) -> None:
        identified = self.known.get(distinct_id)
        anonymous = self.known.get(anon_id)

        if identified is None and anonymous is None:
            person = Person.objects.create(project_id=self.project_id, is_identified=True)
            self._attach(distinct_id, person)
            self._attach(anon_id, person)
            return
        if identified is None:
            assert anonymous is not None
            if anonymous.person.is_identified:
                # The device already belongs to another user: this is a second user, not
                # the same one, so they get their own person.
                person = Person.objects.create(project_id=self.project_id, is_identified=True)
                self._attach(distinct_id, person)
                return
            self._attach(distinct_id, anonymous.person)
            target = anonymous.person
        elif anonymous is None:
            self._attach(anon_id, identified.person)
            target = identified.person
        elif identified.person_id == anonymous.person_id:
            target = identified.person
        elif not anonymous.person.is_identified:
            self._merge(anonymous.person, identified.person)
            target = identified.person
        elif not identified.person.is_identified:
            self._merge(identified.person, anonymous.person)
            target = anonymous.person
        else:
            logger.info(
                "not merging two identified persons (project %s, %r <- %r)",
                self.project_id,
                distinct_id,
                anon_id,
            )
            return
        if not target.is_identified:
            target.is_identified = True
            target.save(update_fields=["is_identified"])

    def rows(self) -> list[DistinctIdRow]:
        return [
            DistinctIdRow(
                project_id=self.project_id,
                distinct_id=row.distinct_id,
                person_id=str(row.person.uuid),
                version=row.version,
            )
            for row in self.known.values()
        ]


class PersonResolver:
    def resolve(self, events: Sequence[CapturedEvent]) -> list[DistinctIdRow]:
        """Make sure every distinct_id maps to a person and return the current mappings.

        The result covers every distinct_id in the batch, not only the ones that changed.
        Writing them all to ClickHouse each time costs a few small rows, and it means a
        batch that failed after Postgres committed still gets its mappings on retry.
        """
        by_project: dict[int, list[CapturedEvent]] = defaultdict(list)
        for event in events:
            by_project[event.project_id].append(event)
        mappings: list[DistinctIdRow] = []
        for project_id, project_events in sorted(by_project.items()):
            mappings.extend(self._resolve_project(project_id, project_events))
        return mappings

    def _resolve_project(self, project_id: int, events: list[CapturedEvent]) -> list[DistinctIdRow]:
        identifies = [(e.distinct_id, anon) for e in events if (anon := _anon_id(e))]
        involved = {d for pair in identifies for d in pair}
        plain = [e.distinct_id for e in events if e.distinct_id not in involved]

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)", [_LOCK_NAMESPACE, project_id]
                )
            resolution = _ProjectResolution(project_id)
            resolution.load({e.distinct_id for e in events} | involved)
            resolution.create_anonymous(plain)
            for distinct_id, anon_id in identifies:
                resolution.identify(distinct_id, anon_id)
            return resolution.rows()
