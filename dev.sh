#!/usr/bin/env bash
#
# dev.sh — levanta backend (:8000) y frontend (:5500) en UNA sola pantalla.
#
#   ./dev.sh              backend + frontend, logs mezclados y etiquetados
#   ./dev.sh back         solo el backend
#   ./dev.sh front        solo el frontend
#   ./dev.sh stop         mata lo que haya quedado escuchando en 8000 / 5500
#   ./dev.sh --quiet      arranca sin seguir los logs (solo el chequeo de salud)
#
# Por qué existe, y no un "uvicorn + npm run dev" a mano:
#
#   1. MEDIA_BACKEND. backend/.env trae `s3`, pero a S3 nunca se subió nada:
#      los dos importadores corrieron con --no-upload y espejaron los bytes en
#      backend/media/. Sin el override a `local`, TODA descarga, extracción con
#      IA y ZIP de carpeta responde 404. Este script lo fuerza siempre.
#   2. Servidores zombis. Los dos servidores corren con --reload y quedan
#      huérfanos con facilidad; el síntoma es un cambio que "no aparece".
#      Aquí se libera el puerto antes de arrancar, mostrando qué se mató.
#   3. Un solo Ctrl-C. Los dos procesos viven en el mismo grupo y se apagan
#      juntos, sin dejar el puerto tomado para la próxima sesión.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACK="$ROOT/backend"
FRONT="$ROOT/frontend"
BACK_PORT=8000
FRONT_PORT=5500
LOG_DIR="${TMPDIR:-/tmp}"; LOG_DIR="${LOG_DIR%/}/radal-dev"

# Colores solo si la salida es un terminal (así el log a archivo queda limpio).
if [[ -t 1 ]]; then
  C_BACK=$'\033[38;5;37m'   # teal, como la marca
  C_FRONT=$'\033[38;5;68m'  # azul de acción
  C_OK=$'\033[38;5;107m'; C_WARN=$'\033[38;5;179m'; C_ERR=$'\033[38;5;167m'
  C_DIM=$'\033[2m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_BACK=""; C_FRONT=""; C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_B=""; C_0=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$C_OK" "$C_0" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_0" "$*"; }
die()  { printf '  %s✗%s %s\n' "$C_ERR" "$C_0" "$*" >&2; exit 1; }
rule() { printf '%s────────────────────────────────────────────────────────────%s\n' "$C_DIM" "$C_0"; }

# --- Puertos ------------------------------------------------------------------

port_pids() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true; }

free_port() { # free_port <puerto> <etiqueta>
  local pids; pids="$(port_pids "$1")"
  [[ -z "$pids" ]] && return 0
  local when
  for pid in $pids; do
    when="$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//')"
    warn "puerto $1 ocupado por PID $pid (desde ${when:-?}) — lo cierro"
    kill "$pid" 2>/dev/null || true
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [[ -z "$(port_pids "$1")" ]] && { ok "puerto $1 libre ($2)"; return 0; }
    sleep 0.3
  done
  for pid in $(port_pids "$1"); do kill -9 "$pid" 2>/dev/null || true; done
  sleep 0.5
  [[ -z "$(port_pids "$1")" ]] || die "no pude liberar el puerto $1"
  ok "puerto $1 libre ($2)"
}

# --- Prerrequisitos -----------------------------------------------------------

check_backend() {
  [[ -x "$BACK/.venv/bin/uvicorn" ]] || die \
    "falta el venv del backend. Corré:
     cd backend && uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python"
  [[ -f "$BACK/.env" ]] || warn "backend/.env no existe — se usarán los defaults (AI_API_KEY vacío)"

  if [[ ! -f "$BACK/radal.db" ]]; then
    warn "no hay backend/radal.db todavía. Para cargar el mundo demo:"
    say  "     cd backend"
    say  "     MEDIA_BACKEND=local .venv/bin/python -m app.db.import_fixtures --reset --no-upload"
    say  "     MEDIA_BACKEND=local .venv/bin/python -m app.db.import_expedientes \\"
    say  "         --source \"\$HOME/Downloads/EXPEDIENTES DEMO\" --no-upload"
    return
  fi

  # Cuántos grupos hay: si son 0, la barra lateral abre vacía y parece un bug.
  local groups
  groups="$(sqlite3 "$BACK/radal.db" \
    "select count(*) from account_group" 2>/dev/null || echo "?")"
  if [[ "$groups" == "0" ]]; then
    warn "la base existe pero no tiene grupos — reimportá el corpus o la barra abrirá vacía"
  elif [[ "$groups" =~ ^[0-9]+$ ]]; then
    local cases
    cases="$(sqlite3 "$BACK/radal.db" \
      "select count(*) from case_file where kind in ('account','renewal')" 2>/dev/null || echo "?")"
    ok "base de datos lista · $groups grupos · $cases cuentas"
  else
    ok "base de datos lista"   # sin sqlite3 a mano: no es motivo para no arrancar
  fi
}

check_frontend() {
  [[ -d "$FRONT/node_modules" ]] || die "faltan dependencias. Corré: cd frontend && npm install"
  ok "dependencias del frontend listas"
}

# --- Arranque -----------------------------------------------------------------

# Etiqueta cada línea con su origen y la reemite. Así los dos servidores caben
# en una pantalla sin confundir quién dijo qué.
prefix() { # prefix <color> <etiqueta>
  local color="$1" tag="$2"
  while IFS= read -r line; do
    printf '%s%-5s%s │ %s\n' "$color" "$tag" "$C_0" "$line"
  done
}

start_backend() {
  free_port "$BACK_PORT" backend
  mkdir -p "$LOG_DIR"
  (
    cd "$BACK" || exit 1
    # MEDIA_BACKEND=local es el override que hace que los documentos existan.
    MEDIA_BACKEND=local exec .venv/bin/uvicorn app.main:app \
      --reload --port "$BACK_PORT" --log-level info 2>&1
  ) > >(tee "$LOG_DIR/backend.log" | prefix "$C_BACK" "api") &
  BACK_PID=$!
}

start_frontend() {
  free_port "$FRONT_PORT" frontend
  mkdir -p "$LOG_DIR"
  (
    cd "$FRONT" || exit 1
    exec npm run dev 2>&1
  ) > >(tee "$LOG_DIR/frontend.log" | prefix "$C_FRONT" "web") &
  FRONT_PID=$!
}

wait_http() { # wait_http <url> <segundos>
  local url="$1" secs="${2:-40}" i=0
  while (( i < secs * 2 )); do
    curl -sf -m 2 "$url" >/dev/null 2>&1 && return 0
    sleep 0.5; i=$((i + 1))
  done
  return 1
}

shutdown() {
  trap - INT TERM EXIT
  printf '\n'
  rule
  say "  cerrando…"
  [[ -n "${BACK_PID:-}"  ]] && kill "$BACK_PID"  2>/dev/null
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null
  sleep 1
  # El --reload de uvicorn y el de vite dejan hijos: barré los puertos igual.
  for p in "$BACK_PORT" "$FRONT_PORT"; do
    for pid in $(port_pids "$p"); do kill -9 "$pid" 2>/dev/null || true; done
  done
  ok "puertos $BACK_PORT y $FRONT_PORT liberados"
  exit 0
}

banner() {
  rule
  printf '  %sRadal%s · entorno local\n' "$C_B" "$C_0"
  printf '  %sAPI%s   http://localhost:%s/api/v1   %sdocs en /docs%s\n' \
    "$C_BACK" "$C_0" "$BACK_PORT" "$C_DIM" "$C_0"
  printf '  %sApp%s   http://localhost:%s            %sabre en /groups%s\n' \
    "$C_FRONT" "$C_0" "$FRONT_PORT" "$C_DIM" "$C_0"
  printf '  %sEntrá con usuario11@fuenzalidasr.cl / radal1234 → Grupo Viña Indómita%s\n' "$C_DIM" "$C_0"
  printf '  %slogs en %s · Ctrl-C corta los dos%s\n' "$C_DIM" "$LOG_DIR" "$C_0"
  rule
}

# --- Main ---------------------------------------------------------------------

MODE="both"; FOLLOW=1
for arg in "$@"; do
  case "$arg" in
    back|backend)   MODE="back" ;;
    front|frontend) MODE="front" ;;
    stop)           MODE="stop" ;;
    -q|--quiet)     FOLLOW=0 ;;
    -h|--help)      sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)              die "no conozco la opción '$arg' (probá: back | front | stop | --quiet)" ;;
  esac
done

if [[ "$MODE" == "stop" ]]; then
  rule; say "  liberando puertos…"
  if [[ -z "$(port_pids "$BACK_PORT")$(port_pids "$FRONT_PORT")" ]]; then
    ok "no había nada escuchando en $BACK_PORT ni en $FRONT_PORT"
  else
    free_port "$BACK_PORT" backend
    free_port "$FRONT_PORT" frontend
  fi
  rule
  exit 0
fi

rule
say "  revisando el entorno…"
[[ "$MODE" != "front" ]] && check_backend
[[ "$MODE" != "back"  ]] && check_frontend
rule

trap shutdown INT TERM EXIT

[[ "$MODE" != "front" ]] && start_backend
[[ "$MODE" != "back"  ]] && start_frontend

if [[ "$MODE" != "front" ]]; then
  if wait_http "http://localhost:$BACK_PORT/api/v1/health" 45; then
    ok "backend arriba en :$BACK_PORT"
  else
    die "el backend no respondió en 45 s — mirá $LOG_DIR/backend.log"
  fi
fi
if [[ "$MODE" != "back" ]]; then
  if wait_http "http://localhost:$FRONT_PORT" 60; then
    ok "frontend arriba en :$FRONT_PORT"
  else
    die "el frontend no respondió en 60 s — mirá $LOG_DIR/frontend.log"
  fi
fi

banner

if (( FOLLOW )); then
  # Quedarse en primer plano: los logs ya se están reemitiendo etiquetados.
  wait
else
  say "  corriendo en segundo plano. Para cerrarlos: ./dev.sh stop"
  trap - INT TERM EXIT
  exit 0
fi
