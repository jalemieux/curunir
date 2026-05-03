FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .
ENV PORT=8000
CMD ["sh", "-c", "uvicorn portal.app:app --host 0.0.0.0 --port ${PORT}"]
