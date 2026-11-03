FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev tesseract-ocr ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# sentence-transformers pulls in torch, and PyPI's Linux torch is the CUDA build (several GB of
# NVIDIA libraries). Install the CPU build first so the requirement is already satisfied; build
# with --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 for a GPU image.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url ${TORCH_INDEX_URL}

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

# Run as a non-root user (O4). Dependencies are already installed system-wide as root;
# the app process itself needs no write access to the image.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app \
    && mkdir -p /models && chown appuser:appuser /models
# HF_HOME: where local embedding/re-ranking models are cached. docker-compose mounts a named
# volume here (initialized with this directory's ownership), so models download once.
ENV HF_HOME=/models
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
