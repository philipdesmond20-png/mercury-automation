-- ============================================================
-- Mercury POS — Database Schema
-- SQLite (upgrade to Postgres by changing connection string)
-- ============================================================

-- Stores
CREATE TABLE IF NOT EXISTS stores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,  -- Texaco, Dalton, Rome KS3
    username    TEXT NOT NULL,
    dba         TEXT,                  -- K S 3, DALTON, TEXACO
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Daily shifts (one row per store per day)
CREATE TABLE IF NOT EXISTS shifts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        INTEGER REFERENCES stores(id),
    shift_date      TEXT NOT NULL,     -- YYYY-MM-DD
    display_date    TEXT,              -- MM/DD/YYYY
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(store_id, shift_date)
);

-- Fuel summary
CREATE TABLE IF NOT EXISTS fuel_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id) UNIQUE,
    regular_gal     REAL,
    regular_amt     REAL,
    plus_gal        REAL,
    plus_amt        REAL,
    super_gal       REAL,
    super_amt       REAL,
    diesel_gal      REAL,
    diesel_amt      REAL,
    total_gal       REAL,
    total_amt       REAL,
    raw_json        TEXT
);

-- Inside sales / grocery
CREATE TABLE IF NOT EXISTS inside_sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id),
    category        TEXT NOT NULL,     -- CIGS, BEER, CANDY etc
    quantity        INTEGER,
    amount          REAL,
    UNIQUE(shift_id, category)
);

-- Lottery
CREATE TABLE IF NOT EXISTS lottery (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id) UNIQUE,
    online_sales    REAL,
    instant_sales   REAL,
    online_payout   REAL,
    instant_payout  REAL,
    total_sales     REAL,
    total_payout    REAL,
    net             REAL,
    raw_json        TEXT
);

-- Tenders / payment methods
CREATE TABLE IF NOT EXISTS tenders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id) UNIQUE,
    credit          REAL,
    debit           REAL,
    cash            REAL,
    mobile          REAL,
    food_stamp      REAL,
    coupon          REAL,
    manual_card     REAL,
    manual_debit    REAL,
    total           REAL,
    raw_json        TEXT
);

-- Cash drops
CREATE TABLE IF NOT EXISTS cash_drops (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id),
    cashier         TEXT,
    amount          REAL,
    drop_time       TEXT,
    raw_json        TEXT
);

-- Tax summary
CREATE TABLE IF NOT EXISTS tax_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id) UNIQUE,
    high_tax        REAL,
    low_tax         REAL,
    no_tax          REAL,
    total_tax       REAL,
    raw_json        TEXT
);

-- Financial daily summary (top level)
CREATE TABLE IF NOT EXISTS financials (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id            INTEGER REFERENCES shifts(id) UNIQUE,
    total_sales         REAL,
    fuel_sales          REAL,
    inside_sales        REAL,
    lottery_net         REAL,
    cash_drop           REAL,
    over_short          REAL,
    total_payment       REAL,
    raw_json            TEXT
);

-- Store expenses
CREATE TABLE IF NOT EXISTS expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id),
    vendor          TEXT,
    amount          REAL,
    payment_type    TEXT,
    check_number    TEXT
);

-- Exceptions / error corrects / voids
CREATE TABLE IF NOT EXISTS exceptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id),
    type            TEXT,   -- VOID, ERROR_CORRECT, NO_SALE, SUSPEND
    count           INTEGER,
    total_amount    REAL,
    raw_json        TEXT
);

-- Raw API responses (for debugging and reprocessing)
CREATE TABLE IF NOT EXISTS raw_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER REFERENCES shifts(id),
    endpoint        TEXT NOT NULL,
    response_text   TEXT,
    fetched_at      TEXT DEFAULT (datetime('now'))
);

-- Crawler run log
CREATE TABLE IF NOT EXISTS crawler_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,
    store_id        INTEGER REFERENCES stores(id),
    status          TEXT,   -- success, failed, partial
    endpoints_hit   INTEGER,
    error_message   TEXT,
    duration_secs   REAL,
    started_at      TEXT DEFAULT (datetime('now'))
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_shifts_date    ON shifts(shift_date);
CREATE INDEX IF NOT EXISTS idx_shifts_store   ON shifts(store_id);
CREATE INDEX IF NOT EXISTS idx_inside_shift   ON inside_sales(shift_id);
CREATE INDEX IF NOT EXISTS idx_expenses_shift ON expenses(shift_id);
CREATE INDEX IF NOT EXISTS idx_runs_date      ON crawler_runs(run_date);
