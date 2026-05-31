"""Tests for chunking strategies."""

from app.services.rag.chunker import ChunkingService


def test_fixed_chunking():
    """Test fixed-size chunking."""
    chunker = ChunkingService(strategy="fixed", chunk_size=50, chunk_overlap=10)
    text = " ".join(["word"] * 200)  # ~200 tokens
    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.strip()
        assert chunk.token_count > 0


def test_semantic_chunking():
    """Test semantic paragraph-based chunking."""
    chunker = ChunkingService(strategy="semantic", chunk_size=100, chunk_overlap=10)
    text = "\n\n".join([f"This is paragraph {i}. " * 10 for i in range(10)])
    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.strip()


def test_recursive_chunking():
    """Test recursive splitting."""
    chunker = ChunkingService(strategy="recursive", chunk_size=50, chunk_overlap=10)
    text = "\n\n\n".join(["Section content. " * 20 for _ in range(5)])
    chunks = chunker.chunk(text)

    assert len(chunks) > 1


def test_parent_child_chunking():
    """Test parent-child chunking produces child chunks with parent refs."""
    chunker = ChunkingService(strategy="parent_child", chunk_size=50, chunk_overlap=10)
    text = " ".join(["The quick brown fox jumps over the lazy dog."] * 50)
    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    # At least some should have parent content
    has_parent = any(c.parent_content for c in chunks)
    assert has_parent


def test_empty_text():
    """Test chunking handles empty input."""
    chunker = ChunkingService()
    chunks = chunker.chunk("")
    assert len(chunks) == 0 or (len(chunks) == 1 and chunks[0].content == "")
