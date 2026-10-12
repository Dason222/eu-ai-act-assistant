# EU AI Act Q&A - one image for the API and the UI.
# The knowledge base (parsed text, chunks, embeddings) and the embedding model are built into the image,
# so containers start in ~2 s and need no outbound access except to the LLM provider.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AIACT_DATA_DIR=/app/data \
    AIACT_MODEL_CACHE_DIR=/app/models

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY app ./app
COPY .streamlit ./.streamlit
COPY data/raw ./data/raw
COPY data/eval ./data/eval
# parse + chunk + embed from the versioned EUR-Lex snapshot (downloads the ~130 MB ONNX model once)
RUN aiact-qa ingest

RUN useradd --create-home --uid 10001 aiact && chown -R aiact /app
USER aiact

# Default process = the chat UI on $PORT (8501 if unset), so the image also runs on single-port platforms
# (PaaS, AODE portal). docker-compose.yml starts the REST API from the same image with its own command.
ENV PORT=8501
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c \
    "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8501\")}/_stcore/health', timeout=4)" || exit 1
CMD ["sh", "-c", "exec streamlit run app/ui.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
