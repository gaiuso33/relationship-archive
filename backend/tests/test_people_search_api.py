"""Search people by name (issue #15), against real PostgreSQL.

Names only: notes, interests and preferences are never searched (product requirements, section
5.2 and journey J7). The search text travels in the request body. Two synthetic users, A and B,
use synthetic tokens (no network, no Supabase). Every test runs in a rolled-back transaction.
"""

import io
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, literal, select, text
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.session import get_session
from app.main import API_V1_PREFIX, create_app
from app.schemas.people import PersonCreate
from app.services import people as people_service
from tests import auth_support as a

PEOPLE = f"{API_V1_PREFIX}/people"
SEARCH = f"{PEOPLE}/search"
USER_A = a.USER_ID
USER_B = "99999999-8888-4777-8666-555555555555"
SECRET_TERM = "SyntheticSearchTermNeverLogThis"


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


def create(client: TestClient, headers: dict[str, str], name: str, **fields: object) -> dict:
    response = client.post(PEOPLE, json={"display_name": name, **fields}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def search(client: TestClient, headers: dict[str, str], query: object, **kwargs: object):
    return client.post(SEARCH, json={"query": query}, headers=headers, **kwargs)


def found(client: TestClient, headers: dict[str, str], query: str) -> list[str]:
    response = search(client, headers, query)
    assert response.status_code == 200, response.text
    return [item["display_name"] for item in response.json()["items"]]


# --- results, matching and ordering -------------------------------------------------------


@pytest.fixture
def friends(client: TestClient, as_a: dict, as_b: dict) -> None:
    """The scenario of journey J7: A has Sample Friend A and B, and B has Sample Friend C."""
    create(client, as_a, "Sample Friend B")
    create(client, as_a, "Sample Friend A")
    create(client, as_a, "Someone Else")
    create(client, as_b, "Sample Friend C")


def test_journey_j7_returns_the_callers_matches_and_never_another_users(
    client: TestClient, friends: None, as_a: dict, as_b: dict
) -> None:
    assert found(client, as_a, "Sample") == ["Sample Friend A", "Sample Friend B"]
    assert found(client, as_b, "Sample") == ["Sample Friend C"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("sample", ["Sample Friend A", "Sample Friend B"]),
        ("SAMPLE", ["Sample Friend A", "Sample Friend B"]),
        ("SaMpLe", ["Sample Friend A", "Sample Friend B"]),
        ("ample", ["Sample Friend A", "Sample Friend B"]),
        ("  Sample  ", ["Sample Friend A", "Sample Friend B"]),
        ("\tSample\n", ["Sample Friend A", "Sample Friend B"]),
        ("e Friend", ["Sample Friend A", "Sample Friend B"]),
        ("friend a", ["Sample Friend A"]),
        ("Friend B", ["Sample Friend B"]),
        ("lse", ["Someone Else"]),
    ],
)
def test_matching_is_a_trimmed_case_insensitive_substring(
    client: TestClient, friends: None, as_a: dict, query: str, expected: list[str]
) -> None:
    assert found(client, as_a, query) == expected


def test_the_whole_phrase_is_matched_not_separate_words(
    client: TestClient, friends: None, as_a: dict
) -> None:
    assert found(client, as_a, "Sample Else") == []
    assert found(client, as_a, "Friend Sample") == []


def test_no_match_is_an_ordinary_empty_result(
    client: TestClient, friends: None, as_a: dict
) -> None:
    response = search(client, as_a, "Nobody has this name")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_results_are_ordered_like_the_list_and_have_the_same_shape(
    client: TestClient, as_a: dict
) -> None:
    for name in ["zed Test", "Ada Test", "bob Test", "Ben Test"]:
        create(client, as_a, name, interests=["x"])
    listed = client.get(PEOPLE, headers=as_a).json()["items"]
    searched = search(client, as_a, "test").json()["items"]
    assert [p["display_name"] for p in searched] == ["Ada Test", "Ben Test", "bob Test", "zed Test"]
    assert searched == listed
    assert "owner_id" not in searched[0]


def test_search_responses_are_not_cacheable(client: TestClient, friends: None, as_a: dict) -> None:
    assert search(client, as_a, "Sample").headers["Cache-Control"] == "no-store"


def test_a_search_changes_nothing(client: TestClient, friends: None, as_a: dict) -> None:
    before = client.get(PEOPLE, headers=as_a).json()
    search(client, as_a, "Sample")
    search(client, as_a, "'; DROP TABLE people; --")
    assert client.get(PEOPLE, headers=as_a).json() == before


# --- names only (journey J7, owner decision 2026-10-31) -------------------------------------


def test_text_found_only_in_a_note_interest_or_preference_returns_nobody(
    client: TestClient, as_a: dict
) -> None:
    person = create(
        client,
        as_a,
        "Quiet Person",
        interests=["hiking", "chess"],
        preferences=["hiking boots"],
        timezone="Africa/Lagos",
        birthday="1990-05-04",
    )
    note = client.post(
        f"{PEOPLE}/{person['id']}/notes", json={"content": "Loves hiking on weekends"}, headers=as_a
    )
    assert note.status_code == 201

    assert found(client, as_a, "hiking") == []
    assert found(client, as_a, "chess") == []
    assert found(client, as_a, "weekends") == []
    # Other stored fields are not searched either: not the timezone, the date or the identifier.
    assert found(client, as_a, "Lagos") == []
    assert found(client, as_a, "1990") == []
    assert found(client, as_a, person["id"][:8]) == []
    # The name itself still matches.
    assert found(client, as_a, "quiet") == ["Quiet Person"]


def test_a_note_added_later_does_not_make_a_person_match(client: TestClient, as_a: dict) -> None:
    person = create(client, as_a, "Plain Name")
    client.post(f"{PEOPLE}/{person['id']}/notes", json={"content": "zebra"}, headers=as_a)
    assert found(client, as_a, "zebra") == []


# --- owner isolation ----------------------------------------------------------------------


def test_another_users_people_are_never_returned_even_with_an_identical_name(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    mine = create(client, as_a, "Identical Name")
    theirs = create(client, as_b, "Identical Name")
    create(client, as_b, "Only Bs Person", interests=["secret-b"])

    result_a = search(client, as_a, "identical").json()["items"]
    result_b = search(client, as_b, "identical").json()["items"]
    assert [p["id"] for p in result_a] == [mine["id"]]
    assert [p["id"] for p in result_b] == [theirs["id"]]
    assert found(client, as_a, "Only Bs") == []
    assert found(client, as_a, "secret-b") == []
    assert found(client, as_b, "Only Bs") == ["Only Bs Person"]


def test_the_owner_cannot_be_chosen_by_the_request(
    client: TestClient, as_a: dict, as_b: dict
) -> None:
    create(client, as_b, "Belongs To B")
    hints = {"X-User-Id": USER_B, "X-Owner-Id": USER_B}
    assert search(client, {**as_a, **hints}, "Belongs", params={"owner_id": USER_B}).json() == {
        "items": []
    }
    for field in ("owner_id", "user_id"):
        response = client.post(SEARCH, json={"query": "Belongs", field: USER_B}, headers=as_a)
        assert response.status_code == 422
        assert "Belongs To B" not in response.text


def test_the_service_layer_scopes_search_to_the_owner(session: Session) -> None:
    owner, other = uuid.uuid4(), uuid.uuid4()
    people_service.create_person(session, owner, PersonCreate(display_name="Shared Name"))
    people_service.create_person(session, other, PersonCreate(display_name="Shared Name"))
    assert [p.owner_id for p in people_service.search_people(session, owner, "shared")] == [owner]
    assert [p.owner_id for p in people_service.search_people(session, other, "shared")] == [other]
    assert people_service.search_people(session, uuid.uuid4(), "shared") == []


# --- wildcards and injection-style input --------------------------------------------------


@pytest.fixture
def special_names(client: TestClient, as_a: dict) -> None:
    for name in [
        "Ada Example",
        "Ada_Example",
        "100% Sample",
        "Back\\slash",
        "Slash/Name",
        "O'Neil",
    ]:
        create(client, as_a, name)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("%", ["100% Sample"]),
        ("100%", ["100% Sample"]),
        ("A%e", []),
        ("%%", []),
        ("_", ["Ada_Example"]),
        ("a_E", ["Ada_Example"]),
        ("A_a", []),
        ("a_", ["Ada_Example"]),
        ("\\", ["Back\\slash"]),
        ("k\\s", ["Back\\slash"]),
        ("\\A", []),
        ("\\%", []),
        ("/", ["Slash/Name"]),
        ("h/N", ["Slash/Name"]),
        ("//", []),
        ("/%", []),
        ("O'N", ["O'Neil"]),
    ],
)
def test_like_wildcards_and_escape_characters_match_only_themselves(
    client: TestClient, special_names: None, as_a: dict, query: str, expected: list[str]
) -> None:
    assert found(client, as_a, query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE people; --",
        "Robert'); DROP TABLE people;--",
        "' OR '1'='1",
        '" OR 1=1 --',
        "%' OR ''='",
        "x' UNION SELECT * FROM people --",
        "'; SELECT pg_sleep(10); --",
        "${jndi:ldap://example.invalid/x}",
        "{{7*7}}",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "\\'; --",
    ],
)
def test_injection_style_input_is_an_ordinary_search(
    client: TestClient, special_names: None, as_a: dict, query: str
) -> None:
    start = len(client.get(PEOPLE, headers=as_a).json()["items"])
    response = search(client, as_a, query)
    assert response.status_code == 200
    assert response.json() == {"items": []}
    # Nothing was dropped or changed, and the table is still there and working.
    assert len(client.get(PEOPLE, headers=as_a).json()["items"]) == start


def test_the_search_text_is_a_bound_parameter_never_part_of_the_sql(
    client: TestClient, as_a: dict, session: Session
) -> None:
    create(client, as_a, "Probe Person")
    captured: list[tuple[str, object]] = []
    engine = session.get_bind()

    def capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert found(client, as_a, "50%_/x'; DROP TABLE people; --") == []
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    searches = [c for c in captured if "FROM people" in c[0] and "ILIKE" in c[0]]
    assert len(searches) == 1
    statement, parameters = searches[0]
    assert "DROP TABLE" not in statement and "50%" not in statement
    assert "ESCAPE '/'" in statement
    # The text arrives only as a parameter, with %, _ and / escaped by the escape character.
    assert any(
        "50/%/_//x'; DROP TABLE people; --" in str(value) for value in dict(parameters).values()
    )


# --- validation ---------------------------------------------------------------------------


def assert_validation_error(response, *, secret: str | None = None) -> dict:
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    for field in error["fields"]:
        assert set(field) == {"loc", "type"}
    if secret is not None:
        assert secret not in response.text
    return error


@pytest.mark.parametrize("query", ["", "   ", "\t \n", " "])
def test_a_blank_query_is_rejected(client: TestClient, as_a: dict, query: str) -> None:
    error = assert_validation_error(search(client, as_a, query))
    assert error["fields"][0]["loc"] == ["body", "query"]


@pytest.mark.parametrize(
    "query", ["a\u0000b", "a\u0007b", "a\nb", "a\tb", "a\rb", "a b", "a‍b", "a﻿b"]
)
def test_control_and_invisible_characters_are_rejected_without_echoing_them(
    client: TestClient, as_a: dict, query: str
) -> None:
    assert_validation_error(search(client, as_a, query), secret=query)


def test_the_length_limit_counts_characters_not_bytes(client: TestClient, as_a: dict) -> None:
    assert search(client, as_a, "a" * 120).status_code == 200
    assert search(client, as_a, "😀" * 120).status_code == 200
    assert_validation_error(search(client, as_a, "a" * 121))
    assert_validation_error(search(client, as_a, "😀" * 121))
    # Trimming happens first, so padding does not count.
    assert search(client, as_a, "  " + "a" * 120 + "  ").status_code == 200


def test_an_enormous_query_is_rejected_without_being_echoed(client: TestClient, as_a: dict) -> None:
    response = search(client, as_a, "needle" * 400_000)  # 2.4 million characters
    assert_validation_error(response, secret="needleneedle")
    assert len(response.text) < 1_000


@pytest.mark.parametrize("query", [None, 5, 1.5, True, ["a"], {"a": "b"}])
def test_the_query_must_be_a_string(client: TestClient, as_a: dict, query: object) -> None:
    assert_validation_error(search(client, as_a, query))


@pytest.mark.parametrize(
    "body", [{}, {"q": "x"}, {"query": "x", "extra": 1}, {"query": "x", "limit": 5}, [], "x", None]
)
def test_the_body_must_be_exactly_a_query(client: TestClient, as_a: dict, body: object) -> None:
    assert_validation_error(client.post(SEARCH, json=body, headers=as_a))


def test_a_body_that_is_not_json_is_rejected(client: TestClient, as_a: dict) -> None:
    for content in (b"", b"not json", b'{"query": ', b"\xff\xfe"):
        response = client.post(
            SEARCH, content=content, headers={**as_a, "Content-Type": "application/json"}
        )
        assert_validation_error(response)


def test_the_text_is_not_accepted_in_the_url(client: TestClient, as_a: dict) -> None:
    create(client, as_a, "Findable Person")
    create(client, as_a, "Other Person")
    # Nothing in the URL filters the list: this is the ordinary, unfiltered list.
    listed = client.get(f"{PEOPLE}?query=Findable", headers=as_a).json()["items"]
    assert len(listed) == 2
    # GET on the search path is not a search either: "search" is not an identifier.
    response = client.get(f"{SEARCH}?query=Findable", headers=as_a)
    error = assert_validation_error(response, secret="Findable")
    assert error["fields"][0]["loc"] == ["path", "person_id"]


# --- authentication -----------------------------------------------------------------------


def test_search_requires_authentication_before_anything_else(
    app: FastAPI, client: TestClient
) -> None:
    opened: list[str] = []

    def must_not_open_a_session() -> Iterator[Session]:
        opened.append("opened")
        raise AssertionError("a session was opened for an unauthenticated request")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = must_not_open_a_session
    for headers in ({}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"}):
        response = client.post(SEARCH, json={"query": "Sample"}, headers=headers)
        assert response.status_code == 401
        assert "Sample" not in response.text
    assert opened == []


# --- case folding on PostgreSQL ------------------------------------------------------------
#
# Case-insensitivity is PostgreSQL's ILIKE, so it depends on the database's character settings
# (a `C` database folds only ASCII; others fold most scripts, with some per-letter differences).
# These tests therefore do not hard-code non-ASCII outcomes. They assert that the search agrees
# with the database's own `lower()` for the same text, and they assert what is true for every
# collation: ASCII folds, accents do not fold, and composed and decomposed text are different.


def test_the_database_stores_utf8(session: Session) -> None:
    assert session.scalar(text("show server_encoding")) == "UTF8"


@pytest.mark.parametrize(
    ("stored", "query"),
    [
        ("Émilie", "émilie"),
        ("émilie", "ÉMILIE"),
        ("ÜNAL", "ünal"),
        ("ДМИТРИЙ", "дмитрий"),
        ("ΑΒΓ", "αβγ"),
        ("ΣΙΣΥΦΟΣ", "σισυφοσ"),
        ("ΣΙΣΥΦΟΣ", "σισυφος"),
        ("STRAẞE", "straße"),
        ("İstanbul", "istanbul"),
        ("IĞDIR", "ığdır"),
        ("𐐀𐐁", "𐐨𐐩"),
    ],
)
def test_non_ascii_case_folding_agrees_with_the_databases_lower(
    client: TestClient, as_a: dict, session: Session, stored: str, query: str
) -> None:
    create(client, as_a, stored)
    database_says_equal = bool(
        session.scalar(select(func.lower(literal(stored)) == func.lower(literal(query))))
    )
    assert bool(found(client, as_a, query)) is database_says_equal


@pytest.mark.parametrize(
    ("stored", "query"),
    [
        ("Émilie", "emilie"),
        ("Emilie", "émilie"),
        ("Émilie", "Émilie"),
        ("Émilie", "Émilie"),
        ("Straße", "STRASSE"),
        ("STRASSE", "straße"),
        ("Ǆ", "ǆǆ"),
    ],
)
def test_accents_are_not_folded_and_text_is_not_normalised(
    client: TestClient, as_a: dict, stored: str, query: str
) -> None:
    create(client, as_a, stored)
    assert found(client, as_a, query) == []


def test_ascii_letters_always_fold_and_scripts_without_case_match_exactly(
    client: TestClient, as_a: dict
) -> None:
    create(client, as_a, "Ada Example")
    create(client, as_a, "李雷")
    create(client, as_a, "Ada 😀 Friend")
    assert found(client, as_a, "ADA EXAMPLE") == ["Ada Example"]
    assert found(client, as_a, "李") == ["李雷"]
    assert found(client, as_a, "😀") == ["Ada 😀 Friend"]


# --- logging and error bodies -------------------------------------------------------------


def test_search_terms_and_results_never_reach_logs_or_error_bodies(
    client: TestClient, log_stream: io.StringIO, as_a: dict, as_b: dict, session: Session
) -> None:
    person = create(client, as_a, SECRET_TERM + " Match", interests=["SyntheticInterestSecret"])
    client.post(
        f"{PEOPLE}/{person['id']}/notes", json={"content": "SyntheticNoteSecret"}, headers=as_a
    )
    create(client, as_b, SECRET_TERM + " Other")

    responses = [
        search(client, as_a, SECRET_TERM),  # a match
        search(client, as_a, SECRET_TERM + " nothing"),  # no match
        search(client, as_a, SECRET_TERM + "\u0000"),  # 422
        client.post(SEARCH, json={"query": SECRET_TERM, "x": SECRET_TERM}, headers=as_a),  # 422
        search(client, {"Authorization": "Bearer " + SECRET_TERM}, SECRET_TERM),  # 401
        client.post(
            SEARCH, content=SECRET_TERM, headers={**as_a, "Content-Type": "application/json"}
        ),
    ]
    assert [r.status_code for r in responses] == [200, 200, 422, 422, 401, 422]
    for response in responses[2:]:
        assert SECRET_TERM not in response.text

    logs = log_stream.getvalue()
    assert SECRET_TERM not in logs
    assert "SyntheticInterestSecret" not in logs
    assert "SyntheticNoteSecret" not in logs
    # The request is logged by its route template only.
    assert "/api/v1/people/search" in logs
