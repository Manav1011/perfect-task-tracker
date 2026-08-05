#!/usr/bin/env bash
# scripts/verify_backend.sh — Release-candidate validation harness.
#
# Boots the REAL backend (Postgres + filesystem workspace + runtime cache +
# synchronizer + reconciler + index + search) and exercises every API
# endpoint via curl, capturing request/response evidence. Exits non-zero
# on any failure so CI / an orchestrator can gate on it.
#
# Layout:
#   data/verify_workspace/   — temp workspace root (cleaned each run)
#   data/verify_evidence/    — per-section log files + final report
#   /tmp/verify_backend.pid  — PID of the uvicorn we started
#   /tmp/verify_backend.log  — raw uvicorn stdout/stderr
#
# Usage:
#   bash scripts/verify_backend.sh                # full validation pass
#   bash scripts/verify_backend.sh --skip-startup # assume backend already up
#   bash scripts/verify_backend.sh --keep         # leave backend running on exit
#
# Re-runnable: each run cleans its workspace dir + truncates the index table.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

EVIDENCE_DIR="$REPO_ROOT/data/verify_evidence"
WORKSPACE_DIR="$REPO_ROOT/data/verify_workspace"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="18000"            # avoid port 8000 (llama.cpp) and 5432 collisions
BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"
PID_FILE="/tmp/verify_backend.pid"
LOG_FILE="/tmp/verify_backend.log"
PG_USER="ptt"
PG_PASS="ptt"
PG_DB="ptt"
PG_HOST="127.0.0.1"
PG_PORT="5433"

SKIP_STARTUP=0
KEEP_BACKEND=0
for arg in "$@"; do
  case "$arg" in
    --skip-startup) SKIP_STARTUP=1 ;;
    --keep)         KEEP_BACKEND=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 64 ;;
  esac
done

# ---- helpers ------------------------------------------------------------

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

FAILS=0
PASSES=0

section() { bold ""; bold "== $* =="; }
ok()      { green "  PASS $*"; PASSES=$((PASSES+1)); }
fail()    { red   "  FAIL $*"; FAILS=$((FAILS+1)); }
note()    { yellow "  $*"; }

# Run curl, log to file, fail on HTTP error or jq parse error.
# Usage: http METHOD PATH [json-body] [curl-extra-args...]
# Echoes: <status>\n<body>
http() {
  local method="$1" path="$2" body="${3:-}" extra="${4:-}"
  local out_file="$EVIDENCE_DIR/last_response.json"
  local code
  if [[ -n "$body" ]]; then
    code=$(curl -sS -o "$out_file" -w '%{http_code}' \
      -X "$method" "$BACKEND_URL$path" \
      -H 'Content-Type: application/json' \
      --data "$body" $extra)
  else
    code=$(curl -sS -o "$out_file" -w '%{http_code}' \
      -X "$method" "$BACKEND_URL$path" $extra)
  fi
  printf '%s\n' "$code"
  cat "$out_file"
  printf '\n'
}

# Extract a JSON field. Usage: jq_get FILE '.hits[0].title'
jq_get() { jq -r "$2" "$1" 2>/dev/null; }

# ---- precondition checks ------------------------------------------------

section "Precondition checks"

# 1. Postgres reachable.
if ! PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
     -tAc "SELECT 1" >/dev/null 2>&1; then
  red "  Postgres not reachable at $PG_HOST:$PG_PORT as $PG_USER/$PG_DB"
  exit 2
fi
ok "Postgres reachable at $PG_HOST:$PG_PORT"

# 2. uv available.
command -v uv >/dev/null || { red "  uv not on PATH"; exit 2; }
ok "uv available"

# 3. Clean workspace for a deterministic run.
if [[ -d "$WORKSPACE_DIR" ]]; then
  rm -rf "$WORKSPACE_DIR"
fi
mkdir -p "$WORKSPACE_DIR"
ok "Workspace reset: $WORKSPACE_DIR"

# 4. Reset index table to a clean slate.
PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  -c "TRUNCATE node_index" >/dev/null 2>&1 || true
ok "Index table truncated"

# 5. Evidence dir.
mkdir -p "$EVIDENCE_DIR"
ok "Evidence dir: $EVIDENCE_DIR"

# 6. Free the port if a previous run left a listener.
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  note "Killing leftover backend pid $(cat "$PID_FILE")"
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  sleep 1
fi

# ---- start backend ------------------------------------------------------

if [[ $SKIP_STARTUP -eq 0 ]]; then
  section "Starting backend on $BACKEND_URL"

  : > "$LOG_FILE"

  # Phase 3.3 — workspace path comes from Settings (env var). No more CWD
  # symlink trick; the backend reads WORKSPACE_PATH directly.
  note "Workspace dir: $WORKSPACE_DIR (passed via WORKSPACE_PATH)"

  ( cd "$REPO_ROOT" && \
      WORKSPACE_PATH="$WORKSPACE_DIR" \
      DATABASE_URL="postgresql+psycopg://$PG_USER:$PG_PASS@$PG_HOST:$PG_PORT/$PG_DB" \
      APP_ENV=verify \
      LOG_LEVEL=INFO \
      nohup uv run --project "$REPO_ROOT" uvicorn backend.main:app \
        --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-access-log \
        >"$LOG_FILE" 2>&1 &
      echo $! > "$PID_FILE"
  )

  note "uvicorn PID $(cat "$PID_FILE") — log: $LOG_FILE"

  # Wait for /healthz-like endpoint (here we use /api/v1/health).
  for attempt in $(seq 1 60); do
    if curl -sS -m 2 "$BACKEND_URL/api/v1/health" >/dev/null 2>&1; then
      green "  Backend responsive after ${attempt}s"
      break
    fi
    sleep 1
    if [[ $attempt -eq 60 ]]; then
      red "  Backend did not respond within 60s. Tail of log:"
      tail -80 "$LOG_FILE"
      exit 3
    fi
  done
  ok "Backend up at $BACKEND_URL"

  # Trap to clean up.
  trap 'if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
          if [[ $KEEP_BACKEND -eq 0 ]]; then
            note "Stopping backend pid $(cat "$PID_FILE")"
            kill "$(cat "$PID_FILE")" 2>/dev/null || true
          fi
        fi' EXIT
else
  section "Skipping startup (backend assumed already running)"
fi

# ---- capture startup log ------------------------------------------------

section "Startup log evidence"
cp "$LOG_FILE" "$EVIDENCE_DIR/01_startup.log"
ok "Saved 01_startup.log"

# ---- endpoint matrix ----------------------------------------------------

section "Endpoint matrix"

EVIDENCE_PREFIX="$EVIDENCE_DIR"

# Helper: assert_status EXPECTED METHOD PATH [BODY] [JQ-CHECK] [JQ-EXPECTED]
assert_status() {
  local expected="$1" method="$2" path="$3" body="${4:-}" jq_check="${5:-}" jq_expected="${6:-}"
  local resp; resp=$(http "$method" "$path" "$body")
  local code; code=$(printf '%s' "$resp" | head -1)
  local payload; payload=$(printf '%s' "$resp" | tail -n +2)
  echo "$payload" > "$EVIDENCE_PREFIX/last_${method}_${path//\//_}.json"

  if [[ "$code" != "$expected" ]]; then
    fail "$method $path → expected $expected, got $code"
    note "body: $payload"
    return 1
  fi
  if [[ -n "$jq_check" ]]; then
    local actual; actual=$(printf '%s' "$payload" | jq -r "$jq_check" 2>/dev/null)
    if [[ "$actual" != "$jq_expected" ]]; then
      fail "$method $path → jq '$jq_check' expected '$jq_expected', got '$actual'"
      note "body: $payload"
      return 1
    fi
  fi
  ok "$method $path → $code"
  return 0
}

# 1. health
section "Health"
assert_status 200 GET /api/v1/health "" '.status' 'ok'

# 2. workspace (empty)
section "Workspace — empty"
assert_status 404 GET /api/v1/workspace "" '.code' 'workspace_empty'

# 3. create root story (Alpha)
section "Stories — create + get"
ALPHA_BODY='{"title":"Alpha"}'
assert_status 201 POST /api/v1/stories "$ALPHA_BODY" '.title' 'Alpha'
ALPHA_ID=$(jq -r '.id' "$EVIDENCE_PREFIX/last_POST__api_v1_stories.json")
note "Alpha id: $ALPHA_ID"

# Capture created_at/updated_at for later ordering assertions.
ALPHA_CREATED=$(jq -r '.created_at' "$EVIDENCE_PREFIX/last_POST__api_v1_stories.json")
note "Alpha created_at: $ALPHA_CREATED"

# Get story back
assert_status 200 GET "/api/v1/stories/$ALPHA_ID" "" '.id' "$ALPHA_ID"

# Story not found
assert_status 404 GET /api/v1/stories/00000000-0000-0000-0000-000000000000 "" '.code' 'story_not_found'

# Create story with empty title → 422
assert_status 422 POST /api/v1/stories '{"title":""}' '' '' ''

# Create a second story (Bravo) for ordering tests.
assert_status 201 POST /api/v1/stories '{"title":"Bravo"}' "" '.title' 'Bravo'
BRAVO_ID=$(jq -r '.id' "$EVIDENCE_PREFIX/last_POST__api_v1_stories.json")

# 4. nodes — create children, get, list, patch, delete, move, canvas, metadata
section "Nodes — create child (task under Alpha)"
TASK_BODY='{"title":"First task","type":"task"}'
assert_status 201 POST "/api/v1/nodes/$ALPHA_ID/children" "$TASK_BODY" '.type' 'task'
TASK_ID=$(jq -r '.id' "$EVIDENCE_PREFIX/last_POST__api_v1_nodes_${ALPHA_ID}_children.json")
note "First task id: $TASK_ID"

# Note (under Alpha).
NOTE_BODY='{"title":"Note one","type":"note"}'
assert_status 201 POST "/api/v1/nodes/$ALPHA_ID/children" "$NOTE_BODY" '.type' 'note'
NOTE_ID=$(jq -r '.id' "$EVIDENCE_PREFIX/last_POST__api_v1_nodes_${ALPHA_ID}_children.json")

# Children listing
assert_status 200 GET "/api/v1/nodes/$ALPHA_ID/children" "" '. | length' '2'

# Get node
assert_status 200 GET "/api/v1/nodes/$TASK_ID" "" '.id' "$TASK_ID"

# Get node — not found
assert_status 404 GET "/api/v1/nodes/00000000-0000-0000-0000-000000000000" "" '.code' 'node_not_found'

# Create child with bad parent → 404 parent_not_found
assert_status 404 POST "/api/v1/nodes/00000000-0000-0000-0000-000000000000/children" \
  '{"title":"orphan","type":"task"}' '.code' 'parent_not_found'

# Create child with bad type → 422
assert_status 422 POST "/api/v1/nodes/$ALPHA_ID/children" '{"title":"x","type":"epic"}' "" '' ''

# Patch (rename)
section "Nodes — patch / move / canvas / metadata / delete"
assert_status 200 PATCH "/api/v1/nodes/$TASK_ID" '{"title":"First task renamed"}' '.title' 'First task renamed'

# Patch with empty title → 422
assert_status 422 PATCH "/api/v1/nodes/$TASK_ID" '{"title":""}' '' '' ''

# Patch non-existent → 404
assert_status 404 PATCH "/api/v1/nodes/00000000-0000-0000-0000-000000000000" \
  '{"title":"x"}' '.code' 'node_not_found'

# Move (under Bravo)
assert_status 200 POST "/api/v1/nodes/$TASK_ID/move" \
  "{\"new_parent_id\":\"$BRAVO_ID\",\"position\":0}" '.id' "$TASK_ID"

# Move into self → 409 cycle
assert_status 409 POST "/api/v1/nodes/$ALPHA_ID/move" \
  "{\"new_parent_id\":\"$ALPHA_ID\"}" '.code' 'cycle_in_move'

# Canvas write + read
section "Canvas — write + read"
assert_status 200 PATCH "/api/v1/nodes/$TASK_ID/canvas" \
  '{"content":"# hello\nbody"}' '.content' '# hello
body'

assert_status 200 GET "/api/v1/nodes/$TASK_ID/canvas" "" '.node_id' "$TASK_ID"

# Canvas read on non-existent → 404
assert_status 404 GET "/api/v1/nodes/00000000-0000-0000-0000-000000000000/canvas" \
  "" '.code' 'node_not_found'

# Metadata
section "Metadata"
assert_status 200 PATCH "/api/v1/nodes/$TASK_ID/metadata" \
  '{"key":"priority","value":"high"}' '.metadata.priority' 'high'

# Metadata with empty key → 422
assert_status 422 PATCH "/api/v1/nodes/$TASK_ID/metadata" '{"key":"","value":1}' "" '' ''

# Metadata on non-existent → 404
assert_status 404 PATCH "/api/v1/nodes/00000000-0000-0000-0000-000000000000/metadata" \
  '{"key":"k","value":1}' '.code' 'node_not_found'

# Workspace tree
section "Workspace — full tree (after seeding)"
assert_status 200 GET /api/v1/workspace "" '.roots | length' '2'

# 5. search (eventual consistency — synchroniser hooks run during writes above)
section "Search — exact / prefix / filters / sort / pagination / errors"
sleep 1   # let synchroniser write settle (it's async on the repo write path)

assert_status 200 GET "/api/v1/search?title=Alpha" "" '.hits[0].title' 'Alpha'
assert_status 200 GET "/api/v1/search?prefix=Al" "" '.total' '1'
assert_status 200 GET "/api/v1/search?node_type=task" "" '.total' '1'
assert_status 200 GET "/api/v1/search?sort=title_asc" "" '.total' '4'

# Empty result is 200, not 404
assert_status 200 GET "/api/v1/search?title=Zulu" "" '.total' '0'

# Invalid sort → 422
assert_status 422 GET "/api/v1/search?sort=foo" "" '' ''

# Title and prefix both set → 422 invalid_search_query (mutual exclusion)
RESP=$(http GET "/api/v1/search?title=A&prefix=A")
CODE=$(printf '%s' "$RESP" | head -1)
if [[ "$CODE" == "422" ]]; then ok "GET /api/v1/search?title=A&prefix=A → 422 (mutual exclusion)"; else
  fail "mutual exclusion expected 422, got $CODE"
fi

# Page size > 200 → 422
assert_status 422 GET "/api/v1/search?page_size=201" "" '' ''

# Negative page → 422
assert_status 422 GET "/api/v1/search?page=-1" "" '' ''

# Invalid node_type → 422
assert_status 422 GET "/api/v1/search?node_type=epic" "" '' ''

# 6. delete
section "Nodes — delete"
assert_status 204 DELETE "/api/v1/nodes/$NOTE_ID" "" '' ''

# Delete non-existent → 404
assert_status 404 DELETE "/api/v1/nodes/00000000-0000-0000-0000-000000000000" \
  "" '.code' 'node_not_found'

# 7. OpenAPI surface
section "OpenAPI surface"
assert_status 200 GET /openapi.json "" '.paths."/api/v1/search".get.summary | type' 'string'

# 8. Restart persistence — restart backend, expect same data
section "Persistence across restart"
note "Stopping backend (PID $(cat "$PID_FILE"))"
kill "$(cat "$PID_FILE")" 2>/dev/null || true
# Wait for port to free.
for _ in $(seq 1 20); do
  if ! curl -sS -m 1 "$BACKEND_URL/api/v1/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Boot a second time with the same workspace + DB.
( cd "$REPO_ROOT" && \
    WORKSPACE_PATH="$WORKSPACE_DIR" \
    DATABASE_URL="postgresql+psycopg://$PG_USER:$PG_PASS@$PG_HOST:$PG_PORT/$PG_DB" \
    APP_ENV=verify \
    LOG_LEVEL=INFO \
    nohup uv run --project "$REPO_ROOT" uvicorn backend.main:app \
      --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-access-log \
      >"$EVIDENCE_DIR/02_restart_startup.log" 2>&1 &
    echo $! > "$PID_FILE"
)

for attempt in $(seq 1 60); do
  if curl -sS -m 2 "$BACKEND_URL/api/v1/health" >/dev/null 2>&1; then
    green "  Restarted backend responsive after ${attempt}s"
    break
  fi
  sleep 1
  if [[ $attempt -eq 60 ]]; then
    red "  Restarted backend did not respond within 60s"
    tail -80 "$EVIDENCE_DIR/02_restart_startup.log"
    exit 3
  fi
done
ok "Backend restarted"

# After restart, Alpha must still exist and search must find it.
assert_status 200 GET "/api/v1/stories/$ALPHA_ID" "" '.id' "$ALPHA_ID"
sleep 1
assert_status 200 GET "/api/v1/search?title=Alpha" "" '.hits[0].title' 'Alpha'

# ---- final report -------------------------------------------------------

section "Final report"

cat > "$EVIDENCE_DIR/REPORT.md" <<EOF
# Backend Validation Report

- Date: $(date -Iseconds)
- Backend URL: $BACKEND_URL
- Postgres: $PG_USER@$PG_HOST:$PG_PORT/$PG_DB
- Workspace: $WORKSPACE_DIR
- Log (initial boot): $LOG_FILE → 01_startup.log
- Log (restart): 02_restart_startup.log

## Result

- **PASS: $PASSES**
- **FAIL: $FAILS**

EOF

if [[ $FAILS -gt 0 ]]; then
  printf '## Failures\n\n```\n' >> "$EVIDENCE_DIR/REPORT.md"
  grep -h "FAIL " "$EVIDENCE_DIR"/*.log 2>/dev/null >> "$EVIDENCE_DIR/REPORT.md" || true
  printf '```\n' >> "$EVIDENCE_DIR/REPORT.md"
  red   "RESULT: $FAILS failure(s), $PASSES pass(es)"
  yellow "Report: $EVIDENCE_DIR/REPORT.md"
  exit 1
else
  green "RESULT: all $PASSES checks passed"
  yellow "Report: $EVIDENCE_DIR/REPORT.md"
  exit 0
fi
