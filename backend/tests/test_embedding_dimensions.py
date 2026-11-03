"""Configurable embedding size: model metadata, prefixes, schema statements, and guards."""

import numpy as np
import pytest
from fastapi import HTTPException

from app.core import embedding_models
from app.core.config import Settings, settings
from app.services.rag import embedder as embedder_mod
from app.services.rag import vector_schema
from app.services.rag.embedder import EmbeddingService


def test_settings_reject_a_size_that_does_not_match_the_model():
    with pytest.raises(ValueError, match="DEFAULT_EMBEDDING_DIMENSIONS=384"):
        Settings(default_embedding_model="BAAI/bge-small-en-v1.5", default_embedding_dimensions=1536)
    ok = Settings(default_embedding_model="BAAI/bge-small-en-v1.5", default_embedding_dimensions=384)
    assert ok.default_embedding_dimensions == 384
    # Unknown (e.g. custom fine-tuned) models are trusted to match the configured size.
    Settings(default_embedding_model="acme/custom-embedder", default_embedding_dimensions=512)


def test_prefixes_for_asymmetric_models():
    q, d = embedding_models.prefixes("BAAI/bge-small-en-v1.5")
    assert q.startswith("Represent this sentence") and d == ""
    assert embedding_models.prefixes("intfloat/multilingual-e5-base") == ("query: ", "passage: ")
    assert embedding_models.prefixes("sentence-transformers/all-MiniLM-L6-v2") == ("", "")


@pytest.mark.asyncio
async def test_local_embedder_applies_query_and_document_prefixes(monkeypatch):
    seen = []

    class _Model:
        def encode(self, texts, normalize_embeddings):
            seen.append(list(texts))
            return np.zeros((len(texts), 384))

    monkeypatch.setitem(embedder_mod._local_models, "intfloat/e5-small-v2", _Model())
    monkeypatch.setattr(settings, "embedding_cache_enabled", False)
    svc = EmbeddingService(model="intfloat/e5-small-v2")
    await svc.embed_texts(["a chunk"])
    await svc.embed_query("a question")
    assert seen == [["passage: a chunk"], ["query: a question"]]
    assert svc.dimensions == 384


def test_resize_statements():
    stmts = vector_schema.resize_statements(384)
    assert stmts[0] == "DROP INDEX IF EXISTS ix_chunks_embedding"
    assert "TYPE vector(384) USING NULL" in stmts[1]
    assert "USING hnsw" in stmts[2]
    assert vector_schema.resize_warning(384) is None


def test_resize_beyond_hnsw_limit_skips_the_index_and_warns():
    stmts = vector_schema.resize_statements(3072)
    assert not any("hnsw" in s for s in stmts)
    assert "HNSW limit" in vector_schema.resize_warning(3072)


@pytest.mark.parametrize("bad", [0, -5, "384", 3.5])
def test_resize_rejects_invalid_sizes(bad):
    # The value is interpolated into DDL, so anything but a positive int is refused outright.
    with pytest.raises(ValueError):
        vector_schema.resize_statements(bad)


@pytest.mark.asyncio
async def test_collection_with_a_different_embedding_model_is_rejected():
    from app.api.v1.collections import create_collection
    from app.schemas.collection import CollectionCreate

    with pytest.raises(HTTPException) as exc:
        await create_collection(CollectionCreate(name="c", embedding_model="some-other-model"), user=None, db=None)
    assert exc.value.status_code == 422


def test_cli_resize_requires_confirmation(capsys):
    from app.cli.embeddings import main

    assert main(["resize"]) == 2
    assert "--yes" in capsys.readouterr().out
