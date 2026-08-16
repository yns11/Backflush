#!/usr/bin/env bash
# =============================================================================
# Compile le frontend React dans app/server/static.
#
# À exécuter AVANT tout déploiement : le bundle n'est pas versionné (c'est un
# artefact de build), et FastAPI le sert tel quel. Sans lui, l'API répond mais
# l'interface est absente.
#
#   ./scripts/build_frontend.sh
#
# npm introuvable ? Le script cherche l'exécutable aux emplacements usuels, y
# compris ceux d'une installation Windows vue depuis Git Bash. En dernier
# recours, imposez-le sans modifier ce fichier :
#
#   NPM="/c/Program Files/nodejs/npm" ./scripts/build_frontend.sh
#
# Sous PowerShell, utilisez plutôt scripts/build_frontend.ps1.
# =============================================================================
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
client="${racine}/app/client"
sortie="${racine}/app/server/static"

# --- Localisation de npm -----------------------------------------------------
# Une installation Node sous Windows n'est pas toujours dans le PATH de Git
# Bash. Plutôt que de coder un chemin en dur — qui casserait sur tout autre
# poste, en intégration continue comme chez un collègue — on le cherche.
trouver_npm() {
  if [ -n "${NPM:-}" ]; then
    printf '%s' "${NPM}"
    return 0
  fi
  if command -v npm >/dev/null 2>&1; then
    printf 'npm'
    return 0
  fi
  local candidat
  for candidat in \
    "/c/Program Files/nodejs/npm" \
    "/c/Program Files (x86)/nodejs/npm" \
    "/mnt/c/Program Files/nodejs/npm" \
    "${APPDATA:-}/npm/npm" \
    "/usr/local/bin/npm" \
    "/opt/homebrew/bin/npm"
  do
    if [ -x "${candidat}" ]; then
      printf '%s' "${candidat}"
      return 0
    fi
  done
  return 1
}

if ! npm_bin="$(trouver_npm)"; then
  cat >&2 <<'AIDE'
npm est introuvable.

  • Installez Node.js 20 ou supérieur : https://nodejs.org
  • Ou indiquez son emplacement sans modifier ce script :

      NPM="/c/Program Files/nodejs/npm" ./scripts/build_frontend.sh

  • Sous PowerShell, utilisez scripts/build_frontend.ps1.
AIDE
  exit 1
fi

echo "→ npm : ${npm_bin}"
"${npm_bin}" --version >/dev/null || {
  echo "npm a été trouvé à « ${npm_bin} » mais ne s'exécute pas." >&2
  exit 1
}

echo "→ Installation des dépendances"
# `npm ci` garantit une compilation reproductible à partir du fichier de
# verrouillage ; on retombe sur `npm install` si le verrou est absent.
if [ -f "${client}/package-lock.json" ]; then
  "${npm_bin}" --prefix "${client}" ci --no-fund --no-audit
else
  "${npm_bin}" --prefix "${client}" install --no-fund --no-audit
fi

echo "→ Vérification des types"
"${npm_bin}" --prefix "${client}" run typecheck

echo "→ Compilation"
"${npm_bin}" --prefix "${client}" run build

if [ ! -f "${sortie}/index.html" ]; then
  echo "Échec : ${sortie}/index.html est absent après compilation." >&2
  exit 1
fi

echo "✓ Bundle prêt dans ${sortie}"
du -sh "${sortie}"
