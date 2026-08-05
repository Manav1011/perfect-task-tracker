# Configuration Inventory — Phase 3.3 post-refactor audit

- **Date:** 2026-08-05 (updated Phase 5.0 C1: secrets seam added)
- **Author:** Phase 3.3 implementation (5 incremental commits);
  Phase 5.0 C1 adds the `Secrets` sub-model.
- **Status:** LOCKED — every addition/removal below has been applied
  in code. New settings MUST extend this document + add tests.
- **Cross-reference:** `docs/TECH_SPEC.md §13g`, `§13h`,
  ADR-0021, and ADR-0022 (Phase 5.0).
- **Companion to the 4 AST isolation tests** (`test_api_isolation.py`,
  `test_services_isolation.py`, `tests/workspace/test_isolation.py`,
  `tests/index/test_isolation.py`, `tests/search/test_isolation.py`)
  + the new `tests/test_config_isolation.py`.

This document is the **source of truth for configuration surface**.
After Phase 3.3, every new setting added to `backend.config` MUST
extend §1 (typed layer) and §3 (env vars) here, with a row in the
hot-reload policy table (§5), and a test in `backend/tests/config/`.

## Classification scheme

| Class | Meaning |
| --- | --- |
| **RUNTIME** | Affects per-request behavior of the running backend (log level, app identity, log format). |
| **INFRASTRUCTURE** | Affects how the backend connects to external systems (workspace path, Postgres DSN). |
| **OPERATIONAL** | Affects how the backend is deployed / validated (verify_backend.sh harness constants). |
| **PROCESS** | Owned by the process supervisor — passed via CLI args, NOT by `Settings`. |

## Section index

1. [The typed layer (post-Phase 3.3)](#1-the-typed-layer-post-phase-33)
2. [Hardcoded values (post-Phase 3.3)](#2-hardcoded-values-post-phase-33)
3. [Environment variables](#3-environment-variables)
4. [Configuration entry points](#4-configuration-entry-points)
5. [Hot-reload policy (V1)](#5-hot-reload-policy-v1)
6. [Configuration dependency graph](#6-configuration-dependency-graph)
7. [Startup-time vs runtime-read classification](#7-startup-time-vs-runtime-read-classification)
8. [Migration risk per change (closed in Phase 3.3)](#8-migration-risk-per-change-closed-in-phase-33)
9. [Adding new settings — the contract](#9-adding-new-settings--the-contract)

---

## 1. The typed layer (post-Phase 5.0 C1)

Three Pydantic models live in `backend.config/`:

- `Settings` (top-level, frozen, validated) — application identity,
  filesystem substrate, a nested `database` sub-model, and a nested
  `secrets` sub-model (Phase 5.0 C1).
- `DatabaseSettings` (sub-model, frozen, validated) — the
  DATABASE_URL / POSTGRES_* precedence rule lives here.
- `Secrets` (sub-model, frozen, validated, Phase 5.0 C1) —
  `session_secret` / `csrf_secret`. Env-gated: required when
  `app_env != "development"`. No consumers wired yet — the seam
  exists; auth (Phase 5.4 future) is the first consumer.

### `Settings` fields

| Field | Env alias | Default | Class | Reload model | Current owner | Target owner | Migration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `app_env` | `APP_ENV` | `"development"` | RUNTIME | RESTART ONLY | `Settings` | `Settings` | DONE (Phase 3.3) |
| `app_name` | `APP_NAME` | `"perfect-task-tracker"` | RUNTIME | RESTART ONLY | `Settings` | `Settings` | DONE (Phase 3.3) |
| `log_level` | `LOG_LEVEL` | `"INFO"` (Literal) | RUNTIME | RESTART ONLY | `Settings` | `Settings` | DONE (Phase 3.3) — Literal validator added; silent-coerce fallback removed |
| `workspace_path` | `WORKSPACE_PATH` | `Path("./data/workspace")` | INFRASTRUCTURE | RESTART ONLY | `Settings` | `Settings` | DONE (Phase 3.3) — formerly hardcoded in `api/dependencies.py:36` |
| `database` | (nested) | `DatabaseSettings()` | INFRASTRUCTURE | RESTART ONLY | `DatabaseSettings` | `DatabaseSettings` | DONE (Phase 3.3) — sub-model + precedence rule |
| `database_url` (property) | — | resolves from `database` | INFRASTRUCTURE | RESTART ONLY | `Settings.database` | `Settings.database` | DONE (Phase 3.3) — flat field → sub-model property |
| `secrets` | (nested) | `Secrets()` (env-gated; in dev, both secrets may be unset) | OPERATIONAL | RESTART ONLY | `Secrets` | `Secrets` | DONE (Phase 5.0 C1) — sub-model + env-gated construction rule; no consumers wired (auth is Phase 5.4 future) |

Removed in Phase 3.3:
- `host` (was `"0.0.0.0"`) — moved to PROCESS / `--host`.
- `port` (was `8000`) — moved to PROCESS / `--port`.

### `DatabaseSettings` fields

| Field | Env alias | Default | Reload model | Current owner | Target owner | Migration |
| --- | --- | --- | --- | --- | --- | --- |
| `database_url` | `DATABASE_URL` | `None` (resolved by validator) | RESTART ONLY | `DatabaseSettings` | `DatabaseSettings` | DONE (Phase 3.3) — explicit-URL path is canonical |
| `postgres_host` | `POSTGRES_HOST` | `None` | RESTART ONLY | (compose only pre-Phase-3.3) | `DatabaseSettings` | DONE (Phase 3.3) — now consumed; previously unread |
| `postgres_port` | `POSTGRES_PORT` | `None` | RESTART ONLY | (compose only) | `DatabaseSettings` | DONE (Phase 3.3) |
| `postgres_db` | `POSTGRES_DB` | `None` | RESTART ONLY | (compose only) | `DatabaseSettings` | DONE (Phase 3.3) |
| `postgres_user` | `POSTGRES_USER` | `None` | RESTART ONLY | (compose only) | `DatabaseSettings` | DONE (Phase 3.3) |
| `postgres_password` | `POSTGRES_PASSWORD` | `None` | RESTART ONLY | (compose only) | `DatabaseSettings` | DONE (Phase 3.3) |

The `database_url` field is `None` until the `model_validator`
resolves it via the precedence rule (DATABASE_URL > full POSTGRES_*
> dev default; partial POSTGRES_* raises `ValueError`).

### `Secrets` fields (Phase 5.0 C1)

| Field | Env alias | Default | Reload model | Current owner | Target owner | Migration |
| --- | --- | --- | --- | --- | --- | --- |
| `session_secret` | `SESSION_SECRET` | `None` (env-gated: required when `app_env != "development"`) | RESTART ONLY | `Secrets` | `Secrets` | DONE (Phase 5.0 C1) — typed boundary; no consumers wired |
| `csrf_secret` | `CSRF_SECRET` | `None` (env-gated: required when `app_env != "development"`) | RESTART ONLY | `Secrets` | `Secrets` | DONE (Phase 5.0 C1) — typed boundary; no consumers wired |

The `Secrets` validator enforces the rule at construction time:

- `app_env == "development"` (the default): secrets may be `None` or
  empty. No failure.
- Any other `app_env` value (e.g. `staging`, `production`, `verify`):
  both `SESSION_SECRET` and `CSRF_SECRET` must resolve to non-empty
  values, or `ValueError` raises at `Settings()` construction.

Empty string is treated as missing. Pydantic `SecretStr` is used to
prevent accidental logging of the secret value. No `Secret`-anything
env-var reads happen outside `backend/config/secrets.py` — the
isolation allowlist in `tests/test_config_isolation.py` enforces
this. The verify harness sets `APP_ENV=verify` (see
`scripts/verify_backend.sh:150,393`); this is the one non-development
env the V1 verification exercises, so the secrets gate is a real
constraint at live-verify time.

**No consumers in V1.** Auth (Phase 5.4 future) will be the first
real consumer; until then, the seam exists, the rules are enforced,
and the env vars are documented.

### Settings is `frozen=True`

Every Pydantic model involved here is frozen. Mutation attempts
raise `ValidationError`. Tests vary config by constructing a
fresh `Settings(...)` instance — the structural test
(`test_config_isolation.py`) and the freeze test (`test_settings_freeze.py`)
pin this invariant.

---

## 2. Hardcoded values (post-Phase 3.3)

### INFRASTRUCTURE

| Value | Location | Status |
| --- | --- | --- |
| `"./data/workspace"` | `backend.config.settings.Settings.workspace_path` | **NOW A Pydantic default** — no longer hardcoded in `api/dependencies.py`. The dependency tree reads it via `settings.workspace_path`. |
| Dev DSN `postgresql+psycopg://ptt:ptt@localhost:5433/ptt` | `backend.config.database.DatabaseSettings._resolve_database_url` | Stays as a Pydantic default when DATABASE_URL + POSTGRES_* are both unset. Dev-only fallback. |

### PROCESS (supervisor)

These are NOT in `Settings` — they belong to the uvicorn CLI and
the shell launcher. Listed here for cross-reference only.

| Value | Owner | How it's passed |
| --- | --- | --- |
| `127.0.0.1`, `18000` (verify harness ports) | uvicorn CLI | `--host 127.0.0.1 --port 18000` |
| `0.0.0.0`, `8000` (default) | uvicorn CLI | `--host 0.0.0.0 --port 8000` (or app-default if omitted) |

### OPERATIONAL

| Value | Location | Status |
| --- | --- | --- |
| `127.0.0.1`, `18000`, `5433`, `ptt/ptt/ptt` | `scripts/verify_backend.sh` (lines 29-38) | Unchanged from pre-Phase-3.3 — these are HARNESS constants, not application config. The verify harness still sets them itself; the app receives `DATABASE_URL` and `WORKSPACE_PATH` as env vars. |
| `/tmp/verify_backend.{pid,log}` | `scripts/verify_backend.sh` (lines 32-33) | Harness-only — never consumed by the app. |
| `APP_ENV=verify` | `scripts/verify_backend.sh:149,391` | The app treats `APP_ENV` as opaque; the value is harness metadata. |

### Removed in Phase 3.3

| Value | Was | Action |
| --- | --- | --- |
| `"./data/workspace"` | `backend/api/dependencies.py:36` | **MIGRATED** to `Settings.workspace_path`. The CWD-relative symlink trick in `verify_backend.sh` is replaced by `WORKSPACE_PATH=...`. |
| `"INFO"` | `backend/core/logging.py:15` (function default) | **REMOVED.** Caller now passes `settings.log_level` (typed `LogLevel`). |

### Compile-time constants (deliberately not Configuration)

These literals appear in code but should NEVER migrate to config —
adding config flags for them would be speculative. Justified by
"no per-env behaviour" or "physical-world constant":

| Literal | Location | Why it's NOT configuration |
| --- | --- | --- |
| `pool_pre_ping=True`, `future=True` | `backend/database/session.py:34` | SQLAlchemy session options, same for every environment. |
| `StreamHandler(sys.stdout)`, `PrintLoggerFactory()`, JSON renderer | `backend/core/logging.py` | Logging destination + format. Infrastructure decision, not deployment config. |
| `time.monotonic()` / `time.time()` | `backend/workspace/cache.py` | stdlib time functions. |
| `TimeStamper(fmt="iso")` | `backend/core/logging.py` | Timestamp format standardisation, not deployment config. |

---

## 3. Environment variables

### Declared in `.env.example` and **consumed by Settings**

| Variable | Field | Class | Notes |
| --- | --- | --- | --- |
| `APP_ENV` | `app_env` | RUNTIME | Opaque to the app; surfaced in startup log + health endpoint. |
| `APP_NAME` | `app_name` | RUNTIME | Surfaced in startup log + health endpoint. |
| `LOG_LEVEL` | `log_level` | RUNTIME | Literal-validated; unknown values rejected at startup. |
| `WORKSPACE_PATH` | `workspace_path` | INFRASTRUCTURE | NEW in Phase 3.3. Path is `Path` typed; `str` gets coerced by Pydantic. |
| `DATABASE_URL` | `database.database_url` | INFRASTRUCTURE | Canonical DSN — explicit always wins. |
| `POSTGRES_HOST` | `database.postgres_host` | INFRASTRUCTURE | NEW consumer in Phase 3.3. |
| `POSTGRES_PORT` | `database.postgres_port` | INFRASTRUCTURE | NEW consumer. |
| `POSTGRES_DB` | `database.postgres_db` | INFRASTRUCTURE | NEW consumer. |
| `POSTGRES_USER` | `database.postgres_user` | INFRASTRUCTURE | NEW consumer. |
| `POSTGRES_PASSWORD` | `database.postgres_password` | INFRASTRUCTURE | NEW consumer. |
| `SESSION_SECRET` | `secrets.session_secret` | OPERATIONAL | NEW in Phase 5.0 C1. Env-gated: required when `app_env != "development"`. `SecretStr` typed. |
| `CSRF_SECRET` | `secrets.csrf_secret` | OPERATIONAL | NEW in Phase 5.0 C1. Env-gated: required when `app_env != "development"`. `SecretStr` typed. |

### Process-supervisor env (NOT in `.env.example`)

`HOST`, `PORT` were removed from `.env.example` in Phase 3.3. They
belong to the uvicorn CLI args, not Settings. The `.env.example`
file documents this with a comment.

### Runtime `os.environ` / `os.getenv` reads

| Location | Variable | Class | Status |
| --- | --- | --- | --- |
| `backend/tests/benchmarks/test_load_tree_benchmark.py:91,121,151` | `RUN_LARGE_BENCH` | FEATURE (test-only) | Test-only gate — excluded from the AST isolation test by directory. |
| `backend/tests/benchmarks/test_search_benchmark.py:73,108,139` | `RUN_LARGE_BENCH` | FEATURE (test-only) | same |

**Zero** `os.environ` / `os.getenv` reads in production code. The
AST test (`test_config_isolation.py`) enforces this going forward.

### `.env` file loading

`Settings` and `DatabaseSettings` both read `.env` via
`env_file=".env"`. No `python-dotenv` / `load_dotenv` calls
anywhere. The project does NOT commit a `.env` (only
`.env.example`). The verify harness does not load a `.env` — it
passes env vars directly to the uvicorn subprocess.

---

## 4. Configuration entry points

| Site | What it reads | Class | Notes |
| --- | --- | --- | --- |
| `backend/main.py:32` (`create_app`) | `get_settings()` — eager-priming | RUNTIME | First call materialises the cache. |
| `backend/core/lifespan.py:43` | `get_settings()` → `configure_logging(log_level)` + `StartupSubsystem.build(config=settings, …)` + `build_filesystem(settings=settings)` | RUNTIME + INFRASTRUCTURE | The seam Phase 3.3 grew into. Single consumer of most settings fields. |
| `backend/api/v1/endpoints/health.py` | `app_name`, `app_env` | RUNTIME | Reads from `app.state.settings` (Phase 3.3) — no direct `get_settings()` call. |
| `backend/database/session.py:31` | `database_url` (lazy) | INFRASTRUCTURE | First read primes the Settings cache. **Allowlisted** in the structural test (lazy engine runs before `app.state` exists). |
| `backend/alembic/env.py:28` | `database_url` | INFRASTRUCTURE | Alembic migration tool. **Allowlisted** (standalone tool, not in the request path). |
| `backend/api/dependencies.py:28` (`build_filesystem`) | `workspace_path` (default from settings) | INFRASTRUCTURE | Reads `Settings().workspace_path` if no explicit path is passed. Tests pass `workspace_path` directly. |
| `scripts/verify_backend.sh:148,389` | `DATABASE_URL`, `WORKSPACE_PATH`, `APP_ENV`, `LOG_LEVEL` | OPERATIONAL | Passed as shell env to the uvicorn subprocess via the real env-var path (no symlink trick). |

---

## 5. Hot-reload policy (V1)

**Every setting is RESTART ONLY.** Construction-time validation is
the bound on misuse. This policy is documented explicitly in
`backend.config.settings` docstring, in §13g "Configuration Layer",
in ADR-0021, and in this inventory.

| Setting | Reload model | Why restart-only |
| --- | --- | --- |
| `app_env` | RESTART ONLY | Affects logging format + health endpoint output. Hot-reload would mean stale health responses until next request. |
| `app_name` | RESTART ONLY | Application identity. |
| `log_level` | RESTART ONLY (V1) | Technically hot-reloadable via `logging.basicConfig` re-call, but the change would race with in-flight structured log lines. Document as restart-only. |
| `workspace_path` | RESTART ONLY | Cache populated once at boot from this path; runtime change would orphan the cache. |
| `database_url` (or POSTGRES_*) | RESTART ONLY | Engine constructed once; runtime swap requires connection-pool draining. |

Future phases MAY introduce a `reload_signal` plumbing (SIGHUP
handler + a per-subsystem `reload(settings)` method) for fields
that have a documented hot-reload path. None exist today. When
added, they appear in this table with `HOT-RELOADABLE` and a one-line
description of the reload side-effects.

---

## 6. Configuration dependency graph

```
                  ┌────────────────────────────────────────┐
                  │ process.env + .env file (Pydantic)     │
                  └────────────────┬───────────────────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────────────┐
                  │ backend.config.settings.Settings       │
                  │ backend.config.database.DatabaseSettings│
                  │ (frozen=True, validated, immutable     │
                  │  after startup)                        │
                  │  app_env | app_name | log_level        │
                  │  workspace_path                        │
                  │  database_url (resolved)               │
                  └────────────────┬───────────────────────┘
                                   │  via get_settings() (lru_cache)
                                   ▼
                  ┌────────────────────────────────────────┐
                  │ backend.core.lifespan                  │
                  │ (the SINGLE direct consumer)           │
                  └─────┬──────────┬──────────┬─────────────┘
                        │          │          │
                        ▼          ▼          ▼
                  configure_logging  build_filesystem  create_app
                  (log_level)        (workspace_path)  app.state.X
                        │          │          │
                        ▼          ▼          ▼
                  ┌────────────────────────────────────────┐
                  │ request handlers                       │
                  │ read app.state ONLY — never Settings() │
                  └────────────────────────────────────────┘
```

Two exceptions (allowlisted in the structural test):
- `backend/database/session.py` — lazy engine; reads settings on
  first `get_engine()` call. Runs before app.state exists.
- `backend/alembic/env.py` — standalone migration tool. Not in
  the request path.

After Phase 3.3, `get_settings()` is called in **exactly two
production places**:
1. `backend/core/lifespan.py:43` — at startup.
2. `backend/api/dependencies.py:43` (`build_filesystem` default-arg
   fallback) — at startup OR when called outside lifespan (tests).

This is the smallest possible call surface while keeping the
default-arg path ergonomic. A future tightening could collapse
these into a single owner.

---

## 7. Startup-time vs runtime-read classification

| Phase | Where it's read | When | Allowlisted? |
| --- | --- | --- | --- |
| Settings construction | `backend.config.settings.Settings()` | startup (lifespan entry) | Yes (the boundary) |
| DatabaseSettings construction | `backend.config.database.DatabaseSettings()` | startup (alongside Settings) | Yes (the boundary) |
| Logging config | `configure_logging(settings.log_level)` | startup (lifespan line 44) | reads via Settings |
| Filesystem root | `build_filesystem(settings=settings)` | startup (lifespan) | reads via Settings |
| Engine | `database/session.py:31 get_settings()` | first DB call (lazy, bounded) | Yes (allowlisted carve-out) |
| Alembic env | `alembic/env.py:28` | migration entry (standalone tool) | Yes (allowlisted carve-out) |
| Health endpoint | `app_name`, `app_env` (via app.state) | per-request | reads via app.state, NOT Settings |

Hard rule: **all configuration is read at startup, never at request
time.** After the lifespan yields, configuration is frozen. The
two allowlisted exceptions run before `app.state` exists; both
are documented in §6 of this inventory.

---

## 8. Migration risk per change (closed in Phase 3.3)

| Change | Risk | Compatibility | Verification |
| --- | --- | --- | --- |
| Freeze Settings | Low — defaults unchanged; consumers unaffected | Back-compat | `test_settings_freeze.py` (5 tests); full pytest + verify |
| Log-level validator (Literal) | Low — rejects unknown at construction; default `INFO` preserved | Back-compat unless someone passes a typo'd env var | `test_log_level_validator.py` (13 tests); verify sets `LOG_LEVEL=INFO` |
| workspace_path from settings | High — `build_filesystem()` called from dependencies AND lifespan; both now read settings | Back-compat: default `./data/workspace`; verify harness sets `WORKSPACE_PATH` | `test_workspace_path_from_settings.py` (5 tests); verify smoke |
| DatabaseSettings POSTGRES_* builder | Medium — adds 5 fields with defaults; explicit `DATABASE_URL` still wins | Back-compat: existing `DATABASE_URL`-using setups unchanged | `test_database_settings.py` (13 tests); verify sets `DATABASE_URL` |
| HOST/PORT removed from Settings | Low — process supervisor owns them via `--host`/`--port` | Back-compat: verify script passes CLI args; Settings host/port were dead fields | `test_settings_host_and_port_fields_are_removed` (in test_settings_freeze.py); verify smoke |
| os.environ structural test | Low — zero production hits today | Back-compat: nothing currently breaks | `test_config_isolation.py` (7 tests including detector self-tests) |

### Highest-risk change: workspace_path from settings

`build_filesystem()` is called from TWO places today:
- `backend/core/lifespan.py` (production)
- `backend/api/dependencies.get_repository()` (legacy convenience for
  unit tests that don't run inside a FastAPI app)

Both call sites read from `Settings().workspace_path`. The verify
harness's prior CWD-symlink trick (`RUN_DIR/data/workspace`
symlink → real verify dir) is retired — `WORKSPACE_PATH` is now
a real env var flowing through Settings into the application. Live
verification proves the seam holds: 49/49 PASS with the verify
workspace populated at `data/verify_workspace/` (independent of
project's `./data/workspace`).

---

## 9. Adding new settings — the contract

**This document is a living engineering artifact.** Every change
to `backend.config.*` MUST extend this inventory in the same
commit. The contributor checklist below is the contract that
prevents future drift.

### Contributor checklist (copy-paste into your PR)

```
## Configuration change checklist

[ ] New (or removed) Settings field documented in §1 of
    CONFIGURATION_INVENTORY.md with these columns filled:
    Field, Env alias, Default, Class, Reload model,
    Current owner, Target owner, Migration.

[ ] New env-var aliases added to §3 of the inventory.

[ ] Reload-model row added to §5 (default: RESTART ONLY).

[ ] If this is a request-time consumer, the change is wired via
    app.state / injected dependency, NOT direct get_settings().
    Structural test test_config_isolation.py still passes.

[ ] If a sub-model was added (Phase 5.0 example: `Secrets`):
    - `ALLOWED_CONFIG_READERS` in tests/test_config_isolation.py
      updated with the new module path.
    - Test file in `backend/tests/config/test_<sub>.py` covers:
      construction in dev (with and without secrets), construction in
      non-dev (raises on missing), env-var resolution, frozen mutation.

[ ] Unit test in backend/tests/config/ covering:
    - default value
    - env-var resolution
    - validator accepts good / rejects bad input
    - frozen mutation raises

[ ] scripts/verify_backend.sh still 49/49 PASS (or higher after
    new probe checks land — Phase 5.0 C2 will extend it).

[ ] TECH_SPEC.md updated if the architectural meaning changed
    (e.g., a new lifecycle diagram node, a new explicit carve-out,
    or a new reload side-effect). Phase 5.0 C3 lands TECH_SPEC §13h
    and ADR-0022 in their own commit.
```

### When you add a new field to `backend.config.settings.Settings` (or
a sub-model), you MUST:

1. **Pick the right class.** RUNTIME affects per-request behavior;
   INFRASTRUCTURE affects external connections. PROCESS belongs to
   the supervisor — DO NOT add PROCESS fields to Settings.

2. **Declare the env alias** explicitly via `Field(alias=...)`
   unless the field name already matches the env var.

3. **Validate at construction.** Constrain `Literal[...]`,
   numeric ranges, or non-empty strings via Pydantic types or
   `@field_validator`. Silently coercing bad values to defaults
   is the bug we're trying to prevent.

4. **Add the reload-model column entry** to §5 of this inventory.
   The default is "RESTART ONLY" — if you claim HOT-RELOADABLE,
   document the reload side-effects.

5. **Add a unit test** in `backend/tests/config/`:
   - default value matches the documented default
   - env-var resolution works
   - validator rejects bad input
   - mutation raises (frozen=True)

6. **Wire the consumer via `app.state` if at request time, or
   via the lifespan if at startup.** NEVER consume the new field
   via direct `get_settings()` from a request handler — that would
   re-create the runtime-vs-startup ambiguity §7 explicitly forbids.

7. **Update §1 of this inventory** with the new field row (incl.
   Current owner / Target owner / Migration columns), the new env
   alias in §3, and the new entry point in §4.

   The structural test (`test_config_isolation.py`) will catch
   any new direct env-var read; nothing else needs to change
   there.

### Ownership conventions

Each configuration row carries a **Current owner** and **Target
owner**. The values mean:

| Owner value | Meaning |
| --- | --- |
| `Settings` | The field lives on `backend.config.settings.Settings` directly. |
| `DatabaseSettings` | The field lives on the sub-model `backend.config.database.DatabaseSettings`. |
| `Secrets` | The field lives on the sub-model `backend.config.secrets.Secrets` (Phase 5.0 C1). |
| `process supervisor` | The setting is owned by uvicorn / shell — NOT in `Settings`. Documented for cross-reference only. |
| `compose only` (legacy) | Pre-Phase-3.3 — the env var was declared in `.env.example` but unread by the app. Kept only on rows describing migration history. |

### Migration status conventions

The Migration column tracks how a field transitioned into the
typed boundary:

| Status | Meaning |
| --- | --- |
| `DONE (Phase 3.3)` | Refactored — explicit DSN wins; previously dead field / unread env var / silent coercion. |
| `DONE (Phase X.Y)` | Migrated in an earlier phase (rare for items still listed). |
| `PENDING` | Field planned but not yet in `Settings`. Used during refactor scoping. |
| `OUT OF SCOPE` | Field intentionally not in `Settings` (process-supervisor items). |
