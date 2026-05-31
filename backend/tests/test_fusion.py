"""Tests for Reciprocal Rank Fusion in the hybrid retriever (O5)."""

from app.services.rag.retriever import HybridRetriever, RetrievedChunk


def _chunk(cid):
    return RetrievedChunk(chunk_id=cid, document_id="d", content="c", score=0.0)


def _retriever():
    # embedding_service is supplied so __init__ doesn't construct a real one.
    return HybridRetriever(db=None, tenant_id="t", embedding_service=object())


def test_rrf_ranks_items_in_both_lists_highest():
    r = _retriever()
    dense = [_chunk("a"), _chunk("b")]
    sparse = [_chunk("b"), _chunk("c")]

    fused = r._rrf_fusion(dense, sparse, dense_weight=0.7, sparse_weight=0.3)
    ids = [c.chunk_id for c in fused]

    # "b" appears in both rankings, so RRF should rank it first.
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_rrf_deduplicates():
    r = _retriever()
    dense = [_chunk("a"), _chunk("a")]
    sparse = [_chunk("a")]
    fused = r._rrf_fusion(dense, sparse, dense_weight=0.5, sparse_weight=0.5)
    assert [c.chunk_id for c in fused] == ["a"]


def test_rrf_handles_empty_inputs():
    r = _retriever()
    assert r._rrf_fusion([], [], 0.7, 0.3) == []
