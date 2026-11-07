-- Run ONCE as the superuser (postgres), from psql:
--   \i 000_setup.sql
-- Change the password before running. Never commit the real password to GitHub.
CREATE USER ert_user WITH PASSWORD 'change_me';
CREATE DATABASE tenant_ert OWNER ert_user;
\c tenant_ert
-- Needed for the "one active lease per flat" exclusion constraint.
CREATE EXTENSION IF NOT EXISTS btree_gist;
