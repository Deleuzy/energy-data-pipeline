FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
RUN pip install --no-cache-dir -e .
EXPOSE 8000
# Fetch, validate, store and report once, then serve the API on :8000.
CMD ["sh", "-c", "edp run && uvicorn edp.api:app --host 0.0.0.0 --port 8000"]
