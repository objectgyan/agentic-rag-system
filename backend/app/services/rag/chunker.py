"""Document chunking strategies."""

from typing import List, Optional
from dataclasses import dataclass
import re
import tiktoken


@dataclass
class TextChunk:
    content: str
    index: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    parent_content: Optional[str] = None
    token_count: int = 0


class ChunkingService:
    """Multiple chunking strategies for document processing."""

    def __init__(self, strategy: str = "semantic", chunk_size: int = 512, chunk_overlap: int = 50):
        self.strategy = strategy
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def chunk(self, text: str, page_numbers: Optional[List[int]] = None) -> List[TextChunk]:
        """Chunk text using the configured strategy."""
        strategies = {
            "fixed": self._fixed_chunk,
            "semantic": self._semantic_chunk,
            "recursive": self._recursive_chunk,
            "paragraph": self._paragraph_chunk,
            "parent_child": self._parent_child_chunk,
        }
        fn = strategies.get(self.strategy, self._semantic_chunk)
        chunks = fn(text)

        # Count tokens
        for chunk in chunks:
            chunk.token_count = len(self.tokenizer.encode(chunk.content))

        return chunks

    def _fixed_chunk(self, text: str) -> List[TextChunk]:
        """Fixed-size token chunking with overlap."""
        tokens = self.tokenizer.encode(text)
        chunks = []
        i = 0
        idx = 0
        while i < len(tokens):
            end = min(i + self.chunk_size, len(tokens))
            chunk_tokens = tokens[i:end]
            content = self.tokenizer.decode(chunk_tokens)
            chunks.append(TextChunk(content=content.strip(), index=idx))
            idx += 1
            i += self.chunk_size - self.chunk_overlap
        return chunks

    def _semantic_chunk(self, text: str) -> List[TextChunk]:
        """Semantic chunking based on natural boundaries (paragraphs, sentences)."""
        paragraphs = re.split(r"\n\s*\n", text)
        chunks = []
        current = ""
        idx = 0
        section_title = None

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Detect section headers
            if re.match(r"^#{1,6}\s+", para) or (len(para) < 100 and para.isupper()):
                section_title = para.lstrip("#").strip()

            test = f"{current}\n\n{para}" if current else para
            if len(self.tokenizer.encode(test)) > self.chunk_size and current:
                chunks.append(TextChunk(
                    content=current.strip(), index=idx, section_title=section_title
                ))
                idx += 1
                # Overlap: include last sentence of previous chunk
                sentences = re._split_pattern(r"(?<=[.!?])\s+", current)
                overlap = sentences[-1] if sentences else ""
                current = f"{overlap}\n\n{para}" if overlap else para
            else:
                current = test

        if current.strip():
            chunks.append(TextChunk(content=current.strip(), index=idx, section_title=section_title))

        return chunks

    def _recursive_chunk(self, text: str) -> List[TextChunk]:
        """Recursive splitting: try large separators first, then smaller ones."""
        separators = ["\n\n\n", "\n\n", "\n", ". ", " "]
        return self._recursive_split(text, separators, 0)

    def _recursive_split(self, text: str, separators: List[str], start_idx: int) -> List[TextChunk]:
        chunks = []
        if not separators:
            chunks.append(TextChunk(content=text.strip(), index=start_idx))
            return chunks

        sep = separators[0]
        parts = text.split(sep)
        current = ""

        for part in parts:
            test = f"{current}{sep}{part}" if current else part
            if len(self.tokenizer.encode(test)) > self.chunk_size:
                if current:
                    chunks.append(TextChunk(content=current.strip(), index=start_idx + len(chunks)))
                if len(self.tokenizer.encode(part)) > self.chunk_size:
                    sub_chunks = self._recursive_split(part, separators[1:], start_idx + len(chunks))
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = part
            else:
                current = test

        if current.strip():
            chunks.append(TextChunk(content=current.strip(), index=start_idx + len(chunks)))

        return chunks

    def _paragraph_chunk(self, text: str) -> List[TextChunk]:
        """One chunk per paragraph, merging small ones."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks = []
        current = ""
        idx = 0

        for para in paragraphs:
            if current and len(self.tokenizer.encode(f"{current}\n\n{para}")) > self.chunk_size:
                chunks.append(TextChunk(content=current, index=idx))
                idx += 1
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para

        if current:
            chunks.append(TextChunk(content=current, index=idx))

        return chunks

    def _parent_child_chunk(self, text: str) -> List[TextChunk]:
        """Parent-child chunking: large parent chunks with smaller child chunks."""
        parent_size = self.chunk_size * 3
        child_size = self.chunk_size

        # Create parent chunks
        parent_chunks = []
        tokens = self.tokenizer.encode(text)
        i = 0
        while i < len(tokens):
            end = min(i + parent_size, len(tokens))
            parent_content = self.tokenizer.decode(tokens[i:end])
            parent_chunks.append(parent_content)
            i += parent_size - self.chunk_overlap

        # Create child chunks from each parent
        all_chunks = []
        idx = 0
        for parent_content in parent_chunks:
            parent_tokens = self.tokenizer.encode(parent_content)
            j = 0
            while j < len(parent_tokens):
                end = min(j + child_size, len(parent_tokens))
                child_content = self.tokenizer.decode(parent_tokens[j:end])
                all_chunks.append(TextChunk(
                    content=child_content.strip(),
                    index=idx,
                    parent_content=parent_content.strip(),
                ))
                idx += 1
                j += child_size - (self.chunk_overlap // 2)

        return all_chunks
