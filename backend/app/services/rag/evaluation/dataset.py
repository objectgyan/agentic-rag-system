"""The golden dataset — the human-labeled cases the harness scores against.

A "golden set" is the ground truth: for each question, which chunks/documents *should* be
retrieved, and (optionally) what an ideal answer looks like. It's the single most valuable
artifact in a RAG project — everything downstream is measured against it. Keep it in version
control; grow it every time you find a query the system got wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union


@dataclass
class GoldenCase:
    """One evaluation case.

    - ``question``: the query to run.
    - ``relevant_chunk_ids`` / ``relevant_document_ids``: ids a human judged correct. Provide
      whichever granularity your labels are at; the runner prefers chunk-level, falls back to
      document-level. These drive the **retrieval** metrics.
    - ``ground_truth``: an ideal reference answer (optional) — enables answer-completeness scoring.
    - ``answer_must_contain``: cheap, deterministic substring checks the answer must include.
    - ``collection_ids``: restrict retrieval to these collections (optional).
    """

    question: str
    relevant_chunk_ids: List[str] = field(default_factory=list)
    relevant_document_ids: List[str] = field(default_factory=list)
    ground_truth: Optional[str] = None
    answer_must_contain: List[str] = field(default_factory=list)
    collection_ids: Optional[List[str]] = None
    id: Optional[str] = None

    @property
    def has_retrieval_labels(self) -> bool:
        return bool(self.relevant_chunk_ids or self.relevant_document_ids)


def load_golden_dataset(path: Union[str, Path]) -> List[GoldenCase]:
    """Load a golden dataset from JSON.

    Accepts either a top-level list of cases, or an object ``{"cases": [...]}``. Each case needs
    at least a ``question``; everything else is optional.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise ValueError("golden dataset must be a list of cases or an object with a 'cases' list")

    out: List[GoldenCase] = []
    for i, c in enumerate(cases):
        if "question" not in c:
            raise ValueError(f"case {i} is missing required field 'question'")
        out.append(
            GoldenCase(
                id=c.get("id") or f"case-{i + 1}",
                question=c["question"],
                relevant_chunk_ids=c.get("relevant_chunk_ids", []),
                relevant_document_ids=c.get("relevant_document_ids", []),
                ground_truth=c.get("ground_truth"),
                answer_must_contain=c.get("answer_must_contain", []),
                collection_ids=c.get("collection_ids"),
            )
        )
    return out
