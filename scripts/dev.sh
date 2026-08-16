#!/usr/bin/env bash
# =============================================================================
# Démarre l'environnement de développement complet :
#   • un Postgres local rempli de données de démonstration (si demandé) ;
#   • l'API FastAPI en rechargement automatique sur :8000 ;
#   • le serveur Vite sur :5173, qui relaie /api vers :8000.
#
#   ./scripts/dev.sh            # utilise LAKEBASE_PG_URL déjà défini
#   ./scripts/dev.sh --seed     # (re)génère le jeu de démonstration
# =============================================================================
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${racine}"

if [ -z "${LAKEBASE_PG_URL:-}" ]; then
  echo "LAKEBASE_PG_URL n'est pas défini." >&2
  echo "Exemple : export LAKEBASE_PG_URL=\"postgresql://postgres:mdp@localhost:5432/postgres\"" >&2
  exit 1
fi

if [ "${1:-}" = "--seed" ]; then
  echo "→ Génération du jeu de démonstration"
  # L'historique démarre au 30 mars 2026, comme en préproduction et en
  # production (variable de bundle `date_from`) : le défaut du script.
  python -m src.jobs.seed_demo_data
fi

echo "→ API sur http://127.0.0.1:8000 (documentation : /api/docs)"
uvicorn app.server.main:app --reload --port 8000 &
api_pid=$!
# Sans ce piège, l'API resterait en arrière-plan après un Ctrl+C sur Vite et
# occuperait le port au prochain démarrage.
trap 'kill ${api_pid} 2>/dev/null || true' EXIT

echo "→ Interface sur http://127.0.0.1:5173"
npm --prefix app/client run dev
