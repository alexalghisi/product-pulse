from __future__ import annotations

import pytest

from product_pulse.core.models import Person, PersonDistinctId, Project
from product_pulse.ingestion.persons import DistinctIdRow, PersonResolver

from .helpers import identify, make_event

pytestmark = pytest.mark.django_db


def person_of(project: Project, distinct_id: str) -> Person:
    return PersonDistinctId.objects.get(project=project, distinct_id=distinct_id).person


def by_id(rows: list[DistinctIdRow]) -> dict[str, DistinctIdRow]:
    return {r.distinct_id: r for r in rows}


def test_each_new_distinct_id_gets_a_person_and_is_reused(project: Project) -> None:
    resolver = PersonResolver()
    first = by_id(
        resolver.resolve(
            [make_event("a", project_id=project.pk), make_event("b", project_id=project.pk)]
        )
    )
    again = by_id(resolver.resolve([make_event("a", project_id=project.pk)]))
    assert first["a"].person_id != first["b"].person_id
    assert again["a"] == first["a"]
    assert Person.objects.filter(project=project).count() == 2


def test_identify_links_anonymous_history_to_the_user(project: Project) -> None:
    resolver = PersonResolver()
    resolver.resolve([make_event("anon-1", project_id=project.pk)])
    rows = by_id(resolver.resolve([identify("user-1", "anon-1", project_id=project.pk)]))
    assert rows["user-1"].person_id == rows["anon-1"].person_id
    assert person_of(project, "user-1").is_identified


def test_identify_merges_two_anonymous_persons_and_bumps_versions(project: Project) -> None:
    resolver = PersonResolver()
    resolver.resolve(
        [make_event("anon-1", project_id=project.pk), make_event("user-1", project_id=project.pk)]
    )
    anon_person = person_of(project, "anon-1")
    rows = by_id(resolver.resolve([identify("user-1", "anon-1", project_id=project.pk)]))
    assert rows["anon-1"].person_id == rows["user-1"].person_id
    assert rows["anon-1"].version == 1  # moved, so ClickHouse prefers this row
    assert not Person.objects.filter(pk=anon_person.pk).exists()


def test_merge_carries_every_distinct_id_of_the_anonymous_person(project: Project) -> None:
    resolver = PersonResolver()
    p = project.pk
    resolver.resolve([identify("anon-2", "anon-1", project_id=p)])  # one anonymous person, two ids
    Person.objects.filter(pk=person_of(project, "anon-1").pk).update(is_identified=False)
    resolver.resolve([make_event("user-1", project_id=p)])
    rows = by_id(resolver.resolve([identify("user-1", "anon-1", project_id=p)]))
    target = person_of(project, "user-1")
    assert person_of(project, "anon-2") == target
    assert rows["anon-2"].person_id == str(target.uuid)  # not in the batch, still re-sent


def test_two_identified_users_are_never_merged(project: Project) -> None:
    resolver = PersonResolver()
    p = project.pk
    resolver.resolve(
        [identify("alice", "device-1", project_id=p), identify("bob", "device-2", project_id=p)]
    )
    resolver.resolve([identify("bob", "alice", project_id=p)])
    assert person_of(project, "alice") != person_of(project, "bob")


def test_second_user_on_a_shared_device_gets_their_own_person(project: Project) -> None:
    resolver = PersonResolver()
    p = project.pk
    resolver.resolve([identify("alice", "laptop", project_id=p)])
    resolver.resolve([identify("bob", "laptop", project_id=p)])
    assert person_of(project, "bob") != person_of(project, "alice")
    assert person_of(project, "laptop") == person_of(project, "alice")


def test_identify_and_events_in_one_batch_resolve_in_order(project: Project) -> None:
    p = project.pk
    rows = by_id(
        PersonResolver().resolve(
            [
                make_event("anon-9", project_id=p),
                make_event("user-9", "signed_up", project_id=p),
                identify("user-9", "anon-9", project_id=p),
                make_event("user-9", "project_created", project_id=p),
            ]
        )
    )
    assert rows["anon-9"].person_id == rows["user-9"].person_id
    assert Person.objects.filter(project=project).count() == 1


def test_projects_never_share_persons(project: Project, other_project: Project) -> None:
    rows = PersonResolver().resolve(
        [
            make_event("same-id", project_id=project.pk),
            make_event("same-id", project_id=other_project.pk),
        ]
    )
    assert len({r.person_id for r in rows}) == 2
    assert {r.project_id for r in rows} == {project.pk, other_project.pk}
