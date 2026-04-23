-- ============================================================
-- AI SOC Analyst Platform — Initial Schema Migration
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- Table: log_entries
-- Stores raw and parsed system log lines
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_entries (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    source_file     TEXT        NOT NULL,
    raw_message     TEXT        NOT NULL,
    parsed_fields   JSONB,
    ingested_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_log_entries_timestamp    ON log_entries (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_log_entries_source_file  ON log_entries (source_file);
CREATE INDEX IF NOT EXISTS idx_log_entries_ingested_at  ON log_entries (ingested_at DESC);

-- ------------------------------------------------------------
-- Table: analysis_results
-- Stores Claude AI analysis for each log entry
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_results (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    log_entry_id        UUID        REFERENCES log_entries(id) ON DELETE CASCADE,
    criticality         TEXT        CHECK (criticality IN ('CRITICAL','HIGH','MEDIUM','LOW','INFO')),
    threat_type         TEXT,
    summary             TEXT,
    ioc_data            JSONB,
    recommended_actions JSONB,
    llm_model_used      TEXT,
    analyzed_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analysis_log_entry_id ON analysis_results (log_entry_id);
CREATE INDEX IF NOT EXISTS idx_analysis_criticality   ON analysis_results (criticality);
CREATE INDEX IF NOT EXISTS idx_analysis_analyzed_at   ON analysis_results (analyzed_at DESC);

-- ------------------------------------------------------------
-- Table: playbooks
-- Incident response playbooks generated for HIGH/CRITICAL events
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS playbooks (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    analysis_result_id  UUID        REFERENCES analysis_results(id) ON DELETE CASCADE,
    title               TEXT        NOT NULL,
    incident_id         TEXT        UNIQUE NOT NULL,
    status              TEXT        DEFAULT 'open' CHECK (status IN ('open','in_progress','closed')),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_playbooks_status      ON playbooks (status);
CREATE INDEX IF NOT EXISTS idx_playbooks_incident_id ON playbooks (incident_id);
CREATE INDEX IF NOT EXISTS idx_playbooks_created_at  ON playbooks (created_at DESC);

-- ------------------------------------------------------------
-- Table: playbook_steps
-- Individual steps within an incident response playbook
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS playbook_steps (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    playbook_id     UUID        REFERENCES playbooks(id) ON DELETE CASCADE,
    step_order      INTEGER     NOT NULL,
    category        TEXT        NOT NULL,
    description     TEXT        NOT NULL,
    is_completed    BOOLEAN     DEFAULT FALSE,
    completed_at    TIMESTAMPTZ,
    completed_by    TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_playbook_id ON playbook_steps (playbook_id);
CREATE INDEX IF NOT EXISTS idx_steps_order       ON playbook_steps (playbook_id, step_order);

-- ------------------------------------------------------------
-- Auto-update updated_at on playbooks
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_playbooks_updated_at ON playbooks;
CREATE TRIGGER set_playbooks_updated_at
    BEFORE UPDATE ON playbooks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ------------------------------------------------------------
-- Enable Row Level Security (RLS) — allow service role full access
-- ------------------------------------------------------------
ALTER TABLE log_entries       ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_results  ENABLE ROW LEVEL SECURITY;
ALTER TABLE playbooks         ENABLE ROW LEVEL SECURITY;
ALTER TABLE playbook_steps    ENABLE ROW LEVEL SECURITY;

-- Service role bypass (default Supabase behaviour — no explicit policy needed)
-- Anon / authenticated read policies (adjust as needed for your auth setup)
CREATE POLICY "Allow anon read log_entries"
    ON log_entries FOR SELECT USING (true);

CREATE POLICY "Allow anon read analysis_results"
    ON analysis_results FOR SELECT USING (true);

CREATE POLICY "Allow anon read playbooks"
    ON playbooks FOR SELECT USING (true);

CREATE POLICY "Allow anon read playbook_steps"
    ON playbook_steps FOR SELECT USING (true);
