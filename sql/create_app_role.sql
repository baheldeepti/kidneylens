-- Read-only login role for the Streamlit app: kidneylens_app. Safe to re-run.
--
-- Run as the admin user, BEFORE `dbt build` (dbt grants SELECT to this role):
--   docker compose exec -T postgres psql -U kidneylens -d kidneylens < sql/create_app_role.sql
--
-- What it can do:  CONNECT to kidneylens; SELECT on analytics.dim_facility_current and
--                  analytics.mart_state_services (granted by dbt `grants` config on those models).
-- What it cannot:  INSERT, UPDATE, DELETE, TRUNCATE, DROP, ALTER, CREATE (incl. temp tables),
--                  or read raw.* / staging models.
-- Password is for local development only; must match APP_DB_PASSWORD in .env.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kidneylens_app') THEN
        CREATE ROLE kidneylens_app LOGIN PASSWORD 'local_app_password';
    END IF;
END
$$;

-- No elevated attributes, ever (re-asserted on every run).
ALTER ROLE kidneylens_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Second layer: sessions start read-only. Privileges below are the real barrier,
-- because a session could switch this off with BEGIN READ WRITE.
ALTER ROLE kidneylens_app SET default_transaction_read_only = on;

-- Nobody but the owner may create objects in `public` or temporary tables in this database.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE kidneylens FROM PUBLIC;

GRANT CONNECT ON DATABASE kidneylens TO kidneylens_app;
CREATE SCHEMA IF NOT EXISTS analytics;
GRANT USAGE ON SCHEMA analytics TO kidneylens_app;  -- lookup only; no CREATE

-- Table-level SELECT is granted by dbt on the published models only (see
-- dbt/kidneylens/models/marts/*.yml `grants`), so it survives every rebuild.

-- Retire the broader role from Task 10, which could also read staging views.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'kidneylens_reader') THEN
        EXECUTE 'DROP OWNED BY kidneylens_reader';  -- revokes its grants and default privileges
        EXECUTE 'DROP ROLE kidneylens_reader';
    END IF;
END
$$;
