#!/usr/bin/env bash
#
# review-v12.sh — levanta Radal v12 con un MUNDO DEMO fresco para revisión manual / con Claude en Chrome.
#
#   ./review-v12.sh          siembra /tmp/radal-review.db y levanta backend :8000 + frontend :5500
#   ./review-v12.sh stop     mata lo que quede en 8000 / 5500
#
# Seguro por diseño: IA, correo y RPA apagados (AI_API_KEY vacío y AI_BASE_URL a un puerto muerto,
# MAIL_ENABLED=false, RPA_ENABLED=false). Nada sale a internet. La base es desechable (/tmp).
# Las cuotas del demo se calculan respecto de HOY: vuelve a correrlo el día de la revisión.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACK="$ROOT/backend"; FRONT="$ROOT/frontend"
BACK_PORT=8000; FRONT_PORT=5500
DB_FILE=/tmp/radal-review.db
MEDIA_DIR=/tmp/radal-review-media
LOG_DIR=/tmp/radal-review-logs

free_port() {
  local pids; pids="$(lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true)"
  [[ -n "$pids" ]] && { echo "  puerto $1 ocupado (PID $pids) — lo cierro"; kill $pids 2>/dev/null || true; sleep 1; }
  return 0
}

if [[ "${1:-}" == "stop" ]]; then free_port $BACK_PORT; free_port $FRONT_PORT; exit 0; fi

# One environment for the seeder AND the server: the seeder encrypts demo secrets with
# CREDENTIALS_KEY/SECRET_KEY and writes PDFs to MEDIA_LOCAL_DIR — the server must read both back.
export DATABASE_URL="sqlite:///$DB_FILE"
export MEDIA_BACKEND=local MEDIA_LOCAL_DIR="$MEDIA_DIR"
export AI_API_KEY="" AI_BASE_URL="http://127.0.0.1:9/no-ai-in-review"
export MAIL_ENABLED=false RPA_ENABLED=false RPA_INLINE=false
export CORS_ORIGINS="http://localhost:$FRONT_PORT,http://127.0.0.1:$FRONT_PORT"
export SECRET_KEY="${SECRET_KEY:-review-v12-secret-key}"
export CREDENTIALS_KEY="${CREDENTIALS_KEY:-7Q7m0Zg2bV7z9o3cN1qk4cVb3o0VQm3tq9cX8z7pR6s=}"

free_port $BACK_PORT; free_port $FRONT_PORT
rm -f "$DB_FILE"; rm -rf "$MEDIA_DIR"; mkdir -p "$MEDIA_DIR" "$LOG_DIR"

echo "== sembrando mundo demo en $DB_FILE"
( cd "$BACK" && .venv/bin/python -m scripts.seed_v12_demo --media-dir "$MEDIA_DIR" ) | tee "$LOG_DIR/seed.txt" || {
  echo "  ✗ falló la siembra (ver arriba)"; exit 1; }

echo "== backend :$BACK_PORT (log $LOG_DIR/backend.log)"
( cd "$BACK" && .venv/bin/uvicorn app.main:app --port $BACK_PORT >"$LOG_DIR/backend.log" 2>&1 ) &
echo "== frontend :$FRONT_PORT (log $LOG_DIR/frontend.log)"
( cd "$FRONT" && VITE_API_URL="http://localhost:$BACK_PORT/api/v1" npx vite --port $FRONT_PORT --strictPort >"$LOG_DIR/frontend.log" 2>&1 ) &

for _ in $(seq 1 60); do
  curl -fsS "http://localhost:$BACK_PORT/api/v1/health" >/dev/null 2>&1 && curl -fsS "http://localhost:$FRONT_PORT" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://localhost:$BACK_PORT/api/v1/health" >/dev/null 2>&1 || { echo "  ✗ el backend no respondió"; tail -20 "$LOG_DIR/backend.log"; exit 1; }

cat <<EOF

  ✓ Radal v12 listo:  http://localhost:$FRONT_PORT
    corredora admin   admin@demo.cl / radal1234
    ejecutivo         ejecutivo@demo.cl / radal1234
    plataforma Radal  admin@radal.cl / radal1234
    links públicos    ver "share links" arriba (anteponer http://localhost:$FRONT_PORT)

  Ctrl-C detiene todo (o ./review-v12.sh stop).
EOF
trap 'free_port $BACK_PORT; free_port $FRONT_PORT; exit 0' INT TERM
wait
