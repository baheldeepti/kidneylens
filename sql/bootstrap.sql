-- Runs automatically the first time the Postgres container starts with an empty volume.
-- It does NOT re-run on later starts. To re-run it: docker compose down -v && docker compose up -d

-- Landing area for CSV data loaded exactly as received (all columns as text).
CREATE SCHEMA IF NOT EXISTS raw;
