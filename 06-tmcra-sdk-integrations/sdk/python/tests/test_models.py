from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tmcra_client import IngestRequest, MemoryMessage


def message(message_id: str) -> MemoryMessage:
    return MemoryMessage(
        message_id=message_id,
        role="user",
        content="hello",
        timestamp=datetime.now(timezone.utc),
    )


def test_ingest_request_rejects_duplicate_message_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        IngestRequest(session_id="s", messages=[message("m"), message("m")])


def test_ingest_request_serializes_datetime_and_defaults() -> None:
    request = IngestRequest(session_id="s", messages=[message("m")])
    payload = request.model_dump(mode="json")
    assert payload["consistency"] == "eventual"
    assert payload["slow_policy"] == "auto"
    assert payload["messages"][0]["timestamp"].endswith("Z")
