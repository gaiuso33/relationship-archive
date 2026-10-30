"""Synthetic values and probe endpoints shared by the privacy tests.

Nothing here is real. Each value is distinctive so that a test can search captured logs
and responses for it. The probe router exists only in tests, never in the application.
"""

import base64
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.errors import AppError, ErrorCode


def _b64(value: dict[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# Built at run time so that no credential-shaped literal sits in the source.
BEARER_VALUE = "-".join(("synthetic", "bearer", "credential", "value", "0001"))
JWT_VALUE = ".".join((_b64({"alg": "none"}), _b64({"sub": "synthetic"}), _b64({"sig": "none"})))
EMAIL = "synthetic.person@example.invalid"
NAME = "Alexandra Syntheticperson"
NOTE = "SYNTHETIC-NOTE-TEXT-never-log-this"
QUERY_VALUE = "SYNTHETIC-QUERY-VALUE-9876"
PATH_ID = "SYNTHETIC-PATH-ID-5555"
HTTP_DETAIL = "SYNTHETIC-HTTP-DETAIL-1212"
EXTRA_FIELD = "SYNTHETIC_EXTRA_FIELD_3434"
DB_VALUE = "synthetic.db.value@example.invalid"

ALL_SENSITIVE = (
    BEARER_VALUE,
    JWT_VALUE,
    EMAIL,
    NAME,
    NOTE,
    QUERY_VALUE,
    PATH_ID,
    HTTP_DETAIL,
    EXTRA_FIELD,
    DB_VALUE,
)

AUTH_HEADERS = {"Authorization": f"Bearer {BEARER_VALUE}", "Cookie": f"session={JWT_VALUE}"}


class NoteIn(BaseModel):
    text: str
    rating: int


class StrictNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


def build_probe_router() -> APIRouter:
    router = APIRouter(prefix="/probe")

    @router.post("/notes")
    def create_note(note: NoteIn) -> dict[str, bool]:
        return {"created": True}

    @router.post("/strict")
    def create_strict(note: StrictNoteIn) -> dict[str, bool]:
        return {"created": True}

    @router.get("/people/{person_id}")
    def read_person(person_id: str) -> dict[str, bool]:
        return {"found": True}

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError(f"could not save {NOTE} for {NAME} <{EMAIL}> with {BEARER_VALUE}")

    @router.get("/http-400")
    def http_400() -> None:
        raise HTTPException(status_code=400, detail=f"bad value {HTTP_DETAIL} {NAME}")

    @router.get("/http-503")
    def http_503() -> None:
        raise HTTPException(status_code=503, detail=f"upstream said {HTTP_DETAIL}")

    @router.get("/http-401")
    def http_401() -> None:
        raise HTTPException(
            status_code=401,
            detail=f"token {NOTE} rejected",
            headers={"WWW-Authenticate": "Bearer", "X-Debug": NOTE},
        )

    @router.get("/app-error")
    def app_error() -> None:
        raise AppError(ErrorCode.NOT_FOUND)

    return router
