#!/usr/bin/env bash
# =============================================================================
# Compile le frontend puis déploie le bundle — dans cet ordre, toujours.
#
# Raison d'être : `databricks bundle deploy` ne vérifie pas que l'interface a
# été compilée. Un déploiement sans elle réussit, l'application démarre, l'API
# répond, et il ne manque « que » le frontend — panne silencieuse, coûteuse à
# diagnostiquer. Ce script rend l'oubli impossible.
#
#   ./scripts/deploy.sh dev  PROFIL --var="lakebase_host=..." ...
#   ./scripts/deploy.sh prod PROFIL
#
# La recompilation est ignorée si aucune source frontend n'a changé depuis le
# dernier build : l'usage courant reste rapide.
# =============================================================================
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${racine}"

cible="${1:-dev}"
profil="${2:-}"
shift $(( $# > 2 ? 2 : $# )) || true

if [ -z "${profil}" ]; then
  echo "Usage : ./scripts/deploy.sh <cible> <profil> [--var=...]" >&2
  echo "Exemple : ./scripts/deploy.sh dev PROD --var=\"lakebase_host=ep-....com\"" >&2
  exit 1
fi

bundle="app/server/static/index.html"

recompiler=0
if [ ! -f "${bundle}" ]; then
  echo "→ Aucune interface compilée."
  recompiler=1
elif [ -n "$(find app/client/src app/client/index.html app/client/package.json \
              -newer "${bundle}" 2>/dev/null | head -1)" ]; then
  echo "→ Sources frontend plus récentes que le bundle."
  recompiler=1
fi

if [ "${recompiler}" -eq 1 ]; then
  "${racine}/scripts/build_frontend.sh"
else
  echo "✓ Interface à jour, compilation ignorée."
fi

echo "→ Validation du bundle (cible : ${cible})"
databricks bundle validate -t "${cible}" --profile "${profil}" "$@"

echo "→ Déploiement"
databricks bundle deploy -t "${cible}" --profile "${profil}" "$@"

echo "✓ Déployé. Vérifiez l'état :"
echo "  databricks apps get backflush-analytics-${cible} --profile ${profil} -o json | jq '.app_status'"
