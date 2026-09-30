-- Fresh-only logical import schema; AIOStreams v2.35.3 migrations 1 and 19.
-- The application runs its remaining migrations on first startup.
CREATE TABLE IF NOT EXISTS users (
        uuid TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        config TEXT NOT NULL,
        config_salt TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP),
        updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP),
        accessed_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP)
      );

      CREATE TABLE IF NOT EXISTS config_profiles (
        id                 TEXT PRIMARY KEY,
        owner              TEXT NOT NULL,
        uuid               TEXT NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
        encrypted_password TEXT NOT NULL,
        label              TEXT NOT NULL,
        alias              TEXT,
        broken_at          INTEGER,
        last_opened_at     INTEGER,
        created_at         INTEGER NOT NULL DEFAULT 0,
        updated_at         INTEGER NOT NULL DEFAULT 0,
        CHECK (length(owner) BETWEEN 1 AND 255),
        CHECK (length(label) BETWEEN 1 AND 64 AND trim(label) = label),
        CHECK (alias IS NULL OR (length(alias) BETWEEN 2 AND 64 AND trim(alias) = alias))
      );

      CREATE UNIQUE INDEX IF NOT EXISTS idx_config_profiles_owner_uuid
        ON config_profiles (owner, uuid);

      CREATE UNIQUE INDEX IF NOT EXISTS idx_config_profiles_owner_label
        ON config_profiles (owner, label);

      CREATE UNIQUE INDEX IF NOT EXISTS idx_config_profiles_alias
        ON config_profiles (alias) WHERE alias IS NOT NULL;

      CREATE INDEX IF NOT EXISTS idx_config_profiles_uuid
        ON config_profiles (uuid);
