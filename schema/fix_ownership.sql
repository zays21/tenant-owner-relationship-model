-- Run as the superuser (postgres) in SQL Shell, connected to tenant_ert:
--   \c tenant_ert
--   \i 'C:/.../schema/fix_ownership.sql'
-- Removes tables that were created by the wrong user, so they can be re-created as ert_user.
-- Safe here because the tables only hold test data that the simulator replaces.
DROP SCHEMA public CASCADE;
CREATE SCHEMA public AUTHORIZATION ert_user;
GRANT ALL ON SCHEMA public TO ert_user;
CREATE EXTENSION IF NOT EXISTS btree_gist;
