-- Canonical portfolio fixture for the finance eval "C" suite.
--
-- Synthetic-but-structurally-faithful, values FROZEN (a balance-sheet benchmark
-- must be reproducible; the agent uses stored values, never re-fetches live).
-- Numbers are invented so this can live in git without exposing real holdings,
-- but the SHAPE mirrors the real portfolio: multiple brokerage + IRA + 401k + PE
-- accounts, two properties with mortgages, a watch collection with per-piece
-- basis + dates, physical gold, cash, and a line of credit.
--
-- This is the single source of truth shared by two consumers (per the A/B/D
-- coordination note): the agent's portfolio ENGINE (tables + views) and the eval
-- ANCHOR (`_networth.py`, which queries the SAME views). The VIEWS are the
-- contract — agent and grader read identical views over identical data, so
-- neither can drift. The A/B/D engine builds `context/memory/portfolio.db` from
-- this seed; `_networth.py` queries that store's views directly.

CREATE TABLE assets (
  id         INTEGER PRIMARY KEY,
  class      TEXT NOT NULL,   -- equity | physical | cash | collectible | real_estate
  label      TEXT NOT NULL,
  account    TEXT,
  qty        REAL,
  cost_basis REAL,            -- NULL = not recorded (P2/T5 probe this gap)
  acquired   TEXT,            -- ISO date, or NULL when unrecorded
  value      REAL NOT NULL
);

CREATE TABLE liabilities (
  id       INTEGER PRIMARY KEY,
  label    TEXT NOT NULL,
  balance  REAL NOT NULL,
  apr      REAL,
  type     TEXT,              -- mortgage | unsecured
  property TEXT               -- for a mortgage: the assets.label it is secured against
);

INSERT INTO assets (class, label, account, qty, cost_basis, acquired, value) VALUES
  ('equity',      'VTI',                          'Brokerage-A',   800,  180000, '2021-03-15', 312000),
  ('equity',      'NVDA',                         'Brokerage-A',   300,   36000, '2023-06-01',  54000),
  ('cash',        'Cash',                         'Brokerage-A',  NULL,    NULL, NULL,          41000),
  ('equity',      'GLD',                          'Brokerage-B',   400,  120000, '2022-01-10', 168000),
  ('equity',      'AMZN',                         'Brokerage-B',   150,   24000, '2024-02-20',  31500),
  ('cash',        'Cash',                         'Brokerage-B',  NULL,    NULL, NULL,            600),
  ('equity',      'VTI',                          'IRA',       500,  150000, '2020-05-05', 195000),
  ('equity',      'LLY',                          'IRA',        60,   66000, '2025-11-01',  72000),
  ('cash',        'Cash',                         'IRA',      NULL,    NULL, NULL,            500),
  ('equity',      'Vanguard 500 Index Trust',     'Employer-401k', NULL,  140000, '2018-01-01', 210000),
  ('equity',      'Target Retirement 2045 Trust', 'Employer-401k', NULL,   50000, '2018-01-01',  62000),
  ('equity',      'Blackstone Private Credit (BCRED)', 'Alt-Fund', NULL, 12000, '2024-09-01', 12000),
  ('physical',    'Physical gold bullion',        'Home safe',        9,   31500, '2023-08-15',  40500),
  ('collectible', 'sunburst archtop guitar',                'Watch collection', 1,   22000, '2021-05-01',  28500),
  ('collectible', 'parlor acoustic guitar',   'Watch collection', 1,    4200, '2022-03-01',   4700),
  ('collectible', 'blue solidbody guitar',   'Watch collection', 1,   11800, '2022-09-01',  15800),
  ('collectible', 'red solidbody guitar',    'Watch collection', 1,   21000, '2020-11-01',  29500),
  ('collectible', 'black hollowbody guitar',        'Watch collection', 1,    NULL, NULL,          12569),
  ('real_estate', 'Primary Residence (primary residence)', 'Real estate', NULL, 1900000, '2019-07-01', 3000000),
  ('real_estate', 'Rental Property (rental)',          'Real estate',   NULL,    NULL, NULL,        1400000);

INSERT INTO liabilities (label, balance, apr, type, property) VALUES
  ('Primary Residence mortgage', 1100000, 2.625, 'mortgage',  'Primary Residence (primary residence)'),
  ('Rental Property mortgage',    360000, 3.0,   'mortgage',  'Rental Property (rental)'),
  ('Line of credit',    95000, 7.5,   'unsecured', NULL);

-- ── Views: the canonical decompositions (the shared agent⋈grader contract) ──
-- Convention: the equities bucket folds in physical commodities for a coarse
-- net-worth split (T2 separately checks the agent distinguishes the GLD ETF from
-- physical bullion in prose). Mortgages are netted inside real-estate equity, so
-- the debt bucket is NON-mortgage liabilities only. rollup.total == networth.net_worth.

CREATE VIEW v_networth AS
SELECT
  (SELECT COALESCE(SUM(value),   0) FROM assets)      AS assets,
  (SELECT COALESCE(SUM(balance), 0) FROM liabilities) AS liabilities,
  (SELECT COALESCE(SUM(value),   0) FROM assets)
    - (SELECT COALESCE(SUM(balance), 0) FROM liabilities) AS net_worth;

CREATE VIEW v_rollup_by_class AS
WITH r AS (
  SELECT
    (SELECT COALESCE(SUM(value),0) FROM assets WHERE class IN ('equity','physical')) AS equities,
    (SELECT COALESCE(SUM(value),0) FROM assets WHERE class='real_estate')
      - (SELECT COALESCE(SUM(balance),0) FROM liabilities WHERE type='mortgage')     AS real_estate_equity,
    (SELECT COALESCE(SUM(value),0) FROM assets WHERE class='collectible')            AS collectibles,
    (SELECT COALESCE(SUM(value),0) FROM assets WHERE class='cash')                   AS cash,
    (SELECT COALESCE(SUM(balance),0) FROM liabilities WHERE type IS NULL OR type<>'mortgage') AS debt
)
SELECT equities, real_estate_equity, collectibles, cash, debt,
       equities + real_estate_equity + collectibles + cash - debt AS total
FROM r;

CREATE VIEW v_collectibles_pnl AS
SELECT
  COALESCE(SUM(value), 0)                                                AS value,
  COALESCE(SUM(CASE WHEN cost_basis IS NOT NULL THEN cost_basis END), 0) AS cost_basis,
  COALESCE(SUM(CASE WHEN cost_basis IS NOT NULL THEN value - cost_basis END), 0) AS unrealized_gain,
  SUM(CASE WHEN cost_basis IS NULL THEN 1 ELSE 0 END)                    AS missing_basis_count
FROM assets WHERE class='collectible';
