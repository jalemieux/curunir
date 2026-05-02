CREATE TABLE IF NOT EXISTS users (
  id              BIGSERIAL PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE,
  sign_in_token   TEXT NOT NULL UNIQUE,
  container_token TEXT NOT NULL UNIQUE,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS users_sign_in_token_idx
  ON users (sign_in_token) WHERE is_active;
CREATE INDEX IF NOT EXISTS users_container_token_idx
  ON users (container_token) WHERE is_active;
