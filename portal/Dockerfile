FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . ./portal/

# Drop root. The portal is internet-facing and holds no local state — nothing
# it does at runtime needs write access outside /tmp, so there is no bind-mount
# UID caveat here (unlike the curunir container).
RUN useradd -m -s /usr/sbin/nologin portal && chown -R portal:portal /app
USER portal

ENV PORT=8000
CMD ["sh", "-c", "uvicorn portal.app:app --host 0.0.0.0 --port ${PORT}"]
