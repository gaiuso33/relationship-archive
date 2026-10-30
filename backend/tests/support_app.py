"""Application used by the real-server test: the real app plus the test-only probe router."""

from app.main import API_V1_PREFIX, create_app
from tests.support import build_probe_router

app = create_app()
app.include_router(build_probe_router(), prefix=API_V1_PREFIX)
