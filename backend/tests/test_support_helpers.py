"""The shared test helpers must themselves be able to fail.

`assert_identifier_not_reflected` replaced `value not in response.text` (#73). These tests
show that it still catches a real reflection of the request, and ignores only the request id.
"""

import pytest

from tests.support import assert_identifier_not_reflected

REQUEST_ID = "ab123fabcdefabcdefabcdefabcdefab"


def error_body(**changes: object) -> dict:
    body: dict = {
        "code": "validation_error",
        "message": "Some fields are missing or invalid.",
        "request_id": REQUEST_ID,
        "fields": [{"loc": ["path", "person_id"], "type": "uuid_parsing"}],
    }
    body.update(changes)
    return body


def test_a_value_found_only_in_the_request_id_is_not_a_reflection() -> None:
    assert_identifier_not_reflected(error_body(), "123")


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param(
            {"fields": [{"loc": ["path", "person_id"], "type": "uuid_parsing", "input": "123"}]},
            id="echoed-input-in-a-field",
        ),
        pytest.param({"message": "Invalid identifier 123."}, id="echoed-in-the-message"),
        pytest.param({"code": "invalid_123"}, id="echoed-in-the-code"),
        pytest.param(
            {"fields": [{"loc": ["path", "123"], "type": "uuid_parsing"}]}, id="echoed-in-loc"
        ),
        pytest.param({"detail": "123"}, id="echoed-in-an-extra-key"),
    ],
)
def test_a_value_reflected_anywhere_else_is_detected(changes: dict) -> None:
    with pytest.raises(AssertionError, match="reflected"):
        assert_identifier_not_reflected(error_body(**changes), "123")


def test_the_original_assertion_would_have_failed_on_the_request_id_alone() -> None:
    """Documents the defect: a plain substring check on the whole body trips on the id."""
    assert "123" in str(error_body())
