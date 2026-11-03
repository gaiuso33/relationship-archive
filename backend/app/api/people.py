"""Owner-scoped People API (issue #12).

The owner is `user.user_id`, taken from the verified token by `require_user`. Nothing in the
request (body, query string, other headers) selects or overrides it, and every data access goes
through `app.services.people`, which applies the owner in SQL. A person that belongs to another
user is reported exactly like one that does not exist: 404 `not_found`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, require_user
from app.core.errors import AppError, ErrorCode, ErrorResponse
from app.db.session import get_session
from app.models.person import Person
from app.schemas.people import PersonCreate, PersonId, PersonList, PersonResponse, PersonUpdate
from app.services import people as people_service


def no_store(response: Response) -> None:
    # Personal records must not be stored by browsers or shared caches.
    response.headers["Cache-Control"] = "no-store"


# Authentication runs first (router dependencies precede parameter dependencies), so an
# unauthenticated request is rejected before a database session or a body is touched.
router = APIRouter(
    prefix="/people",
    tags=["people"],
    dependencies=[Depends(require_user), Depends(no_store)],
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)

SessionDep = Annotated[Session, Depends(get_session)]
_NOT_FOUND = {404: {"model": ErrorResponse}}


def _found(person: Person | None) -> Person:
    if person is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return person


@router.get("", summary="List my people")
def list_people(user: CurrentUser, session: SessionDep) -> PersonList:
    people = people_service.list_people(session, user.user_id)
    return PersonList(items=[PersonResponse.model_validate(p) for p in people])


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a person")
def create_person(data: PersonCreate, user: CurrentUser, session: SessionDep) -> PersonResponse:
    person = people_service.create_person(session, user.user_id, data)
    return PersonResponse.model_validate(person)


@router.get("/{person_id}", summary="Get one of my people", responses=_NOT_FOUND)
def get_person(person_id: PersonId, user: CurrentUser, session: SessionDep) -> PersonResponse:
    person = people_service.get_person(session, user.user_id, person_id)
    return PersonResponse.model_validate(_found(person))


@router.patch("/{person_id}", summary="Update one of my people", responses=_NOT_FOUND)
def update_person(
    person_id: PersonId, data: PersonUpdate, user: CurrentUser, session: SessionDep
) -> PersonResponse:
    person = people_service.update_person(session, user.user_id, person_id, data.changes())
    return PersonResponse.model_validate(_found(person))


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of my people",
    responses=_NOT_FOUND,
)
def delete_person(person_id: PersonId, user: CurrentUser, session: SessionDep) -> None:
    if not people_service.delete_person(session, user.user_id, person_id):
        raise AppError(ErrorCode.NOT_FOUND)
