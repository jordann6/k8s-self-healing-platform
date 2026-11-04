-- Row S1b's password-auth database user (run by scripts/eks-db-bootstrap.sh as
-- the RDS master). It can connect and run SELECT 1, nothing else. The
-- rotation function creates selfheal_app_clone on its first run and grants it
-- this role, so both alternating users carry the same rights.
--
-- The initial password arrives in the APP_PW environment variable (from a
-- short-lived Secret), so it is never on a command line.
\getenv pw APP_PW

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'selfheal_app') THEN
        CREATE ROLE selfheal_app LOGIN;
    END IF;
END
$$;

-- Set every run, so a re-bootstrap re-seeds the user and the secret together.
ALTER ROLE selfheal_app WITH LOGIN PASSWORD :'pw';

GRANT CONNECT ON DATABASE selfheal TO selfheal_app;
