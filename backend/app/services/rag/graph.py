"""Knowledge-graph extraction and retrieval (A3).

Ingestion (opt-in per collection) extracts (subject, predicate, object) triples from each
document with an LLM and stores them as graph_edges. At query time, query_facts finds the
entities mentioned in the question, then returns the 1-hop neighborhood of those entities
(every stored fact touching them) to augment generation — letting the model answer from
explicit relationships, not just nearby text.
"""

import json
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm_clients import openai_client
from app.models.graph import GraphEdge

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = (
    "Extract the key factual relationships from the text as (subject, predicate, object) "
    "triples. Return ONLY a JSON array of objects with keys 'subject', 'predicate', 'object'. "
    "Use concise canonical entity names. Extract at most 20 of the most important triples. "
    "If there are none, return []."
)


class GraphService:
    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_compression_model

    async def extract_triples(self, text: str) -> List[dict]:
        client = openai_client()
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": text[:6000]},
            ],
            temperature=0.0,
            max_tokens=1000,
        )
        return self._parse((response.choices[0].message.content or "").strip())

    @staticmethod
    def _parse(raw: str) -> List[dict]:
        # Be tolerant of code fences / preamble: extract the JSON array substring.
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        try:
            data = json.loads(raw[start : end + 1])
        except Exception:
            return []

        triples: List[dict] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            s, p, o = item.get("subject"), item.get("predicate"), item.get("object")
            if s and p and o:
                triples.append(
                    {"subject": str(s)[:500], "predicate": str(p)[:500], "object": str(o)[:500]}
                )
        return triples

    async def store(self, db: AsyncSession, tenant_id, document_id, collection_id, triples) -> int:
        for t in triples:
            db.add(
                GraphEdge(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    collection_id=collection_id,
                    subject=t["subject"],
                    predicate=t["predicate"],
                    object=t["object"],
                )
            )
        await db.flush()
        return len(triples)

    async def query_facts(
        self, db: AsyncSession, query: str, collection_ids: Optional[List[str]] = None, limit: int = 15
    ) -> List[str]:
        """Return facts in the 1-hop neighborhood of entities mentioned in the query."""
        stmt = select(GraphEdge)
        if collection_ids:
            stmt = stmt.where(GraphEdge.collection_id.in_(collection_ids))
        edges = (await db.execute(stmt.limit(1000))).scalars().all()
        if not edges:
            return []

        q = query.lower()
        # Seed entities: subjects/objects that appear in the question.
        seeds = {e.subject.lower() for e in edges if e.subject.lower() in q}
        seeds |= {e.object.lower() for e in edges if e.object.lower() in q}
        if not seeds:
            return []

        # Expand one hop: a seed's neighbors join the frontier, so we surface connected
        # facts (e.g. "X reports to Y" AND "Y leads Z"), not just facts naming a query entity.
        frontier = set(seeds)
        for e in edges:
            if e.subject.lower() in seeds:
                frontier.add(e.object.lower())
            if e.object.lower() in seeds:
                frontier.add(e.subject.lower())

        facts: List[str] = []
        seen = set()
        for e in edges:
            if e.subject.lower() in frontier or e.object.lower() in frontier:
                fact = f"{e.subject} {e.predicate} {e.object}"
                if fact not in seen:
                    seen.add(fact)
                    facts.append(fact)
            if len(facts) >= limit:
                break
        return facts
