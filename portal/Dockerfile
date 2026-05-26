FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . ./portal/
ENV PORT=8000
CMD ["sh", "-c", "uvicorn portal.app:app --host 0.0.0.0 --port ${PORT}"]
