"""Tests for the evaluation harness's pure retrieval metrics + dataset loader.

No DB, no LLM, no network — these are the deterministic core of the quality loop and must be
rock-solid, since every "did my change help?" decision leans on them.
"""

import json
import math

import pytest

from app.services.rag.evaluation.dataset import GoldenCase, load_golden_dataset
from app.services.rag.evaluation.metrics import (
    hit_at_k,
    mean_scores,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    retrieval_scores,
)

# retrieved best-first: a, b, c, d, e ; relevant = {c, e}
RETRIEVED = ["a", "b", "c", "d", "e"]
RELEVANT = {"c", "e"}


def test_hit_at_k():
    assert hit_at_k(RETRIEVED, RELEVANT, k=2) == 0.0   # a, b — no relevant yet
    assert hit_at_k(RETRIEVED, RELEVANT, k=3) == 1.0   # c is in
    assert hit_at_k(RETRIEVED, set(), k=3) == 0.0      # no relevant labels -> 0


def test_recall_at_k():
    assert recall_at_k(RETRIEVED, RELEVANT, k=3) == pytest.approx(0.5)   # got c of {c,e}
    assert recall_at_k(RETRIEVED, RELEVANT, k=5) == pytest.approx(1.0)   # got both
    assert recall_at_k(RETRIEVED, RELEVANT, k=2) == pytest.approx(0.0)   # got neither


def test_precision_at_k():
    # top-3 = a,b,c -> 1 relevant of 3
    assert precision_at_k(RETRIEVED, RELEVANT, k=3) == pytest.approx(1 / 3)
    # top-5 = all -> 2 relevant of 5
    assert precision_at_k(RETRIEVED, RELEVANT, k=5) == pytest.approx(2 / 5)


def test_precision_divides_by_actual_when_fewer_than_k():
    # only 2 retrieved but k=5 -> divide by 2, not 5
    assert precision_at_k(["c", "x"], RELEVANT, k=5) == pytest.approx(0.5)


def test_reciprocal_rank():
    # first relevant (c) is at rank 3 -> 1/3
    assert reciprocal_rank(RETRIEVED, RELEVANT) == pytest.approx(1 / 3)
    # a relevant item first -> 1.0
    assert reciprocal_rank(["e", "a"], RELEVANT) == pytest.approx(1.0)
    # none present -> 0.0
    assert reciprocal_rank(["a", "b"], RELEVANT) == pytest.approx(0.0)
    # k cutoff excludes the only relevant hit
    assert reciprocal_rank(RETRIEVED, RELEVANT, k=2) == pytest.approx(0.0)


def test_ndcg_rewards_higher_rank():
    # same relevant set, but ranked higher should score higher
    high = ndcg_at_k(["c", "e", "a"], RELEVANT, k=3)
    low = ndcg_at_k(["a", "c", "e"], RELEVANT, k=3)
    assert high > low
    # perfect ranking -> 1.0
    assert ndcg_at_k(["c", "e", "a", "b"], RELEVANT, k=4) == pytest.approx(1.0)


def test_ndcg_matches_hand_computation():
    # retrieved a,b,c with relevant={c}: hit at position 3 -> DCG = 1/log2(4)
    # ideal: relevant at position 1 -> IDCG = 1/log2(2) = 1
    expected = (1 / math.log2(4)) / (1 / math.log2(2))
    assert ndcg_at_k(["a", "b", "c"], {"c"}, k=3) == pytest.approx(expected)


def test_retrieval_scores_bundle_keys():
    scores = retrieval_scores(RETRIEVED, RELEVANT, k=3)
    assert set(scores) == {"hit@3", "recall@3", "precision@3", "mrr", "ndcg@3"}


def test_empty_relevant_is_zero_not_crash():
    for fn in (hit_at_k, recall_at_k, ndcg_at_k):
        assert fn(RETRIEVED, [], k=3) == 0.0


def test_mean_scores_averages_and_tolerates_missing_keys():
    means = mean_scores([{"recall@3": 1.0, "mrr": 0.5}, {"recall@3": 0.0}])
    assert means["recall@3"] == pytest.approx(0.5)
    assert means["mrr"] == pytest.approx(0.5)  # only one case had it
    assert mean_scores([]) == {}


def test_dataset_loader_roundtrip(tmp_path):
    data = {
        "cases": [
            {"question": "q1", "relevant_chunk_ids": ["x"], "answer_must_contain": ["foo"]},
            {"id": "custom", "question": "q2", "relevant_document_ids": ["d1"]},
        ]
    }
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    cases = load_golden_dataset(path)
    assert len(cases) == 2
    assert cases[0].id == "case-1"            # auto-assigned
    assert cases[0].has_retrieval_labels
    assert cases[1].id == "custom"            # explicit id preserved
    assert cases[1].relevant_document_ids == ["d1"]


def test_dataset_loader_accepts_bare_list(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(json.dumps([{"question": "q1"}]), encoding="utf-8")
    cases = load_golden_dataset(path)
    assert len(cases) == 1
    assert not cases[0].has_retrieval_labels


def test_dataset_loader_rejects_missing_question(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"relevant_chunk_ids": ["x"]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="question"):
        load_golden_dataset(path)


def test_golden_case_defaults():
    c = GoldenCase(question="hi")
    assert c.relevant_chunk_ids == []
    assert not c.has_retrieval_labels
