-- Brain v0.2.0-neurosymbolic — PROV-DM PV primitive (brain-W1-S1)
-- W3C PROV-DM 1.3 conformant: agent + activity + wasGeneratedBy + wasDerivedFrom + sourceUri
-- Non-destructive 3-step: ADD nullable -> backfill -> ADD CONSTRAINT NOT NULL
BEGIN;

-- Step 1: Add columns (all nullable initially)
ALTER TABLE brain.thoughts
  ADD COLUMN IF NOT EXISTS prov_agent       VARCHAR(100),
  ADD COLUMN IF NOT EXISTS prov_activity    VARCHAR(50),
  ADD COLUMN IF NOT EXISTS was_generated_by VARCHAR(64),
  ADD COLUMN IF NOT EXISTS was_derived_from VARCHAR(64),
  ADD COLUMN IF NOT EXISTS source_uri       TEXT;

-- Step 2: Backfill legacy rows (idempotent)
UPDATE brain.thoughts
SET prov_agent     = COALESCE(prov_agent, 'legacy-import'),
    prov_activity  = COALESCE(prov_activity, 'unknown'),
    was_generated_by = COALESCE(was_generated_by, 'activity-legacy-' || thought_id)
WHERE prov_agent IS NULL
   OR prov_activity IS NULL
   OR was_generated_by IS NULL;

-- Step 3: Enforce NOT NULL on required PROV fields (idempotent via DO block)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='brain' AND table_name='thoughts'
               AND column_name='prov_agent' AND is_nullable='YES') THEN
    ALTER TABLE brain.thoughts ALTER COLUMN prov_agent SET NOT NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='brain' AND table_name='thoughts'
               AND column_name='prov_activity' AND is_nullable='YES') THEN
    ALTER TABLE brain.thoughts ALTER COLUMN prov_activity SET NOT NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='brain' AND table_name='thoughts'
               AND column_name='was_generated_by' AND is_nullable='YES') THEN
    ALTER TABLE brain.thoughts ALTER COLUMN was_generated_by SET NOT NULL;
  END IF;
END $$;

-- Step 4: Soft FK for derivative chain (NULL OK; if parent forgotten, child orphaned not cascaded)
-- Note: ADD CONSTRAINT IF NOT EXISTS not supported pre-PG 13; use DO block.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                 WHERE table_schema='brain' AND table_name='thoughts'
                   AND constraint_name='fk_thoughts_derived_from') THEN
    ALTER TABLE brain.thoughts
      ADD CONSTRAINT fk_thoughts_derived_from
      FOREIGN KEY (was_derived_from) REFERENCES brain.thoughts(thought_id)
      ON DELETE SET NULL
      DEFERRABLE INITIALLY DEFERRED;
  END IF;
END $$;

-- Step 5: Indexes for citation walker (Wave-2 W2-S1 will consume)
CREATE INDEX IF NOT EXISTS idx_thoughts_derived_from
  ON brain.thoughts(was_derived_from) WHERE was_derived_from IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_thoughts_generated_by
  ON brain.thoughts(was_generated_by);

COMMIT;
