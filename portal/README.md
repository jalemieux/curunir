# Curunir Portal

Hosted multi-user chat surface for curunir.

## Local development

```bash
cd portal
docker compose up -d   # local Postgres
pip install -e ".[dev]"
uvicorn portal.app:app --reload
```

## Tests

```bash
docker compose up -d
pytest
```

## Deploy

Render auto-deploys from the linked branch using `render.yaml`.
