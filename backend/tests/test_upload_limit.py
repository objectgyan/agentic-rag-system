"""Tests for the capped upload reader (F7)."""

import io
import pytest
from fastapi import HTTPException

from app.api.v1.documents import read_upload_capped


class _FakeUpload:
    """Minimal UploadFile stand-in with an async chunked read()."""

    def __init__(self, data: bytes, filename: str = "f.bin"):
        self._buf = io.BytesIO(data)
        self.filename = filename

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


@pytest.mark.asyncio
async def test_reads_file_under_limit():
    data = b"x" * (3 * 1024 * 1024)  # 3 MiB
    result = await read_upload_capped(_FakeUpload(data), max_bytes=5 * 1024 * 1024)
    assert result == data


@pytest.mark.asyncio
async def test_rejects_file_over_limit():
    data = b"x" * (6 * 1024 * 1024)  # 6 MiB
    with pytest.raises(HTTPException) as exc:
        await read_upload_capped(_FakeUpload(data), max_bytes=5 * 1024 * 1024)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_limit_is_inclusive_boundary():
    # Exactly at the limit is allowed; one byte over is not.
    limit = 2 * 1024 * 1024
    assert await read_upload_capped(_FakeUpload(b"x" * limit), max_bytes=limit) == b"x" * limit
    with pytest.raises(HTTPException):
        await read_upload_capped(_FakeUpload(b"x" * (limit + 1)), max_bytes=limit)
