# Read-Only App-DB Role Setup

How `devops_agent_readonly` — the Postgres role the RDS agent's DB-internal
tools connect with — is created, and why. Read this before touching
`mcp_server.py`'s `_connect_app_db`, `config/settings.py`'s `AppDbConfig`, or
setting up a new environment (`stag`/`production`) for this agent.

## Why this role exists

Four tools in `app/agents/tools/mcp/rds/mcp_server.py` — `get_active_connections`,
`get_lock_waits`, `explain_query_for_pid`, `get_table_bloat` — connect directly to
the monitored app's own database (not this project's `devops_agent` database) to run
diagnostic queries against `pg_stat_activity`/`pg_locks`/`pg_stat_user_tables`. That
connection must never use the app's own superuser/full-access credentials — this is a
standing rule for this codebase, not a suggestion. `devops_agent_readonly` is
a dedicated role scoped to exactly what those four tools need: read the
data, nothing else. It can't write, can't alter schema, can't create roles.

## Commands

Run these **once per environment** (`dev`, `stag`, `production` each have
their own cluster and need their own role + password — see
`config/settings.py`'s three `db_{env}_*` blocks). Connect as a superuser
(this project's own application code never does, and never should — these
commands are a one-time setup step, run by hand, not from `mcp_server.py`):

```sql
-- 1. The role itself
CREATE ROLE devops_agent_readonly WITH LOGIN PASSWORD '<pick a real password>';

-- 2. Cluster-wide stats visibility (pg_stat_activity, pg_locks, etc. --
--    without this, a non-superuser role only sees its own backend's rows)
GRANT pg_read_all_stats TO devops_agent_readonly;

-- 3. Schema access -- required even for a role that will only ever run
--    read-only queries: Postgres checks USAGE before letting any query
--    (including EXPLAIN, per explain_query_for_pid) touch objects in a
--    schema at all.
GRANT USAGE ON SCHEMA public TO devops_agent_readonly;
GRANT USAGE ON SCHEMA pgboss TO devops_agent_readonly;

-- 4. Read access to every table that exists right now
GRANT SELECT ON ALL TABLES IN SCHEMA public TO devops_agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA pgboss TO devops_agent_readonly;

-- 5. Read access to every table created from now on -- without this, a
--    future migration adding a new table would silently leave it
--    unreadable by this role until someone remembers to re-grant by hand.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO devops_agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA pgboss GRANT SELECT ON TABLES TO devops_agent_readonly;
```

Why `pgboss` specifically, alongside `public`: it's a real schema on the
monitored app's own database (the app's job-queue library owns it), and
`get_active_connections`/`get_lock_waits` can encounter backends touching
either schema — both need to be readable for the same reason `public` does.
If a different app's database has other schemas its own connections/locks
might touch, grant those too, following the same pattern.

Then put the role's credentials in `.env` under the matching block
(`DB_DEV_READONLY_USERNAME`/`DB_DEV_READONLY_PASSWORD`, or `DB_STAGING_*`/
`DB_PRODUCTION_*`) — never under that environment's main `DB_*_USERNAME`/
`PASSWORD` fields, which are reserved for the app's own superuser creds in
`config/settings.py`'s naming convention.

## Verification

Confirm the role's actual grants match what the commands above intend —
useful both right after setup and any time you're unsure what a role
actually has (grants can drift from what was originally run):

```python
import psycopg
from config.settings import settings

conn = psycopg.connect(
    host=settings.db_dev_host,       # swap for db_staging_host / db_production_host
    port=settings.db_dev_port,
    dbname=settings.db_dev_database,
    user=settings.db_dev_readonly_username,
    password=settings.db_dev_readonly_password,
)
cur = conn.cursor()

# Not superuser, can log in, can't create roles/DBs/replicate
cur.execute("SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'devops_agent_readonly'")
print(cur.fetchone())  # expect (True, False, False, False)

# Member of pg_read_all_stats
cur.execute("""
    SELECT r.rolname FROM pg_auth_members m
    JOIN pg_roles r ON r.oid = m.roleid
    JOIN pg_roles u ON u.oid = m.member
    WHERE u.rolname = 'devops_agent_readonly'
""")
print(cur.fetchall())  # expect [('pg_read_all_stats',)]

# Every existing table in both schemas actually has a grant, not just the ones
# that happened to exist when the GRANT ran
cur.execute("""
    SELECT table_schema, count(*) FROM information_schema.role_table_grants
    WHERE grantee = 'devops_agent_readonly' GROUP BY table_schema
""")
print(cur.fetchall())
```

Confirmed live against the real `dev` cluster (`sgm-backend-dev-stage-mb-01`,
`stargallery` database) while writing this doc: `SELECT`-only, 131/131 tables
in `public` and 16/16 in `pgboss` granted, `pg_read_all_stats` membership
present, no superuser/create privileges.

## Known limitations / not done here

- **Only `dev` has this role set up as of this doc.** `stag`/`production`
  need the same commands run against their own clusters (with their own
  password) before `Settings.app_db_config("stag"/"production")` has
  anything real to connect with — `config/settings.py`'s `db_staging_*`/
  `db_production_*` fields are still empty defaults.
- **No automation for this setup.** It's a manual, one-time-per-environment
  runbook, same as `documentation/devops/monitoring-setup.md`'s alarm setup
  — not scripted, not idempotent-by-tooling (though the SQL itself is
  idempotent: re-running `GRANT`/`ALTER DEFAULT PRIVILEGES` is always safe).
- **No password rotation process documented.** Whoever runs `CREATE ROLE`
  picks the password by hand; there's no rotation schedule or automated
  credential management here.
