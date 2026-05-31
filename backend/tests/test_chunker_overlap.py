"""Regression test: chunk_overlap >= chunk_size must not infinite-loop (worker hang).

This previously hung the ingestion worker forever (the fixed-window step
chunk_size - chunk_overlap was <= 0, so the loop never advanced) — a DoS via
collection config. The constructor now clamps overlap to < size.
"""

from app.services.rag.chunker import ChunkingService


def test_overlap_larger_than_size_terminates():
    # overlap (50) > size (25): must not hang and must produce chunks.
    chunker = ChunkingService(strategy="fixed", chunk_size=25, chunk_overlap=50)
    chunks = chunker.chunk(" ".join(["word"] * 200))
    assert len(chunks) > 1
    assert all(c.content for c in chunks)


def test_overlap_equal_to_size_terminates():
    chunker = ChunkingService(strategy="fixed", chunk_size=30, chunk_overlap=30)
    chunks = chunker.chunk(" ".join(["token"] * 150))
    assert len(chunks) > 1


def test_overlap_is_clamped_below_size():
    chunker = ChunkingService(chunk_size=25, chunk_overlap=50)
    assert chunker.chunk_overlap < chunker.chunk_size
