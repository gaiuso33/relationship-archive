"""The owner-scoped People API (issue #12), against real PostgreSQL.

Two synthetic users, A and B, sign in with synthetic tokens (a fake key provider: no network,
no Supabase). Every test runs in a transaction that is rolled back. The point of the suite is
that one user can never read, change, delete or discover another user's people.
"""

import inspect
import io
import re
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import get_session
from app.main import API_V1_PREFIX, create_app
from app.models.person import Person
from app.schemas.people import PersonCreate
from app.services import people as people_service
from tests import auth_support as a

PEOPLE = f"{API_V1_PREFIX}/people"
USER_A = a.USER_ID
USER_B = "99999999-8888-4777-8666-555555555555"
SECRET_NAME = "SyntheticPersonNameNeverLogThis"
SECRET_INTEREST = "SyntheticInterestNeverLogThis"


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


def create(client: TestClient, headers: dict[str, str], **fields: object) -> dict:
    body = {"display_name": "Synthetic Person", **fields}
    response = client.post(PEOPLE, json=body, headers=headers)
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


# --- authentication ----------------------------------------------------------------------


def people_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) under /people, read from the public OpenAPI schema."""
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(PEOPLE)
        for method in operations
    )


def test_the_expected_routes_exist(app: FastAPI) -> None:
    notes = f"{PEOPLE}/{{person_id}}/notes"
    assert people_routes(app) == [
        ("DELETE", f"{PEOPLE}/{{person_id}}"),
        ("DELETE", f"{notes}/{{note_id}}"),
        ("GET", PEOPLE),
        ("GET", f"{PEOPLE}/{{person_id}}"),
        ("GET", notes),
        ("PATCH", f"{PEOPLE}/{{person_id}}"),
        ("PATCH", f"{notes}/{{note_id}}"),
        ("POST", PEOPLE),
        ("POST", f"{PEOPLE}/search"),
        ("POST", notes),
    ]


def test_every_people_route_rejects_unauthenticated_requests(
    app: FastAPI, client: TestClient
) -> None:
    """Enumerates the router, so a route added later without authentication fails here."""
    opened: list[str] = []

    def must_not_open_a_session() -> Iterator[Session]:
        opened.append("opened")
        raise AssertionError("a session was opened for an unauthenticated request")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = must_not_open_a_session
    routes = people_routes(app)
    assert routes
    for method, path in routes:
        url = path.replace("{person_id}", str(uuid.uuid4())).replace("{note_id}", str(uuid.uuid4()))
        response = client.request(method, url, json={"display_name": "x"})
        assert response.status_code == 401, (method, path)
        error = assert_error(response, 401, "unauthorized")
        assert error["message"] == "Authentication is required."
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert opened == []


@pytest.mark.parametrize(
    "make_headers",
    [
        pytest.param(lambda: {"Authorization": "Bearer not-a-token"}, id="garbage"),
        pytest.param(lambda: {"Authorization": "Basic abc"}, id="wrong-scheme"),
        pytest.param(
            lambda: {
                "Authorization": "Bearer "
                + a.make_token(a.valid_claims(exp=int(time.time()) - 3600))
            },
            id="expired",
        ),
        pytest.param(
            lambda: {"Authorization": f"Bearer {a.make_token(key=a.OTHER_EC_KEY)}"},
            id="wrong-signature",
        ),
        pytest.param(
            lambda: {"Authorization": f"Bearer {a.make_token(a.valid_claims(aud='other'))}"},
            id="wrong-audience",
        ),
        pytest.param(
            lambda: {"Authorization": f"Bearer {a.make_token(a.valid_claims(role='anon'))}"},
            id="wrong-role",
        ),
    ],
)
def test_bad_tokens_are_rejected_on_the_people_api(
    client: TestClient, make_headers: Callable[[], dict[str, str]]
) -> None:
    for method, url in [("GET", PEOPLE), ("POST", PEOPLE), ("GET", f"{PEOPLE}/{uuid.uuid4()}")]:
        response = client.request(method, url, json={"display_name": "x"}, headers=make_headers())
        assert_error(response, 401, "unauthorized")


def test_people_api_does_not_fail_open_when_signing_keys_are_unavailable(
    log_format: str, log_stream: io.StringIO, session: Session
) -> None:
    settings = a.auth_settings(log_level="DEBUG", log_format=log_format)
    application = create_app(settings, a.FakeKeys(unavailable=True))
    configure_logging(settings, log_stream)
    application.dependency_overrides[get_session] = lambda: session
    client = TestClient(application, raise_server_exceptions=False)

    response = client.get(PEOPLE, headers=auth(USER_A))

    assert_error(response, 503, "service_unavailable")


def test_people_api_answers_503_when_supabase_is_not_configured(session: Session) -> None:
    application = create_app(Settings(_env_file=None, app_env="test"))
    application.dependency_overrides[get_session] = lambda: session
    client = TestClient(application, raise_server_exceptions=False)

    assert_error(client.get(PEOPLE, headers=auth(USER_A)), 503, "service_unavailable")


# --- create and read ---------------------------------------------------------------------


def test_create_returns_the_person_without_an_owner(client: TestClient, as_a: dict) -> None:
    response = client.post(
        PEOPLE,
        json={
            "display_name": "  Ada Synthetic  ",
            "birthday": "1990-04-05",
            "timezone": "Africa/Lagos",
            "interests": ["Chess", "Hiking"],
            "preferences": ["Prefers tea"],
        },
        headers=as_a,
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {
        "id",
        "display_name",
        "birthday",
        "timezone",
        "interests",
        "preferences",
        "created_at",
        "updated_at",
    }
    uuid.UUID(body["id"])
    assert body["display_name"] == "Ada Synthetic"
    assert body["birthday"] == "1990-04-05"
    assert body["timezone"] == "Africa/Lagos"
    assert body["interests"] == ["Chess", "Hiking"]
    assert body["preferences"] == ["Prefers tea"]
    assert body["created_at"] == body["updated_at"]
    assert USER_A not in response.text


def test_create_applies_defaults_for_optional_fields(client: TestClient, as_a: dict) -> None:
    body = create(client, as_a)

    assert body["birthday"] is None
    assert body["timezone"] is None
    assert body["interests"] == []
    assert body["preferences"] == []


def test_owner_is_taken_from_the_verified_token(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    from_a = create(client, as_a, display_name="Made by A")
    from_b = create(client, as_b, display_name="Made by B")

    owners = dict(session.execute(select(Person.display_name, Person.owner_id)).all())
    assert owners["Made by A"] == uuid.UUID(USER_A)
    assert owners["Made by B"] == uuid.UUID(USER_B)
    assert from_a["id"] != from_b["id"]


def test_get_returns_own_person(client: TestClient, as_a: dict) -> None:
    created = create(client, as_a, interests=["Chess"])

    response = client.get(f"{PEOPLE}/{created['id']}", headers=as_a)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == created


def test_list_returns_only_own_people_in_a_stable_order(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    create(client, as_a, display_name="zoe")
    create(client, as_a, display_name="Adam", interests=["Chess"])
    create(client, as_a, display_name="bella")
    create(client, as_b, display_name="Belongs to B")

    response = client.get(PEOPLE, headers=as_a)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    names = [p["display_name"] for p in response.json()["items"]]
    assert names == ["Adam", "bella", "zoe"]
    assert response.json()["items"][0]["interests"] == ["Chess"]


def test_list_is_empty_for_a_user_with_no_people(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    create(client, as_b)

    assert client.get(PEOPLE, headers=as_a).json() == {"items": []}


# --- update ------------------------------------------------------------------------------


def test_update_changes_only_the_fields_sent(client: TestClient, as_a: dict) -> None:
    created = create(
        client,
        as_a,
        display_name="Before",
        birthday="1990-04-05",
        timezone="Africa/Lagos",
        interests=["Chess"],
        preferences=["Tea"],
    )
    time.sleep(0.01)

    response = client.patch(
        f"{PEOPLE}/{created['id']}",
        json={"display_name": "After", "interests": ["Chess", "Hiking"]},
        headers=as_a,
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    updated = response.json()
    assert updated["display_name"] == "After"
    assert updated["interests"] == ["Chess", "Hiking"]
    assert updated["birthday"] == "1990-04-05"
    assert updated["timezone"] == "Africa/Lagos"
    assert updated["preferences"] == ["Tea"]
    assert updated["id"] == created["id"]
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    assert client.get(f"{PEOPLE}/{created['id']}", headers=as_a).json() == updated


def test_update_can_clear_optional_fields_and_lists(client: TestClient, as_a: dict) -> None:
    created = create(
        client, as_a, birthday="1990-04-05", timezone="UTC", interests=["a"], preferences=["b"]
    )

    response = client.patch(
        f"{PEOPLE}/{created['id']}",
        json={"birthday": None, "timezone": None, "interests": [], "preferences": []},
        headers=as_a,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["birthday"], body["timezone"]) == (None, None)
    assert (body["interests"], body["preferences"]) == ([], [])


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-body"),
        pytest.param({"display_name": None}, id="null-name"),
        pytest.param({"interests": None}, id="null-interests"),
        pytest.param({"preferences": None}, id="null-preferences"),
        pytest.param({"display_name": "  "}, id="blank-name"),
        pytest.param({"interests": ["ok", " "]}, id="blank-interest"),
    ],
)
def test_invalid_update_is_rejected_and_changes_nothing(
    client: TestClient, as_a: dict, payload: dict
) -> None:
    created = create(client, as_a, display_name="Keep me", interests=["Chess"])

    response = client.patch(f"{PEOPLE}/{created['id']}", json=payload, headers=as_a)

    assert_error(response, 422, "validation_error")
    assert client.get(f"{PEOPLE}/{created['id']}", headers=as_a).json() == created


# --- delete ------------------------------------------------------------------------------


def test_delete_removes_own_person(client: TestClient, session: Session, as_a: dict) -> None:
    keep = create(client, as_a, display_name="Keep")
    gone = create(client, as_a, display_name="Gone")

    response = client.delete(f"{PEOPLE}/{gone['id']}", headers=as_a)

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert_not_found(client.get(f"{PEOPLE}/{gone['id']}", headers=as_a))
    assert [p["id"] for p in client.get(PEOPLE, headers=as_a).json()["items"]] == [keep["id"]]
    assert session.scalar(select(func.count()).select_from(Person)) == 1


def test_deleting_twice_is_not_found(client: TestClient, as_a: dict) -> None:
    created = create(client, as_a)
    assert client.delete(f"{PEOPLE}/{created['id']}", headers=as_a).status_code == 204

    assert_not_found(client.delete(f"{PEOPLE}/{created['id']}", headers=as_a))


# --- cross-user access (IDOR) --------------------------------------------------------------


def request_for(client: TestClient, method: str, person_id: str, headers: dict):
    if method == "GET":
        return client.get(f"{PEOPLE}/{person_id}", headers=headers)
    if method == "PATCH":
        return client.patch(
            f"{PEOPLE}/{person_id}", json={"display_name": "Taken over"}, headers=headers
        )
    return client.delete(f"{PEOPLE}/{person_id}", headers=headers)


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_user_a_cannot_reach_user_bs_person(
    client: TestClient, as_a: dict, as_b: dict, method: str
) -> None:
    owned_by_b = create(
        client, as_b, display_name="B's person", birthday="1985-01-02", interests=["Secret"]
    )
    missing_id = str(uuid.uuid4())

    other = request_for(client, method, owned_by_b["id"], as_a)
    missing = request_for(client, method, missing_id, as_a)

    # Another user's person looks exactly like one that does not exist: nothing is leaked.
    assert_not_found(other)
    assert_not_found(missing)
    other_body, missing_body = other.json()["error"], missing.json()["error"]
    other_body.pop("request_id"), missing_body.pop("request_id")
    assert other_body == missing_body
    assert "B's person" not in other.text and "Secret" not in other.text
    # B's record is untouched.
    assert client.get(f"{PEOPLE}/{owned_by_b['id']}", headers=as_b).json() == owned_by_b


def test_user_a_cannot_enumerate_or_list_user_bs_people(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    owned_by_b = create(client, as_b, display_name="B's person")
    mine = create(client, as_a, display_name="A's person")

    listed = client.get(PEOPLE, headers=as_a).json()["items"]

    assert [p["id"] for p in listed] == [mine["id"]]
    assert owned_by_b["id"] not in client.get(PEOPLE, headers=as_a).text


def test_a_failed_cross_user_delete_leaves_the_row_in_the_database(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    owned_by_b = create(client, as_b)

    client.delete(f"{PEOPLE}/{owned_by_b['id']}", headers=as_a)

    row = session.get(Person, uuid.UUID(owned_by_b["id"]))
    assert row is not None and row.owner_id == uuid.UUID(USER_B)


# --- client-supplied owners are never trusted ---------------------------------------------


@pytest.mark.parametrize("field", ["owner_id", "user_id", "owner", "id", "created_at"])
def test_create_rejects_a_client_supplied_owner_or_server_field(
    client: TestClient, session: Session, as_a: dict, field: str
) -> None:
    response = client.post(PEOPLE, json={"display_name": "Mallory", field: USER_B}, headers=as_a)

    error = assert_error(response, 422, "validation_error")
    assert USER_B not in response.text
    assert error["fields"] == [{"loc": ["body", "<unknown field>"], "type": "extra_forbidden"}]
    assert session.scalar(select(func.count()).select_from(Person)) == 0


def test_update_cannot_reassign_the_owner(
    client: TestClient, session: Session, as_a: dict, as_b: dict
) -> None:
    created = create(client, as_a)

    response = client.patch(f"{PEOPLE}/{created['id']}", json={"owner_id": USER_B}, headers=as_a)

    assert_error(response, 422, "validation_error")
    row = session.get(Person, uuid.UUID(created["id"]))
    assert row is not None and row.owner_id == uuid.UUID(USER_A)
    assert_not_found(client.get(f"{PEOPLE}/{created['id']}", headers=as_b))


def test_owner_hints_in_query_and_headers_are_ignored(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    owned_by_b = create(client, as_b, display_name="B's person")
    hints = {"X-User-Id": USER_B, "X-Owner-Id": USER_B, "X-Forwarded-User": USER_B}

    listed = client.get(
        PEOPLE, params={"owner_id": USER_B, "user_id": USER_B}, headers={**as_a, **hints}
    )
    by_id = client.get(
        f"{PEOPLE}/{owned_by_b['id']}", params={"owner_id": USER_B}, headers={**as_a, **hints}
    )
    created = client.post(
        PEOPLE,
        params={"owner_id": USER_B},
        json={"display_name": "Made with hints"},
        headers={**as_a, **hints},
    )

    assert listed.json() == {"items": []}
    assert_not_found(by_id)
    assert created.status_code == 201
    # The person was created for A (the token), not for B (the hints).
    assert [p["display_name"] for p in client.get(PEOPLE, headers=as_a).json()["items"]] == [
        "Made with hints"
    ]
    assert [p["display_name"] for p in client.get(PEOPLE, headers=as_b).json()["items"]] == [
        "B's person"
    ]


def test_service_functions_always_require_the_owner() -> None:
    """A guard: no data-access function can be written without saying whose data it is."""
    functions = [
        member
        for name, member in inspect.getmembers(people_service, inspect.isfunction)
        if member.__module__ == people_service.__name__ and not name.startswith("_")
    ]
    assert {f.__name__ for f in functions} == {
        "list_people",
        "search_people",
        "get_person",
        "create_person",
        "update_person",
        "delete_person",
    }
    for function in functions:
        parameters = list(inspect.signature(function).parameters)
        assert parameters[:2] == ["session", "owner_id"], function.__name__


def test_service_layer_scopes_every_operation_to_the_owner(session: Session) -> None:
    owner, other = uuid.uuid4(), uuid.uuid4()
    person = people_service.create_person(session, owner, PersonCreate(display_name="Mine"))

    assert people_service.get_person(session, other, person.id) is None
    assert people_service.update_person(session, other, person.id, {"display_name": "X"}) is None
    assert people_service.delete_person(session, other, person.id) is False
    assert people_service.list_people(session, other) == []
    # Nothing changed, and the owner still has full access.
    mine = people_service.get_person(session, owner, person.id)
    assert mine is not None and mine.display_name == "Mine"
    assert people_service.delete_person(session, owner, person.id) is True


# --- missing records and malformed identifiers -----------------------------------------------


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_a_missing_person_is_not_found(client: TestClient, as_a: dict, method: str) -> None:
    assert_not_found(request_for(client, method, str(uuid.uuid4()), as_a))


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("person_id", ["not-a-uuid", "123", "00000000-0000-0000-0000-00000000000g"])
def test_a_malformed_identifier_is_a_validation_error(
    client: TestClient, as_a: dict, method: str, person_id: str
) -> None:
    response = request_for(client, method, person_id, as_a)

    error = assert_error(response, 422, "validation_error")
    assert person_id not in response.text
    assert error["fields"][0]["loc"] == ["path", "person_id"]


# --- input validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-name"),
        pytest.param({"display_name": None}, id="null-name"),
        pytest.param({"display_name": ""}, id="empty-name"),
        pytest.param({"display_name": "   \t "}, id="blank-name"),
        pytest.param({"display_name": "x" * 121}, id="name-too-long"),
        pytest.param({"display_name": "line\nbreak"}, id="name-control-character"),
        pytest.param({"display_name": "nul\x00"}, id="name-nul"),
        pytest.param({"display_name": 123}, id="name-not-text"),
        pytest.param({"display_name": ["a"]}, id="name-list"),
        pytest.param({"display_name": "A", "birthday": "not-a-date"}, id="birthday-format"),
        pytest.param({"display_name": "A", "birthday": "1899-12-31"}, id="birthday-too-old"),
        pytest.param({"display_name": "A", "birthday": "2999-01-01"}, id="birthday-future"),
        pytest.param({"display_name": "A", "birthday": "2000-02-30"}, id="birthday-impossible"),
        pytest.param({"display_name": "A", "timezone": "Not A Zone!"}, id="timezone-shape"),
        pytest.param({"display_name": "A", "timezone": "../etc/passwd"}, id="timezone-path"),
        pytest.param({"display_name": "A", "timezone": "x" * 65}, id="timezone-too-long"),
        pytest.param({"display_name": "A", "timezone": 5}, id="timezone-not-text"),
        pytest.param({"display_name": "A", "interests": "chess"}, id="interests-not-list"),
        pytest.param({"display_name": "A", "interests": None}, id="interests-null"),
        pytest.param({"display_name": "A", "interests": [""]}, id="interest-empty"),
        pytest.param({"display_name": "A", "interests": ["  "]}, id="interest-blank"),
        pytest.param({"display_name": "A", "interests": ["x" * 101]}, id="interest-too-long"),
        pytest.param({"display_name": "A", "interests": [5]}, id="interest-not-text"),
        pytest.param({"display_name": "A", "interests": ["a\nb"]}, id="interest-control-char"),
        pytest.param(
            {"display_name": "A", "interests": [f"i{n}" for n in range(51)]},
            id="too-many-interests",
        ),
        pytest.param({"display_name": "A", "preferences": [""]}, id="preference-empty"),
        pytest.param({"display_name": "A", "preferences": ["y" * 101]}, id="preference-too-long"),
        pytest.param(
            {"display_name": "A", "preferences": [f"p{n}" for n in range(51)]},
            id="too-many-preferences",
        ),
    ],
)
def test_invalid_create_is_rejected_and_stores_nothing(
    client: TestClient, session: Session, as_a: dict, payload: dict
) -> None:
    response = client.post(PEOPLE, json=payload, headers=as_a)

    error = assert_error(response, 422, "validation_error")
    assert error["message"] == "Some fields are missing or invalid."
    assert session.scalar(select(func.count()).select_from(Person)) == 0


def test_a_body_that_is_not_json_is_rejected(client: TestClient, as_a: dict) -> None:
    response = client.post(
        PEOPLE, content=b"{not json", headers={**as_a, "Content-Type": "application/json"}
    )

    assert_error(response, 422, "validation_error")


def test_documented_limits_are_accepted_at_the_boundary(client: TestClient, as_a: dict) -> None:
    body = create(
        client,
        as_a,
        display_name="n" * 120,
        interests=["i" * 100] + [f"interest {n}" for n in range(49)],
        preferences=[f"preference {n}" for n in range(50)],
        timezone="America/Argentina/Buenos_Aires",
        birthday="1900-01-01",
    )

    assert len(body["display_name"]) == 120
    assert len(body["interests"]) == 50
    assert len(body["preferences"]) == 50


def test_entries_are_trimmed_and_case_insensitive_duplicates_are_dropped(
    client: TestClient, as_a: dict
) -> None:
    body = create(
        client,
        as_a,
        interests=["  Chess ", "chess", "CHESS", "Hiking", " hiking"],
        preferences=["Tea", "tea ", "Coffee"],
    )

    assert body["interests"] == ["Chess", "Hiking"]
    assert body["preferences"] == ["Tea", "Coffee"]


def test_interests_and_preferences_can_be_set_changed_and_read(
    client: TestClient, as_a: dict
) -> None:
    created = create(client, as_a, interests=["Chess"], preferences=["Tea"])
    changed = client.patch(
        f"{PEOPLE}/{created['id']}",
        json={"interests": ["Hiking", "hiking", " Reading "], "preferences": ["Coffee"]},
        headers=as_a,
    ).json()

    assert changed["interests"] == ["Hiking", "Reading"]
    assert changed["preferences"] == ["Coffee"]
    fetched = client.get(f"{PEOPLE}/{created['id']}", headers=as_a).json()
    listed = client.get(PEOPLE, headers=as_a).json()["items"][0]
    assert fetched["interests"] == listed["interests"] == ["Hiking", "Reading"]
    assert fetched["preferences"] == listed["preferences"] == ["Coffee"]


def test_a_birthday_of_today_is_accepted(client: TestClient, as_a: dict) -> None:
    assert create(client, as_a, birthday=date.today().isoformat())["birthday"] is not None


# --- privacy of errors and logs ----------------------------------------------------------------


def test_names_and_entries_never_reach_logs_or_error_bodies(
    client: TestClient, log_stream: io.StringIO, as_a: dict, as_b: dict
) -> None:
    owned_by_b = create(client, as_b, display_name=SECRET_NAME, interests=[SECRET_INTEREST])
    responses = [
        client.post(PEOPLE, json={"display_name": SECRET_NAME, "birthday": "nope"}, headers=as_a),
        client.post(
            PEOPLE,
            json={"display_name": SECRET_NAME, "interests": [SECRET_INTEREST, ""]},
            headers=as_a,
        ),
        client.get(f"{PEOPLE}/{owned_by_b['id']}", headers=as_a),
        client.patch(
            f"{PEOPLE}/{owned_by_b['id']}", json={"display_name": SECRET_NAME}, headers=as_a
        ),
        client.delete(f"{PEOPLE}/{owned_by_b['id']}", headers=as_a),
        client.get(f"{PEOPLE}/{SECRET_NAME}", headers=as_a),
        client.post(
            PEOPLE, json={"display_name": SECRET_NAME}, headers={"Authorization": "Bearer x"}
        ),
    ]

    assert [r.status_code for r in responses] == [422, 422, 404, 404, 404, 422, 401]
    for response in responses:
        assert SECRET_NAME not in response.text
        assert SECRET_INTEREST not in response.text
    logs = log_stream.getvalue()
    assert SECRET_NAME not in logs
    assert SECRET_INTEREST not in logs
    assert re.search(r"Bearer x", logs) is None


def test_existing_me_endpoint_is_unchanged(client: TestClient, as_a: dict) -> None:
    response = client.get(f"{API_V1_PREFIX}/me", headers=as_a)

    assert response.status_code == 200
    assert response.json() == {"user_id": USER_A}
    assert client.get(f"{API_V1_PREFIX}/me").status_code == 401
