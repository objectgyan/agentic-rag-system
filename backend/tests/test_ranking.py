"""Tests for business/metadata re-ranking (item 4b). Pure, no infra."""

import pytest

from app.services.rag.ranking import BoostRule, MetadataBoostReranker, _normalize
from app.services.rag.retriever import RetrievedChunk


def _chunk(cid, score, **meta):
    return RetrievedChunk(chunk_id=cid, document_id="d", content=f"c{cid}", score=score, metadata=meta or None)


def test_normalize():
    assert _normalize([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]
    assert _normalize([3.0, 3.0]) == [0.0, 0.0]  # flat -> no signal
    assert _normalize([]) == []


def test_boostrule_from_dict():
    r = BoostRule.from_dict({"field": "mfr", "equals": "Sony", "weight": 0.3})
    assert r.field == "mfr" and r.equals == "Sony" and r.weight == 0.3 and r.numeric is False


@pytest.mark.asyncio
async def test_no_rules_orders_by_relevance():
    chunks = [_chunk("a", 0.2), _chunk("b", 0.9), _chunk("c", 0.5)]
    out = await MetadataBoostReranker([]).rerank("q", chunks, top_k=3)
    assert [c.chunk_id for c in out] == ["b", "c", "a"]


@pytest.mark.asyncio
async def test_categorical_boost_reorders():
    # 'a' is slightly less relevant, but a strong manufacturer boost pushes it to the top.
    chunks = [
        _chunk("a", 0.60, manufacturer="Sony"),
        _chunk("b", 0.65, manufacturer="Acme"),
        _chunk("c", 0.50, manufacturer="Acme"),
    ]
    rules = [{"field": "manufacturer", "equals": "Sony", "weight": 1.0}]
    out = await MetadataBoostReranker(rules).rerank("q", chunks, top_k=3)
    assert out[0].chunk_id == "a"  # boosted past the more-relevant 'b'


@pytest.mark.asyncio
async def test_categorical_boost_accepts_list_of_values():
    chunks = [_chunk("a", 0.5, brand="X"), _chunk("b", 0.9, brand="Z")]
    rules = [{"field": "brand", "equals": ["X", "Y"], "weight": 2.0}]
    out = await MetadataBoostReranker(rules).rerank("q", chunks, top_k=2)
    assert out[0].chunk_id == "a"  # 'X' is in the preferred set


@pytest.mark.asyncio
async def test_numeric_boost_normalizes():
    # equal relevance; popularity is the tiebreaker after min-max normalization.
    chunks = [
        _chunk("a", 0.5, popularity=10),
        _chunk("b", 0.5, popularity=1000),
        _chunk("c", 0.5, popularity=100),
    ]
    rules = [{"field": "popularity", "numeric": True, "weight": 1.0}]
    out = await MetadataBoostReranker(rules).rerank("q", chunks, top_k=3)
    assert [c.chunk_id for c in out] == ["b", "c", "a"]


@pytest.mark.asyncio
async def test_numeric_boost_tolerates_missing_or_bad_values():
    chunks = [_chunk("a", 0.5, popularity="not-a-number"), _chunk("b", 0.5)]  # b has no metadata
    rules = [{"field": "popularity", "numeric": True, "weight": 1.0}]
    out = await MetadataBoostReranker(rules).rerank("q", chunks, top_k=2)
    assert len(out) == 2  # no crash; both treated as 0.0 popularity


@pytest.mark.asyncio
async def test_top_k_truncates():
    chunks = [_chunk(str(i), 0.1 * i) for i in range(5)]
    out = await MetadataBoostReranker([]).rerank("q", chunks, top_k=2)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_empty_chunks():
    out = await MetadataBoostReranker([{"field": "x", "equals": 1, "weight": 1}]).rerank("q", [], top_k=5)
    assert out == []
