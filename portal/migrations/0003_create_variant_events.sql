-- Top-of-funnel events for /v/<slug>/ positioning-test variants (#547).
-- No raw IPs or cookies: ip_hash is an HMAC-SHA256 of the client IP.
CREATE TABLE IF NOT EXISTS variant_events (
  id          BIGSERIAL PRIMARY KEY,
  variant     TEXT NOT NULL,
  event_type  TEXT NOT NULL CHECK (event_type IN ('page_view', 'cta_click')),
  ip_hash     TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS variant_events_variant_idx
  ON variant_events (variant, event_type);
