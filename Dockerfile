FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORAGE_PROFILE=demo \
    PYTHONPATH=/app

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

COPY src/ ./src/
COPY lightrag_index/ ./lightrag_index/

# Make Streamlit find config.toml at CWD/.streamlit/
RUN mkdir -p .streamlit && cp src/ui/.streamlit/config.toml .streamlit/config.toml

EXPOSE 7860

CMD ["uv", "run", "streamlit", "run", "src/ui/streamlit_app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
