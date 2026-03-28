-- Run this in the Supabase SQL editor.
-- Creates the user_profiles table used by the onboarding flow and the agent.

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id             TEXT PRIMARY KEY,        -- phone in E.164 format (e.g. +51995132783)
    name                TEXT,
    gender              TEXT,                   -- 'hombre', 'mujer', 'otro'
    style_tags          TEXT[],                 -- e.g. ['casual', 'deportivo']
    sizes_text          TEXT,                   -- free text: "Camiseta M, Pantalón 32, Zapato 42"
    budget_range        TEXT,                   -- 'hasta_150', '150_400', 'mas_400'
    favorite_colors     TEXT[],                 -- e.g. ['neutros', 'azules y verdes']
    favorite_brands     TEXT[],                 -- e.g. ['Nike', 'Zara']
    city                TEXT,
    onboarding_step     INTEGER DEFAULT 0,
    onboarding_complete BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
