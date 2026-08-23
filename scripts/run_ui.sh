#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "No se ha encontrado el Python del proyecto en .venv/bin/python."
  echo "Abre el proyecto en Codex o crea el entorno virtual antes de lanzar LXCell."
  read -r -p "Pulsa Enter para cerrar..."
  exit 1
fi

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}/src"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

URL="http://localhost:8501"
PORT="8501"

echo "Arrancando LXCell..."
echo "URL local: ${URL}"
echo "Cierra esta ventana o pulsa Ctrl+C para parar la aplicación."

if command -v lsof >/dev/null 2>&1; then
  while IFS= read -r PID; do
    [[ -z "${PID}" ]] && continue
    PROCESS_CWD="$(lsof -a -p "${PID}" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)"
    if [[ "${PROCESS_CWD}" == "${PROJECT_DIR}" ]]; then
      echo "Parando una instancia anterior de LXCell en el puerto ${PORT} (PID ${PID})..."
      kill "${PID}" 2>/dev/null || true
      sleep 1
    else
      echo "El puerto ${PORT} ya está en uso por otro proceso (PID ${PID})."
      echo "Cierra ese proceso o cambia el puerto antes de arrancar LXCell."
      read -r -p "Pulsa Enter para cerrar..."
      exit 1
    fi
  done < <(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)
fi

if command -v open >/dev/null 2>&1; then
  (sleep 3; open "${URL}" >/dev/null 2>&1 || true) &
fi

exec "${PYTHON_BIN}" -m streamlit run src/lxcell/ui/streamlit_app.py \
  --server.address localhost \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
