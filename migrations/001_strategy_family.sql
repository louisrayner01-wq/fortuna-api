-- Migration 001: add strategy_family scoping to bot_configs and trades.
--
-- Adds a `strategy_family` column to both tables so a user can run Strat 1
-- and Portfolio Strategy independently (each with its own capital, equity,
-- HWM, is_active, trades log). Existing rows are backfilled to 'strat_1'
-- so pre-migration data continues to belong to the original strategy.
--
-- SAFE to run against a live database: additive columns with defaults,
-- unique-constraint swap is atomic in a transaction.
--
-- Run once against Railway Postgres BEFORE deploying the new API code.
-- If the API deploys first, requests will 500 because the ORM expects
-- columns that don't exist yet.
--
-- Usage from Railway:
--   1. Open the Postgres service → "Data" tab → "Query"
--   2. Paste this whole file, run it
--   3. Verify with the SELECTs at the bottom
--
-- Or from a psql shell:
--   psql "$DATABASE_URL" -f migrations/001_strategy_family.sql

BEGIN;

-- 1. Add strategy_family to bot_configs. Existing rows all become 'strat_1'.
ALTER TABLE bot_configs
    ADD COLUMN IF NOT EXISTS strategy_family VARCHAR NOT NULL DEFAULT 'strat_1';

-- 2. Swap the unique constraint: (user_id) → (user_id, strategy_family).
--    The old constraint was named `bot_configs_user_id_key` by SQLAlchemy.
--    Drop-if-exists is defensive in case an earlier attempt already ran.
ALTER TABLE bot_configs
    DROP CONSTRAINT IF EXISTS bot_configs_user_id_key;

ALTER TABLE bot_configs
    ADD CONSTRAINT uq_bot_config_user_family
    UNIQUE (user_id, strategy_family);

-- Indexes to keep per-family lookups snappy.
CREATE INDEX IF NOT EXISTS ix_bot_configs_user_id         ON bot_configs (user_id);
CREATE INDEX IF NOT EXISTS ix_bot_configs_strategy_family ON bot_configs (strategy_family);

-- 3. Add strategy_family to trades so we can filter each family's log independently.
ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS strategy_family VARCHAR NOT NULL DEFAULT 'strat_1';

CREATE INDEX IF NOT EXISTS ix_trades_user_id         ON trades (user_id);
CREATE INDEX IF NOT EXISTS ix_trades_strategy_family ON trades (strategy_family);

COMMIT;

-- ── Verification (read-only, safe to run anytime) ───────────────────────────
-- Every existing bot_config should now belong to 'strat_1':
--   SELECT strategy_family, COUNT(*) FROM bot_configs GROUP BY 1;
-- Every existing trade too:
--   SELECT strategy_family, COUNT(*) FROM trades       GROUP BY 1;
-- Composite unique should be present:
--   SELECT conname FROM pg_constraint WHERE conrelid = 'bot_configs'::regclass;
