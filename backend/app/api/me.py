from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.errors import ErrorResponse

router = APIRouter(tags=["session"])


class MeResponse(BaseModel):
    user_id: str


@router.get(
    "/me",
    summary="Current session",
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def read_me(user: CurrentUser, response: Response) -> MeResponse:
    """Return the authenticated user's stable id, and nothing else.

    Proves the Bearer token was verified. No request data influences the result, and no
    personal data (email, name) is read or returned.
    """
    # Identity responses must not be stored by browsers or shared caches.
    response.headers["Cache-Control"] = "no-store"
    return MeResponse(user_id=str(user.user_id))
