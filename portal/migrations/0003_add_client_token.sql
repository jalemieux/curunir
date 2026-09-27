ALTER TABLE users ADD COLUMN IF NOT EXISTS client_token TEXT UNIQUE;

CREATE INDEX IF NOT EXISTS users_client_token_idx
  ON users (client_token) WHERE is_active;
