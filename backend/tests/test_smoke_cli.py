"""The provider smoke CLI, run offline against FakeLLM (its real job is live API calls)."""

import pytest

from app.cli import smoke
from app.core.config import settings
from app.llm import FakeLLM, use_llm


def _reply(call):
    prompt = str(call["messages"])
    if "pong" in prompt:
        return "pong"
    if "Count from 1 to 5" in prompt:
        return "1 2 3 4 5"
    return "Maria Chen led the Apollo project [Source 1]."


@pytest.fixture
def fake_embeddings(monkeypatch):
    from app.services.rag.embedder import EmbeddingService

    async def embed_query(self, query):
        return [0.0] * settings.default_embedding_dimensions

    monkeypatch.setattr(EmbeddingService, "embed_query", embed_query)


@pytest.mark.asyncio
async def test_all_checks_pass_against_a_working_model(fake_embeddings, capsys):
    with use_llm(FakeLLM(_reply)):
        code = await smoke.main_async(["fake-model"], agent=False)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "4/4 passed" in out


@pytest.mark.asyncio
async def test_a_broken_model_fails_without_hiding_the_others(fake_embeddings, capsys):
    with use_llm(FakeLLM(RuntimeError("401 invalid x-api-key"))):
        code = await smoke.main_async(["fake-model"], agent=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "[PASS] embed" in out
    assert "[FAIL] complete [fake-model]: RuntimeError: 401 invalid x-api-key" in out
    assert "1/4 passed" in out
