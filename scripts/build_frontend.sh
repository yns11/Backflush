#!/usr/bin/env bash
# =============================================================================
# Compile le frontend React dans app/server/static.
#
# À exécuter AVANT tout déploiement : le bundle n'est pas versionné (c'est un
# artefact de build), et FastAPI le sert tel quel. Sans lui, l'API répond mais
# l'interface est absente.
#
#   ./scripts/build_frontend.sh
# =============================================================================
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
client="${racine}/app/client"
sortie="${racine}/app/server/static"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm est introuvable. Installez Node.js 20 ou supérieur." >&2
  exit 1
fi

echo "→ Installation des dépendances"
# `npm ci` garantit une compilation reproductible à partir du fichier de
# verrouillage ; on retombe sur `npm install` si le verrou est absent.
if [ -f "${client}/package-lock.json" ]; then
  npm --prefix "${client}" ci --no-fund --no-audit
else
  npm --prefix "${client}" install --no-fund --no-audit
fi

echo "→ Vérification des types"
npm --prefix "${client}" run typecheck

echo "→ Compilation"
npm --prefix "${client}" run build

if [ ! -f "${sortie}/index.html" ]; then
  echo "Échec : ${sortie}/index.html est absent après compilation." >&2
  exit 1
fi

echo "✓ Bundle prêt dans ${sortie}"
du -sh "${sortie}"
