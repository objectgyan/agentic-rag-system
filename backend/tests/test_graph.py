"""Tests for knowledge-graph extraction and retrieval (A3)."""

import types

import pytest

from app.services.rag.graph import GraphService


def test_parse_extracts_triples_from_fenced_json():
    raw = '```json\n[{"subject":"Maria","predicate":"reports to","object":"Tom"}]\n```'
    assert GraphService._parse(raw) == [
        {"subject": "Maria", "predicate": "reports to", "object": "Tom"}
    ]


def test_parse_drops_incomplete_and_garbage():
    assert GraphService._parse("no json here at all") == []
    assert GraphService._parse('[{"subject": "a", "predicate": "b"}]') == []  # missing object


def _db_with_edges(edges):
    class _Result:
        def scalars(self):
            return types.SimpleNamespace(all=lambda: edges)

    class _DB:
        async def execute(self, stmt):
            return _Result()

    return _DB()


def _edge(s, p, o):
    return types.SimpleNamespace(subject=s, predicate=p, object=o)


@pytest.mark.asyncio
async def test_query_facts_expands_one_hop():
    edges = [
        _edge("Maria Chen", "reports to", "Tom Blake"),
        _edge("Tom Blake", "leads", "Engineering"),  # one hop from Maria via Tom
        _edge("Bob", "likes", "Coffee"),  # unrelated, must not appear
    ]
    facts = await GraphService().query_facts(_db_with_edges(edges), "What does Maria Chen do?", None)

    assert "Maria Chen reports to Tom Blake" in facts
    assert "Tom Blake leads Engineering" in facts  # surfaced via 1-hop expansion
    assert "Bob likes Coffee" not in facts


@pytest.mark.asyncio
async def test_query_facts_empty_when_no_entity_match():
    edges = [_edge("Maria Chen", "reports to", "Tom Blake")]
    facts = await GraphService().query_facts(_db_with_edges(edges), "totally unrelated question", None)
    assert facts == []
