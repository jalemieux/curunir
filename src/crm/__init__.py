"""Deterministic mini-CRM store for the marketing persona.

Mirrors the portfolio package tier-for-tier (db + engine), parameterized to a
``crm`` domain. Pure SQLite + stdlib, zero agent imports — independently
testable. All writes go only through ``engine``."""
