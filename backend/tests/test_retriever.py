"""Tests for the hybrid retriever's SQL construction (F4).

Verifies the dense (pgvector) search binds the query embedding as a parameter
rather than interpolating it into the SQL text. Uses fakes for the embedder and
session, so no database or embedding API is required.
"""

import uuid
import pytest
from app.services.rag.retriever import HybridRetriever


class _FakeEmbedder:
    async def embed_query(self, query):
        return [0.1, 0.2, 0.3]


class _FakeResult:
    def fetchall(self):
        return []


class _CaptureSession:
    def __init__(self):
        self.last_sql = None
        self.last_params = None

    async def execute(self, statement, params=None):
        self.last_sql = str(statement)
        self.last_params = params
        return _FakeResult()


@pytest.mark.asyncio
async def test_dense_search_binds_embedding_as_parameter():
    session = _CaptureSession()
    retriever = HybridRetriever(
        db=session, tenant_id=str(uuid.uuid4()), embedding_service=_FakeEmbedder()
    )

    await retriever._dense_search("a query", collection_ids=None, top_k=5)

    # The embedding travels as a bound parameter...
    assert session.last_params["embedding"] == "[0.1,0.2,0.3]"
    assert ":embedding" in session.last_sql
    # ...and the raw vector values are NOT baked into the SQL string.
    assert "0.1,0.2,0.3" not in session.last_sql


@pytest.mark.asyncio
async def test_dense_search_passes_collection_filter_as_param():
    session = _CaptureSession()
    retriever = HybridRetriever(
        db=session, tenant_id=str(uuid.uuid4()), embedding_service=_FakeEmbedder()
    )
    coll_ids = [str(uuid.uuid4())]

    await retriever._dense_search("q", collection_ids=coll_ids, top_k=3)

    assert session.last_params["collection_ids"] == coll_ids
    assert session.last_params["top_k"] == 3
