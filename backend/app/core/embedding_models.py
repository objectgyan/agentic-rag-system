"""What we know about embedding models: output size and query/document prefixes.

Dependency-free so both ``config`` (startup validation) and the embedder can import it.

Prefixes matter for *asymmetric* models, which were trained to embed a short question and a
long passage differently: BGE wants an instruction on queries only, E5 wants "query: " /
"passage: ". Leaving them off still "works" but measurably lowers recall.
"""

from typing import Dict, Optional, Tuple

# model -> output dimensions
DIMENSIONS: Dict[str, int] = {
    # OpenAI
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Cohere
    "embed-english-v3.0": 1024,
    "embed-multilingual-v3.0": 1024,
    "embed-english-light-v3.0": 384,
    "embed-multilingual-light-v3.0": 384,
    "embed-v4.0": 1536,
    # Local (sentence-transformers) — free, run on the app's own CPU/GPU
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "intfloat/e5-small-v2": 384,
    "intfloat/e5-base-v2": 768,
    "intfloat/e5-large-v2": 1024,
    "intfloat/multilingual-e5-small": 384,
    "intfloat/multilingual-e5-base": 768,
    "intfloat/multilingual-e5-large": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-mpnet-base-v2": 768,
}

_BGE_QUERY = "Represent this sentence for searching relevant passages: "


def prefixes(model: str) -> Tuple[str, str]:
    """``(query_prefix, document_prefix)`` for a local model; empty strings when none apply."""
    if model.startswith("BAAI/bge-") and model.endswith("-en-v1.5"):
        return _BGE_QUERY, ""
    if model.startswith("intfloat/") and "e5" in model:
        return "query: ", "passage: "
    return "", ""


def known_dimensions(model: str) -> Optional[int]:
    return DIMENSIONS.get(model)
