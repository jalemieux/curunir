CREATE TABLE IF NOT EXISTS beta_signups (
  id          BIGSERIAL PRIMARY KEY,
  email       TEXT NOT NULL,
  message     TEXT,
  source      TEXT,
  ip          TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS beta_signups_email_idx
  ON beta_signups (LOWER(email));
