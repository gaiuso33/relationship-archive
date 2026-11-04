"""The owner-scoped Notes API (issue #13), against real PostgreSQL.

Two synthetic users, A and B, sign in with synthetic tokens that are verified for real (a fake
key provider supplies the public key: no network, no Supabase). Every test runs in a
transaction that is rolled back. The point of the suite is that one user can never read, add,
change or delete a note on another user's person, and that note text never leaves the API
except in a successful response to its owner.
"""

import inspect
import io
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg.errors
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import get_session
from app.main import API_V1_PREFIX, create_app
from app.models.note import Note
from app.services import notes as notes_service
from tests import auth_support as a

PEOPLE = f"{API_V1_PREFIX}/people"
USER_A = a.USER_ID
USER_B = "99999999-8888-4777-8666-555555555555"
SECRET_NOTE = "SyntheticNoteTextNeverLogThis-7391"
SECRET_NAME = "SyntheticPersonNameNeverLogThis"


def notes_url(person_id: object) -> str:
    return f"{PEOPLE}/{person_id}/notes"


def note_url(person_id: object, note_id: object) -> str:
    return f"{PEOPLE}/{person_id}/notes/{note_id}"


@pytest.fixture
def keys() -> a.FakeKeys:
    return a.FakeKeys({a.KID: a.EC_KEY.public_key()})


@pytest.fixture
def session(migrated_engine: Engine, db_session: Session) -> Session:
    return db_session


@pytest.fixture
def app(
    log_format: str, log_stream: io.StringIO, keys: a.FakeKeys, session: Session
) -> Iterator[FastAPI]:
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    application = create_app(settings, keys)
    configure_logging(settings, log_stream)
    application.dependency_overrides[get_session] = lambda: session
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {a.make_token(a.valid_claims(sub=user_id))}"}


@pytest.fixture
def as_a() -> dict[str, str]:
    return auth(USER_A)


@pytest.fixture
def as_b() -> dict[str, str]:
    return auth(USER_B)


def make_person(client: TestClient, headers: dict[str, str], name: str = "Synthetic Person") -> str:
    response = client.post(PEOPLE, json={"display_name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_note(
    client: TestClient, headers: dict[str, str], person_id: str, content: str = "A synthetic note"
) -> dict[str, Any]:
    response = client.post(notes_url(person_id), json={"content": content}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert response.headers["X-Request-ID"] == error["request_id"]
    return error


def assert_not_found(response) -> None:
    error = assert_error(response, 404, "not_found")
    assert error["message"] == "The requested resource was not found."
    assert set(error) == {"code", "message", "request_id"}


def note_rows(session: Session) -> list[tuple[str, str, str, str]]:
    """Every note in the database, read with raw SQL (never from the session's cache)."""
    rows = session.execute(
        text("select id, owner_id, person_id, content from notes order by id")
    ).all()
    return [(str(r[0]), str(r[1]), str(r[2]), r[3]) for r in rows]


def note_count(session: Session) -> int:
    return session.execute(text("select count(*) from notes")).scalar_one()


# --- routes and authentication ----------------------------------------------------------------


def notes_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) for notes, read from the public OpenAPI schema."""
    return sorted(
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.endswith("/notes") or "/notes/" in path
        for method in operations
    )


def test_exactly_the_approved_routes_exist(app: FastAPI) -> None:
    base = f"{PEOPLE}/{{person_id}}/notes"
    assert notes_routes(app) == [
        ("DELETE", f"{base}/{{note_id}}"),
        ("GET", base),
        ("PATCH", f"{base}/{{note_id}}"),
        ("POST", base),
    ]


def test_every_notes_route_rejects_unauthenticated_requests(
    app: FastAPI, client: TestClient
) -> None:
    """Enumerates the routes, so one added later without authentication fails here."""
    opened: list[str] = []

    def must_not_open_a_session() -> Iterator[Session]:
        opened.append("opened")
        raise AssertionError("a session was opened for an unauthenticated request")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = must_not_open_a_session
    routes = notes_routes(app)
    assert routes
    for method, path in routes:
        url = path.replace("{person_id}", str(uuid.uuid4())).replace("{note_id}", str(uuid.uuid4()))
        response = client.request(method, url, json={"content": SECRET_NOTE})
        error = assert_error(response, 401, "unauthorized")
        assert error["message"] == "Authentication is required."
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert SECRET_NOTE not in response.text
    assert opened == []


def expired_header() -> dict[str, str]:
    token = a.make_token(a.valid_claims(exp=int(time.time()) - 3600))
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "make_headers",
    [
        pytest.param(lambda: {"Authorization": "Bearer not-a-token"}, id="malformed"),
        pytest.param(lambda: {"Authorization": "Basic abc"}, id="wrong-scheme"),
        pytest.param(expired_header, id="expired"),
        pytest.param(
            lambda: {"Authorization": "Bearer " + a.make_token(key=a.OTHER_EC_KEY)},
            id="invalid-signature",
        ),
        pytest.param(
            lambda: {"Authorization": "Bearer " + a.make_token(a.valid_claims(aud="other"))},
            id="wrong-audience",
        ),
    ],
)
def test_bad_tokens_are_rejected_on_every_notes_method(
    client: TestClient, make_headers: Callable[[], dict[str, str]]
) -> None:
    person, note = uuid.uuid4(), uuid.uuid4()
    calls = [
        ("GET", notes_url(person)),
        ("POST", notes_url(person)),
        ("PATCH", note_url(person, note)),
        ("DELETE", note_url(person, note)),
    ]
    for method, url in calls:
        response = client.request(method, url, json={"content": "x"}, headers=make_headers())
        assert_error(response, 401, "unauthorized")


def test_notes_api_does_not_fail_open_when_signing_keys_are_unavailable(
    log_format: str, log_stream: io.StringIO, session: Session
) -> None:
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    application = create_app(settings, a.FakeKeys(unavailable=True))
    configure_logging(settings, log_stream)
    application.dependency_overrides[get_session] = lambda: session
    client = TestClient(application, raise_server_exceptions=False)

    for method, url in [("GET", notes_url(uuid.uuid4())), ("POST", notes_url(uuid.uuid4()))]:
        response = client.request(method, url, json={"content": "x"}, headers=auth(USER_A))
        assert_error(response, 503, "service_unavailable")


def test_notes_api_answers_503_when_supabase_is_not_configured(session: Session) -> None:
    application = create_app(Settings(_env_file=None, app_env="test"))
    application.dependency_overrides[get_session] = lambda: session
    client = TestClient(application, raise_server_exceptions=False)

    response = client.get(notes_url(uuid.uuid4()), headers=auth(USER_A))

    assert_error(response, 503, "service_unavailable")


def test_malformed_json_without_a_token_is_a_validation_error_as_documented(
    client: TestClient,
) -> None:
    """FastAPI parses the raw body before authentication runs (docs/notes-api.md)."""
    response = client.post(
        notes_url(uuid.uuid4()), content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert_error(response, 422, "validation_error")


def test_valid_json_without_a_token_is_unauthorized_before_field_validation(
    client: TestClient,
) -> None:
    assert_error(client.post(notes_url(uuid.uuid4()), json={"nope": 1}), 401, "unauthorized")


# --- create, list, edit, delete ---------------------------------------------------------------


def test_create_returns_the_note_without_an_owner(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)

    response = client.post(notes_url(person), json={"content": "Likes the sea."}, headers=as_a)

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {"id", "person_id", "content", "created_at", "updated_at"}
    uuid.UUID(body["id"])
    assert body["person_id"] == person
    assert body["content"] == "Likes the sea."
    assert body["created_at"] == body["updated_at"]
    assert USER_A not in response.text


def test_a_new_note_always_has_equal_creation_and_update_times(
    client: TestClient, as_a: dict
) -> None:
    """Clients tell an edited note by updated_at > created_at, so a new one must not differ."""
    person = make_person(client, as_a)

    for _ in range(40):
        created = make_note(client, as_a, person)
        assert created["created_at"] == created["updated_at"]


def test_the_owner_is_taken_from_the_verified_token(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    person_a, person_b = make_person(client, as_a), make_person(client, as_b)
    note_a, note_b = make_note(client, as_a, person_a), make_note(client, as_b, person_b)

    owners = {row[0]: row[1] for row in note_rows(session)}
    assert owners[note_a["id"]] == USER_A
    assert owners[note_b["id"]] == USER_B


def test_content_is_stored_exactly_as_sent(
    client: TestClient, session: Session, as_a: dict
) -> None:
    person = make_person(client, as_a)
    content = "  Line one\r\n\tindented line two\n\nafter a blank line — café 😀  \n"

    created = make_note(client, as_a, person, content)

    assert created["content"] == content
    assert note_rows(session)[0][3] == content
    listed = client.get(notes_url(person), headers=as_a).json()["items"]
    assert listed[0]["content"] == content


def test_list_returns_the_notes_of_one_person_newest_first(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)
    first = make_note(client, as_a, person, "first")
    second = make_note(client, as_a, person, "second")
    third = make_note(client, as_a, person, "third")

    response = client.get(notes_url(person), headers=as_a)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert list(response.json()) == ["items"]
    assert [n["id"] for n in response.json()["items"]] == [third["id"], second["id"], first["id"]]
    assert set(response.json()["items"][0]) == {
        "id",
        "person_id",
        "content",
        "created_at",
        "updated_at",
    }


def test_list_is_empty_for_a_person_with_no_notes(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)

    response = client.get(notes_url(person), headers=as_a)

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_edit_replaces_the_text_and_moves_only_updated_at(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)
    created = make_note(client, as_a, person, "Before")
    time.sleep(0.01)

    response = client.patch(
        note_url(person, created["id"]), json={"content": "After\nedit"}, headers=as_a
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    edited = response.json()
    assert edited["content"] == "After\nedit"
    assert edited["id"] == created["id"]
    assert edited["person_id"] == person
    assert edited["created_at"] == created["created_at"]
    assert edited["updated_at"] > created["updated_at"]
    assert client.get(notes_url(person), headers=as_a).json()["items"] == [edited]


def test_delete_removes_the_note_and_nothing_else(
    client: TestClient, session: Session, as_a: dict
) -> None:
    person = make_person(client, as_a)
    keep = make_note(client, as_a, person, "keep")
    gone = make_note(client, as_a, person, "gone")

    response = client.delete(note_url(person, gone["id"]), headers=as_a)

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert [n["id"] for n in client.get(notes_url(person), headers=as_a).json()["items"]] == [
        keep["id"]
    ]
    assert note_count(session) == 1
    assert_not_found(client.delete(note_url(person, gone["id"]), headers=as_a))
    assert_not_found(
        client.patch(note_url(person, gone["id"]), json={"content": "x"}, headers=as_a)
    )


# --- ownership (ADR-002 requirement 6) --------------------------------------------------------


def request_on(client: TestClient, method: str, person: str, note: str, headers: dict):
    if method == "LIST":
        return client.get(notes_url(person), headers=headers)
    if method == "POST":
        return client.post(notes_url(person), json={"content": "Intruder note"}, headers=headers)
    if method == "PATCH":
        return client.patch(note_url(person, note), json={"content": "Taken over"}, headers=headers)
    return client.delete(note_url(person, note), headers=headers)


@pytest.mark.parametrize("method", ["LIST", "POST", "PATCH", "DELETE"])
def test_user_a_cannot_reach_notes_on_user_bs_person(
    client: TestClient, session: Session, as_a: dict, as_b: dict, method: str
) -> None:
    person_b = make_person(client, as_b)
    note_b = make_note(client, as_b, person_b, "B's private note")
    before = note_rows(session)
    missing_person, missing_note = str(uuid.uuid4()), str(uuid.uuid4())

    foreign = request_on(client, method, person_b, note_b["id"], as_a)
    absent = request_on(client, method, missing_person, missing_note, as_a)

    # Another user's person looks exactly like one that does not exist.
    assert_not_found(foreign)
    assert_not_found(absent)
    foreign_error, absent_error = foreign.json()["error"], absent.json()["error"]
    foreign_error.pop("request_id"), absent_error.pop("request_id")
    assert foreign_error == absent_error
    assert "B's private note" not in foreign.text and USER_B not in foreign.text
    # B's records are untouched: nothing added, changed or removed.
    assert note_rows(session) == before
    assert client.get(notes_url(person_b), headers=as_b).json()["items"] == [note_b]


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_user_a_cannot_use_user_bs_note_id_under_a_person_of_their_own(
    client: TestClient, session: Session, as_a: dict, as_b: dict, method: str
) -> None:
    person_a, person_b = make_person(client, as_a), make_person(client, as_b)
    note_b = make_note(client, as_b, person_b, "B's private note")
    before = note_rows(session)

    response = request_on(client, method, person_a, note_b["id"], as_a)

    assert_not_found(response)
    assert note_rows(session) == before


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_a_note_id_under_a_different_person_of_the_same_owner_is_not_found(
    client: TestClient, session: Session, as_a: dict, method: str
) -> None:
    person_one, person_two = make_person(client, as_a, "One"), make_person(client, as_a, "Two")
    note = make_note(client, as_a, person_one, "Belongs to person one")
    before = note_rows(session)

    wrong_person = request_on(client, method, person_two, note["id"], as_a)

    assert_not_found(wrong_person)
    assert note_rows(session) == before
    # The right person still works.
    assert request_on(client, method, person_one, note["id"], as_a).status_code in {200, 204}


def test_lists_never_mix_owners_or_people(client: TestClient, as_a: dict, as_b: dict) -> None:
    person_one, person_two = make_person(client, as_a, "One"), make_person(client, as_a, "Two")
    person_b = make_person(client, as_b)
    mine = make_note(client, as_a, person_one, "A one")
    make_note(client, as_a, person_two, "A two")
    make_note(client, as_b, person_b, "B only")

    listed = client.get(notes_url(person_one), headers=as_a, params={"limit": 100}).json()["items"]

    assert [n["id"] for n in listed] == [mine["id"]]
    assert "B only" not in client.get(notes_url(person_one), headers=as_a).text


def test_list_filters_by_owner_even_if_the_database_key_were_missing(
    client: TestClient, session: Session, as_a: dict
) -> None:
    """Defence in depth: the query's own owner filter is tested apart from the foreign key.

    With the foreign key removed (inside this test's rolled-back transaction), a note that
    names A's person but another owner must still not be listed to A.
    """
    person_a = make_person(client, as_a)
    mine = make_note(client, as_a, person_a, "Mine")
    session.execute(text("alter table notes drop constraint fk_notes_person_id_owner_id_people"))
    session.execute(
        text(
            "insert into notes (id, owner_id, person_id, content) "
            "values (gen_random_uuid(), :o, :p, 'Planted by someone else')"
        ),
        {"o": USER_B, "p": person_a},
    )
    # Precondition: the planted row really is in the table, so the list below has something
    # it must leave out. Without it this test could pass for an unrelated reason.
    assert sorted((row[1], row[3]) for row in note_rows(session)) == [
        (USER_A, "Mine"),
        (USER_B, "Planted by someone else"),
    ]

    listed = client.get(notes_url(person_a), headers=as_a).json()["items"]

    assert [n["id"] for n in listed] == [mine["id"]]


@pytest.mark.parametrize("field", ["owner_id", "person_id", "user_id", "id", "created_at"])
@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_a_client_supplied_owner_or_person_in_the_body_is_rejected(
    client: TestClient, session: Session, as_a: dict, as_b: dict, method: str, field: str
) -> None:
    person_a, person_b = make_person(client, as_a), make_person(client, as_b)
    note = make_note(client, as_a, person_a, "Mine")
    before = note_rows(session)
    body = {"content": "Mallory", field: person_b if field == "person_id" else USER_B}

    if method == "POST":
        response = client.post(notes_url(person_a), json=body, headers=as_a)
    else:
        response = client.patch(note_url(person_a, note["id"]), json=body, headers=as_a)

    error = assert_error(response, 422, "validation_error")
    assert error["fields"] == [{"loc": ["body", "<unknown field>"], "type": "extra_forbidden"}]
    assert USER_B not in response.text and person_b not in response.text
    assert note_rows(session) == before


def test_owner_hints_in_query_and_headers_are_ignored(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    person_a, person_b = make_person(client, as_a), make_person(client, as_b)
    note_b = make_note(client, as_b, person_b, "B's private note")
    hints = {"X-User-Id": USER_B, "X-Owner-Id": USER_B}
    params = {"owner_id": USER_B, "person_id": person_b}

    listed = client.get(notes_url(person_b), params=params, headers={**as_a, **hints})
    created = client.post(
        notes_url(person_a), params=params, json={"content": "Hinted"}, headers={**as_a, **hints}
    )
    edited = client.patch(
        note_url(person_b, note_b["id"]),
        params=params,
        json={"content": "Taken"},
        headers={**as_a, **hints},
    )

    assert_not_found(listed)
    assert_not_found(edited)
    assert created.status_code == 201
    stored = {row[3]: (row[1], row[2]) for row in note_rows(session)}
    # Created for A, on A's person (the token and the URL), whatever the hints said.
    assert stored["Hinted"] == (USER_A, person_a)
    assert stored["B's private note"] == (USER_B, person_b)


def test_the_database_itself_refuses_a_note_whose_owner_differs_from_its_person(
    client: TestClient, session: Session, as_a: dict
) -> None:
    """The composite foreign key holds even if the application code were bypassed."""
    person_a = make_person(client, as_a)

    with pytest.raises(IntegrityError) as caught:
        session.add(Note(owner_id=uuid.UUID(USER_B), person_id=uuid.UUID(person_a), content="x"))
        session.flush()

    assert getattr(caught.value.orig, "sqlstate", None) == "23503"
    session.rollback()


def test_the_service_layer_requires_the_owner_and_the_person_everywhere() -> None:
    """A guard: no data-access function can be written without saying whose data it is."""
    functions = [
        member
        for name, member in inspect.getmembers(notes_service, inspect.isfunction)
        if member.__module__ == notes_service.__name__ and not name.startswith("_")
    ]
    assert {f.__name__ for f in functions} == {
        "list_notes",
        "create_note",
        "update_note",
        "delete_note",
    }
    for function in functions:
        parameters = list(inspect.signature(function).parameters)
        assert parameters[:3] == ["session", "owner_id", "person_id"], function.__name__


def test_the_service_layer_scopes_every_operation_to_the_owner_and_person(
    client: TestClient, session: Session, as_a: dict
) -> None:
    person_one, person_two = make_person(client, as_a, "One"), make_person(client, as_a, "Two")
    note = make_note(client, as_a, person_one, "Mine")
    owner, other = uuid.UUID(USER_A), uuid.uuid4()
    one, two, note_id = uuid.UUID(person_one), uuid.UUID(person_two), uuid.UUID(note["id"])

    assert notes_service.list_notes(session, other, one, limit=20, offset=0) is None
    assert notes_service.create_note(session, other, one, "x") is None
    assert notes_service.update_note(session, other, one, note_id, "x") is None
    assert notes_service.delete_note(session, other, one, note_id) is False
    assert notes_service.update_note(session, owner, two, note_id, "x") is None
    assert notes_service.delete_note(session, owner, two, note_id) is False
    assert [r[3] for r in note_rows(session)] == ["Mine"]


def test_creating_a_note_for_a_person_that_does_not_exist_stores_nothing(
    client: TestClient, session: Session, as_a: dict
) -> None:
    assert_not_found(client.post(notes_url(uuid.uuid4()), json={"content": "Orphan"}, headers=as_a))
    assert note_count(session) == 0


# --- deleting a person (cascade) --------------------------------------------------------------


def test_deleting_a_person_removes_only_that_persons_notes(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    doomed, kept = make_person(client, as_a, "Doomed"), make_person(client, as_a, "Kept")
    person_b = make_person(client, as_b)
    for text_ in ("d1", "d2"):
        make_note(client, as_a, doomed, text_)
    survivor = make_note(client, as_a, kept, "k1")
    strangers = make_note(client, as_b, person_b, "b1")

    response = client.delete(f"{PEOPLE}/{doomed}", headers=as_a)

    assert response.status_code == 204
    assert sorted(row[3] for row in note_rows(session)) == ["b1", "k1"]
    assert_not_found(client.get(notes_url(doomed), headers=as_a))
    assert client.get(notes_url(kept), headers=as_a).json()["items"] == [survivor]
    assert client.get(notes_url(person_b), headers=as_b).json()["items"] == [strangers]


def test_a_failed_cross_user_person_delete_keeps_the_notes(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    person_b = make_person(client, as_b)
    make_note(client, as_b, person_b, "B's private note")

    assert_not_found(client.delete(f"{PEOPLE}/{person_b}", headers=as_a))

    assert note_count(session) == 1


# --- a person deleted while a note is being added -------------------------------------------------
#
# `create_note` tests that the person is the caller's and inserts in one statement. If a
# concurrent request deletes the person after that test but before the foreign key is checked,
# PostgreSQL raises a foreign-key violation (SQLSTATE 23503). The service must treat it as "the
# person does not exist" (None, so 404) and roll the session back. These tests make that race
# deterministic without sleeping: one connection deletes the person but does not commit, the
# insert is started on another and blocks on the foreign-key lock, and the test commits the
# delete only once PostgreSQL itself reports the insert waiting for that lock.


@pytest.fixture
def committed_person(migrated_engine: Engine) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """A person that other connections can see (committed), removed again afterwards."""
    owner, person = uuid.uuid4(), uuid.uuid4()
    with migrated_engine.begin() as connection:
        connection.execute(
            text("insert into people (id, owner_id, display_name) values (:p, :o, 'Synthetic')"),
            {"p": person, "o": owner},
        )
    try:
        yield owner, person
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(text("delete from people where owner_id = :o"), {"o": owner})


def wait_until_an_insert_is_blocked_on_a_lock(engine: Engine, timeout: float = 10.0) -> None:
    """Return once PostgreSQL shows a notes INSERT waiting on a lock (a condition, not a delay)."""
    deadline = time.monotonic() + timeout
    # Autocommit matters: inside one transaction PostgreSQL keeps showing the same snapshot of
    # pg_stat_activity, so a poll that began before the insert would never see it.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            waiting = connection.execute(
                text(
                    "select count(*) from pg_stat_activity "
                    "where datname = current_database() and pid <> pg_backend_pid() "
                    "and wait_event_type = 'Lock' and query like 'INSERT INTO notes%'"
                )
            ).scalar_one()
            if waiting:
                return
            time.sleep(0.01)
    raise AssertionError("the note insert never blocked on the in-flight person delete")


def while_the_person_is_being_deleted(
    engine: Engine, person_id: uuid.UUID, action: Callable[[], Any]
) -> Any:
    """Run `action` (which adds a note) while a delete of the person is in flight, then commit it.

    The delete holds the person's row lock without committing. `action` runs on a thread, passes
    its ownership test (the person is still visible) and blocks on the foreign-key check. Only then
    does the delete commit, so the check fails exactly as in the real race.
    """
    deleter = engine.connect()
    transaction = deleter.begin()
    deleter.execute(text("delete from people where id = :p"), {"p": person_id})
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["value"] = action()
        except BaseException as error:  # reported to the test thread below
            outcome["error"] = error

    thread = threading.Thread(target=work)
    thread.start()
    try:
        wait_until_an_insert_is_blocked_on_a_lock(engine)
    finally:
        transaction.commit()
        deleter.close()
        thread.join(timeout=20)
    assert not thread.is_alive(), "the insert did not finish after the delete committed"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def notes_for(engine: Engine, person_id: uuid.UUID) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("select count(*) from notes where person_id = :p"), {"p": person_id}
        ).scalar_one()


def test_a_person_deleted_during_create_is_handled_as_not_found_by_the_service(
    migrated_engine: Engine, committed_person: tuple[uuid.UUID, uuid.UUID]
) -> None:
    owner, person = committed_person
    with Session(migrated_engine, expire_on_commit=False) as session:

        def add_note() -> object:
            return notes_service.create_note(session, owner, person, SECRET_NOTE)

        result = while_the_person_is_being_deleted(migrated_engine, person, add_note)

        # The foreign-key violation was absorbed, not raised, and the answer is "not found".
        assert result is None
        # The session was rolled back: no failed transaction is left, and it is usable again.
        assert not session.in_transaction()
        assert session.execute(text("select 1")).scalar_one() == 1
    assert notes_for(migrated_engine, person) == 0


def test_a_person_deleted_during_create_is_a_404_through_the_api(
    log_format: str,
    log_stream: io.StringIO,
    keys: a.FakeKeys,
    migrated_engine: Engine,
    committed_person: tuple[uuid.UUID, uuid.UUID],
) -> None:
    owner, person = committed_person
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    application = create_app(settings, keys)
    configure_logging(settings, log_stream)

    def request_scoped_session() -> Iterator[Session]:
        # As in production: a new session for every request.
        with Session(migrated_engine, expire_on_commit=False) as session:
            yield session

    application.dependency_overrides[get_session] = request_scoped_session
    client = TestClient(application, raise_server_exceptions=False)
    headers = auth(str(owner))

    def add_note():
        return client.post(notes_url(person), json={"content": SECRET_NOTE}, headers=headers)

    raced = while_the_person_is_being_deleted(migrated_engine, person, add_note)
    absent = client.post(notes_url(uuid.uuid4()), json={"content": SECRET_NOTE}, headers=headers)

    assert_not_found(raced)
    assert_not_found(absent)
    raced_error, absent_error = raced.json()["error"], absent.json()["error"]
    raced_error.pop("request_id"), absent_error.pop("request_id")
    assert raced_error == absent_error  # the same 404 as a person that never existed
    assert notes_for(migrated_engine, person) == 0
    assert SECRET_NOTE not in raced.text
    assert SECRET_NOTE not in log_stream.getvalue()


def integrity_error(violation: type[psycopg.errors.IntegrityError]) -> IntegrityError:
    return IntegrityError("INSERT INTO notes ...", {}, violation())


def test_only_a_foreign_key_violation_is_treated_as_not_found(
    client: TestClient, session: Session, as_a: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control for the tests below: the handled error, with the rollback observed."""
    person = uuid.UUID(make_person(client, as_a))
    rolled_back: list[bool] = []
    real_rollback = session.rollback

    def failing_insert(*args: object, **kwargs: object) -> object:
        raise integrity_error(psycopg.errors.ForeignKeyViolation)

    def spy_rollback() -> None:
        rolled_back.append(True)
        real_rollback()

    monkeypatch.setattr(session, "scalars", failing_insert)
    monkeypatch.setattr(session, "rollback", spy_rollback)

    assert notes_service.create_note(session, uuid.UUID(USER_A), person, "x") is None
    assert rolled_back == [True]


@pytest.mark.parametrize(
    "violation",
    [
        psycopg.errors.UniqueViolation,
        psycopg.errors.CheckViolation,
        psycopg.errors.NotNullViolation,
    ],
)
def test_other_integrity_errors_are_not_swallowed_by_create(
    client: TestClient,
    session: Session,
    as_a: dict,
    monkeypatch: pytest.MonkeyPatch,
    violation: type[psycopg.errors.IntegrityError],
) -> None:
    person = uuid.UUID(make_person(client, as_a))

    def failing_insert(*args: object, **kwargs: object) -> object:
        raise integrity_error(violation)

    monkeypatch.setattr(session, "scalars", failing_insert)

    with pytest.raises(IntegrityError) as caught:
        notes_service.create_note(session, uuid.UUID(USER_A), person, "x")

    assert getattr(caught.value.orig, "sqlstate", None) == violation.sqlstate


# --- validation -------------------------------------------------------------------------------


INVALID_CONTENT = [
    pytest.param({}, id="missing"),
    pytest.param({"content": None}, id="null"),
    pytest.param({"content": ""}, id="empty"),
    pytest.param({"content": " "}, id="space"),
    pytest.param({"content": " \t \r\n \n "}, id="whitespace-only"),
    pytest.param({"content": "  "}, id="unicode-whitespace-only"),
    pytest.param({"content": "n" * 10_001}, id="one-over-the-limit"),
    pytest.param({"content": "😀" * 10_001}, id="one-over-the-limit-astral"),
    pytest.param({"content": "\x00"}, id="nul-only"),
    pytest.param({"content": "before\x00after"}, id="nul-inside"),
    pytest.param({"content": "bell\x07"}, id="bell"),
    pytest.param({"content": "esc\x1b[31m"}, id="escape"),
    pytest.param({"content": "del\x7f"}, id="delete-character"),
    pytest.param({"content": "form\x0cfeed"}, id="form-feed"),
    pytest.param({"content": "vertical\x0btab"}, id="vertical-tab"),
    pytest.param({"content": "next\x85line"}, id="c1-control"),
    pytest.param({"content": 123}, id="number"),
    pytest.param({"content": True}, id="boolean"),
    pytest.param({"content": ["a"]}, id="list"),
    pytest.param({"content": {"text": "a"}}, id="object"),
    pytest.param({"content": "ok", "extra": "x"}, id="unknown-field"),
]


@pytest.mark.parametrize("payload", INVALID_CONTENT)
def test_invalid_content_is_a_validation_error_and_stores_nothing(
    client: TestClient, session: Session, as_a: dict, payload: dict
) -> None:
    person = make_person(client, as_a)

    response = client.post(notes_url(person), json=payload, headers=as_a)

    error = assert_error(response, 422, "validation_error")
    assert error["message"] == "Some fields are missing or invalid."
    assert note_count(session) == 0


@pytest.mark.parametrize("payload", INVALID_CONTENT)
def test_invalid_edit_is_rejected_and_changes_nothing(
    client: TestClient, session: Session, as_a: dict, payload: dict
) -> None:
    person = make_person(client, as_a)
    note = make_note(client, as_a, person, "Keep me")
    before = note_rows(session)

    response = client.patch(note_url(person, note["id"]), json=payload, headers=as_a)

    assert_error(response, 422, "validation_error")
    assert note_rows(session) == before


def test_a_lone_surrogate_is_a_validation_error_not_a_server_error(
    client: TestClient, session: Session, as_a: dict
) -> None:
    person = make_person(client, as_a)

    for body in (b'{"content": "broken \\ud800 text"}', b'{"content": "\\udc00"}'):
        response = client.post(
            notes_url(person),
            content=body,
            headers={**as_a, "Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
    assert note_count(session) == 0


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_a_body_that_is_not_json_is_a_validation_error(
    client: TestClient, as_a: dict, method: str
) -> None:
    person = make_person(client, as_a)
    note = make_note(client, as_a, person)
    url = notes_url(person) if method == "POST" else note_url(person, note["id"])

    response = client.request(
        method, url, content=b"{not json", headers={**as_a, "Content-Type": "application/json"}
    )

    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize("length", [1, 9_999, 10_000])
def test_content_up_to_the_limit_is_accepted(client: TestClient, as_a: dict, length: int) -> None:
    person = make_person(client, as_a)

    created = make_note(client, as_a, person, "n" * length)

    assert len(created["content"]) == length


def test_the_limit_counts_characters_not_bytes(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)

    created = make_note(client, as_a, person, "😀" * 10_000)

    assert len(created["content"]) == 10_000


def test_newlines_and_tabs_are_kept(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)
    content = "a\nb\r\nc\td\n"

    assert make_note(client, as_a, person, content)["content"] == content


def test_edit_accepts_the_same_limits(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)
    note = make_note(client, as_a, person, "start")

    response = client.patch(
        note_url(person, note["id"]), json={"content": " " + "n" * 9_998 + " "}, headers=as_a
    )

    assert response.status_code == 200
    assert len(response.json()["content"]) == 10_000


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "00000000-0000-0000-0000-00000000000g"])
def test_a_malformed_identifier_is_a_validation_error(
    client: TestClient, as_a: dict, bad_id: str
) -> None:
    person = make_person(client, as_a)
    note = make_note(client, as_a, person)
    responses = [
        (client.get(notes_url(bad_id), headers=as_a), ["path", "person_id"]),
        (
            client.post(notes_url(bad_id), json={"content": "x"}, headers=as_a),
            ["path", "person_id"],
        ),
        (
            client.patch(note_url(bad_id, note["id"]), json={"content": "x"}, headers=as_a),
            ["path", "person_id"],
        ),
        (
            client.patch(note_url(person, bad_id), json={"content": "x"}, headers=as_a),
            ["path", "note_id"],
        ),
        (client.delete(note_url(person, bad_id), headers=as_a), ["path", "note_id"]),
    ]
    for response, loc in responses:
        error = assert_error(response, 422, "validation_error")
        assert error["fields"][0]["loc"] == loc
        assert bad_id not in response.text


def test_missing_people_and_notes_are_not_found(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)
    absent_person, absent_note = uuid.uuid4(), uuid.uuid4()

    assert_not_found(client.get(notes_url(absent_person), headers=as_a))
    assert_not_found(client.post(notes_url(absent_person), json={"content": "x"}, headers=as_a))
    assert_not_found(
        client.patch(note_url(absent_person, absent_note), json={"content": "x"}, headers=as_a)
    )
    assert_not_found(
        client.patch(note_url(person, absent_note), json={"content": "x"}, headers=as_a)
    )
    assert_not_found(client.delete(note_url(person, absent_note), headers=as_a))


# --- pagination and ordering ------------------------------------------------------------------


def seed_notes(session: Session, owner: str, person: str, count: int, prefix: str = "n") -> None:
    """Insert notes quickly, straight into the database, with increasing creation times."""
    session.execute(
        text(
            "insert into notes (id, owner_id, person_id, content, created_at, updated_at) "
            "select gen_random_uuid(), :o, :p, :prefix || g, "
            "timestamptz '2026-01-01 00:00:00+00' + g * interval '1 second', "
            "timestamptz '2026-01-01 00:00:00+00' + g * interval '1 second' "
            "from generate_series(1, :n) as g"
        ),
        {"o": owner, "p": person, "prefix": prefix, "n": count},
    )


def page(client: TestClient, headers: dict, person: str, **params: object) -> list[dict]:
    response = client.get(notes_url(person), headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_the_default_page_is_twenty_notes_newest_first(
    client: TestClient, session: Session, as_a: dict
) -> None:
    person = make_person(client, as_a)
    seed_notes(session, USER_A, person, 25)

    first = page(client, as_a, person)
    second = page(client, as_a, person, offset=20)

    assert [n["content"] for n in first] == [f"n{i}" for i in range(25, 5, -1)]
    assert [n["content"] for n in second] == [f"n{i}" for i in range(5, 0, -1)]


def test_custom_limit_and_offset(client: TestClient, session: Session, as_a: dict) -> None:
    person = make_person(client, as_a)
    seed_notes(session, USER_A, person, 12)

    items = page(client, as_a, person, limit=5, offset=3)

    assert [n["content"] for n in items] == ["n9", "n8", "n7", "n6", "n5"]


def test_pages_cover_every_note_once(client: TestClient, session: Session, as_a: dict) -> None:
    person = make_person(client, as_a)
    seed_notes(session, USER_A, person, 23)

    seen: list[str] = []
    for offset in range(0, 30, 7):
        seen += [n["id"] for n in page(client, as_a, person, limit=7, offset=offset)]

    assert len(seen) == len(set(seen)) == 23


def test_the_limit_boundaries(client: TestClient, session: Session, as_a: dict) -> None:
    person = make_person(client, as_a)
    seed_notes(session, USER_A, person, 105)

    assert len(page(client, as_a, person, limit=1)) == 1
    assert len(page(client, as_a, person, limit=100)) == 100
    assert len(page(client, as_a, person, limit=100, offset=100)) == 5
    assert page(client, as_a, person, limit=50, offset=105) == []
    assert page(client, as_a, person, offset=2_147_483_647) == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": "abc"},
        {"limit": 1.5},
        {"limit": ""},
        {"offset": -1},
        {"offset": "abc"},
        {"offset": 2_147_483_648},
        {"offset": 99_999_999_999_999_999_999},
        {"limit": 5, "offset": -5},
    ],
)
def test_invalid_pagination_is_a_validation_error(
    client: TestClient, as_a: dict, params: dict
) -> None:
    person = make_person(client, as_a)

    response = client.get(notes_url(person), headers=as_a, params=params)

    error = assert_error(response, 422, "validation_error")
    assert error["fields"][0]["loc"][0] == "query"


def test_ordering_is_deterministic_when_creation_times_tie(
    client: TestClient, session: Session, as_a: dict
) -> None:
    person = make_person(client, as_a)
    seed_notes(session, USER_A, person, 12)
    session.execute(text("update notes set created_at = timestamptz '2026-02-02 00:00:00+00'"))

    ids = [n["id"] for n in page(client, as_a, person, limit=100)]

    assert ids == sorted(ids, reverse=True)
    paged = [n["id"] for n in page(client, as_a, person, limit=5)] + [
        n["id"] for n in page(client, as_a, person, limit=5, offset=5)
    ]
    assert paged == ids[:10]


def test_pagination_never_crosses_owners_or_people(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    person_one, person_two = make_person(client, as_a, "One"), make_person(client, as_a, "Two")
    person_b = make_person(client, as_b)
    seed_notes(session, USER_A, person_one, 6, "one-")
    seed_notes(session, USER_A, person_two, 6, "two-")
    seed_notes(session, USER_B, person_b, 6, "b-")

    items = page(client, as_a, person_one, limit=100)

    assert sorted(n["content"] for n in items) == sorted(f"one-{i}" for i in range(1, 7))
    assert {n["person_id"] for n in items} == {person_one}
    assert_not_found(client.get(notes_url(person_b), headers=as_a, params={"limit": 100}))


# --- privacy ----------------------------------------------------------------------------------


def test_note_text_never_reaches_logs_or_error_bodies(
    client: TestClient, session: Session, log_stream: io.StringIO, as_a: dict, as_b: dict
) -> None:
    person_a, person_b = make_person(client, as_a, SECRET_NAME), make_person(client, as_b)
    note_b = make_note(client, as_b, person_b, SECRET_NOTE)
    created = client.post(notes_url(person_a), json={"content": SECRET_NOTE}, headers=as_a)
    own = created.json()["id"]
    successes = [
        created,
        client.get(notes_url(person_a), headers=as_a),
        client.patch(note_url(person_a, own), json={"content": SECRET_NOTE + "!"}, headers=as_a),
        client.delete(note_url(person_a, own), headers=as_a),
    ]
    failures = [
        client.get(notes_url(person_b), headers=as_a),
        client.post(notes_url(person_b), json={"content": SECRET_NOTE}, headers=as_a),
        client.patch(note_url(person_b, note_b["id"]), json={"content": SECRET_NOTE}, headers=as_a),
        client.delete(note_url(person_b, note_b["id"]), headers=as_a),
        client.post(notes_url(person_a), json={"content": SECRET_NOTE + "\x00"}, headers=as_a),
        client.post(notes_url(person_a), json={"content": SECRET_NOTE * 1000}, headers=as_a),
        client.post(
            notes_url(person_a), json={"content": SECRET_NOTE, SECRET_NOTE: 1}, headers=as_a
        ),
        client.post(notes_url(person_a), json={"content": [SECRET_NOTE]}, headers=as_a),
        client.post(notes_url(SECRET_NOTE), json={"content": SECRET_NOTE}, headers=as_a),
        client.post(
            notes_url(person_a),
            json={"content": SECRET_NOTE},
            headers={"Authorization": "Bearer " + SECRET_NOTE},
        ),
        client.post(notes_url(person_a), json={"content": SECRET_NOTE}),
    ]

    assert [r.status_code for r in successes] == [201, 200, 200, 204]
    assert [r.status_code for r in failures] == [
        404,
        404,
        404,
        404,
        422,
        422,
        422,
        422,
        422,
        401,
        401,
    ]
    for response in failures:
        assert SECRET_NOTE not in response.text
        assert SECRET_NAME not in response.text
    logs = log_stream.getvalue()
    assert SECRET_NOTE not in logs
    assert SECRET_NAME not in logs


def test_note_text_in_a_database_error_never_reaches_the_response_or_logs(
    client: TestClient, session: Session, log_stream: io.StringIO, as_a: dict
) -> None:
    """PostgreSQL's own error text quotes the failing row, note text included."""
    person = make_person(client, as_a)
    session.execute(
        text(
            "alter table notes add constraint synthetic_forced_failure "
            "check (content not like '%SyntheticNoteTextNeverLogThis%')"
        )
    )

    response = client.post(notes_url(person), json={"content": SECRET_NOTE}, headers=as_a)

    error = assert_error(response, 500, "internal_error")
    assert error["message"] == "An unexpected error occurred."
    assert SECRET_NOTE not in response.text
    logs = log_stream.getvalue()
    assert "database_error" in logs
    assert "23514" in logs
    assert SECRET_NOTE not in logs
    assert "synthetic_forced_failure" not in logs
    session.rollback()


def test_a_missing_table_error_does_not_leak_note_text(
    client: TestClient, session: Session, log_stream: io.StringIO, as_a: dict
) -> None:
    person = make_person(client, as_a)
    session.execute(text("drop table notes"))

    response = client.post(notes_url(person), json={"content": SECRET_NOTE}, headers=as_a)

    assert_error(response, 500, "internal_error")
    assert SECRET_NOTE not in response.text
    assert SECRET_NOTE not in log_stream.getvalue()
    session.rollback()


def test_every_successful_notes_response_is_not_cacheable(client: TestClient, as_a: dict) -> None:
    person = make_person(client, as_a)
    created = client.post(notes_url(person), json={"content": "x"}, headers=as_a)
    note = created.json()["id"]
    responses = [
        created,
        client.get(notes_url(person), headers=as_a),
        client.patch(note_url(person, note), json={"content": "y"}, headers=as_a),
        client.delete(note_url(person, note), headers=as_a),
    ]

    assert [r.status_code for r in responses] == [201, 200, 200, 204]
    assert all(r.headers["Cache-Control"] == "no-store" for r in responses)


# --- regression: existing behaviour is unchanged ----------------------------------------------


def test_the_people_and_session_endpoints_still_work(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    person = make_person(client, as_a, "Regression Person")

    assert (
        client.get(f"{PEOPLE}/{person}", headers=as_a).json()["display_name"] == "Regression Person"
    )
    assert [p["id"] for p in client.get(PEOPLE, headers=as_a).json()["items"]] == [person]
    assert_not_found(client.get(f"{PEOPLE}/{person}", headers=as_b))
    assert client.get(f"{API_V1_PREFIX}/me", headers=as_a).json() == {"user_id": USER_A}
    assert client.get(f"{API_V1_PREFIX}/me").status_code == 401
    assert json.loads(client.get(f"{API_V1_PREFIX}/health").text)
