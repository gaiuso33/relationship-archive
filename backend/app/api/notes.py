"""Owner-scoped Notes API (issue #13), nested under a person.

The owner is `user.user_id`, taken from the verified token by `require_user`. Nothing in the
request (body, query string, headers) selects or overrides it, and the person comes only from
the URL. Every data access goes through `app.services.notes`, which applies the owner and the
person in SQL. A person or note that belongs to someone else (or a note that belongs to a
different person) is reported exactly like one that does not exist: 404 `not_found`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, require_user
from app.api.people import no_store
from app.core.errors import AppError, ErrorCode, ErrorResponse
from app.db.session import get_session
from app.models.note import Note
from app.schemas.notes import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_OFFSET,
    NoteCreate,
    NoteId,
    NoteList,
    NoteResponse,
    NoteUpdate,
)
from app.schemas.people import PersonId
from app.services import notes as notes_service

# Router dependencies run before parameter dependencies, so authentication happens before a
# database session is opened or the owner is used. FastAPI parses the raw request body before
# it runs any dependency, so a body that is not valid JSON is answered with 422 even without a
# token. Once the JSON parses, an unauthenticated request gets 401 before field validation.
router = APIRouter(
    prefix="/people/{person_id}/notes",
    tags=["notes"],
    dependencies=[Depends(require_user), Depends(no_store)],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)

SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Notes per page.")]
Offset = Annotated[int, Query(ge=0, le=MAX_OFFSET, description="Notes to skip.")]


def _found(note: Note | None) -> Note:
    if note is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return note


@router.get("", summary="List notes about one of my people")
def list_notes(
    person_id: PersonId,
    user: CurrentUser,
    session: SessionDep,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> NoteList:
    notes = notes_service.list_notes(session, user.user_id, person_id, limit=limit, offset=offset)
    if notes is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return NoteList(items=[NoteResponse.model_validate(n) for n in notes])


@router.post("", status_code=status.HTTP_201_CREATED, summary="Add a note to one of my people")
def create_note(
    person_id: PersonId, data: NoteCreate, user: CurrentUser, session: SessionDep
) -> NoteResponse:
    note = notes_service.create_note(session, user.user_id, person_id, data.content)
    return NoteResponse.model_validate(_found(note))


@router.patch("/{note_id}", summary="Edit one of my notes")
def update_note(
    person_id: PersonId,
    note_id: NoteId,
    data: NoteUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> NoteResponse:
    note = notes_service.update_note(session, user.user_id, person_id, note_id, data.content)
    return NoteResponse.model_validate(_found(note))


@router.delete(
    "/{note_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete one of my notes"
)
def delete_note(
    person_id: PersonId, note_id: NoteId, user: CurrentUser, session: SessionDep
) -> None:
    if not notes_service.delete_note(session, user.user_id, person_id, note_id):
        raise AppError(ErrorCode.NOT_FOUND)
