<#
.SYNOPSIS
    Compile le frontend React dans app/server/static.

.DESCRIPTION
    Équivalent PowerShell de scripts/build_frontend.sh, pour les postes Windows
    sans Git Bash.

    npm est localisé automatiquement, y compris hors PATH — cas courant d'une
    installation Node.js sous « C:\Program Files\nodejs ». Pour imposer un
    emplacement sans modifier ce script :

        $env:NPM = "C:\Program Files\nodejs\npm.cmd"

.EXAMPLE
    .\scripts\build_frontend.ps1
#>
#Requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$racine = Split-Path -Parent $PSScriptRoot
$client = Join-Path $racine 'app\client'
$sortie = Join-Path $racine 'app\server\static'

function Get-NpmPath {
    <#  Retourne le chemin de npm, ou lève une erreur explicite.  #>
    if ($env:NPM) {
        if (Test-Path $env:NPM) { return $env:NPM }
        throw "La variable NPM pointe sur « $env:NPM », qui n'existe pas."
    }

    $commande = Get-Command npm -ErrorAction SilentlyContinue
    if ($commande) { return $commande.Source }

    # Emplacements d'installation usuels, dans l'ordre de probabilité.
    $candidats = @(
        (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\npm.cmd'),
        (Join-Path $env:APPDATA 'npm\npm.cmd'),
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\npm.cmd')
    ) | Where-Object { $_ -and (Test-Path $_) }

    if ($candidats) { return $candidats[0] }

    throw @"
npm est introuvable.

  - Installez Node.js 20 ou superieur : https://nodejs.org
  - Ou indiquez son emplacement sans modifier ce script :

      `$env:NPM = "C:\Program Files\nodejs\npm.cmd"
"@
}

function Invoke-Npm {
    <#  Exécute npm et interrompt le script en cas d'échec.

        PowerShell n'interrompt pas sur le code de retour d'un exécutable
        externe : sans ce contrôle, une compilation en échec passerait
        inaperçue et l'on déploierait un bundle périmé.  #>
    param([Parameter(Mandatory)][string[]] $Arguments)

    & $script:npm @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "npm $($Arguments -join ' ') a échoué (code $LASTEXITCODE)."
    }
}

$script:npm = Get-NpmPath
Write-Host "-> npm : $script:npm"

Write-Host '-> Installation des dépendances'
# `npm ci` garantit une compilation reproductible à partir du fichier de
# verrouillage ; on retombe sur `npm install` si le verrou est absent.
if (Test-Path (Join-Path $client 'package-lock.json')) {
    Invoke-Npm @('--prefix', $client, 'ci', '--no-fund', '--no-audit')
} else {
    Invoke-Npm @('--prefix', $client, 'install', '--no-fund', '--no-audit')
}

Write-Host '-> Vérification des types'
Invoke-Npm @('--prefix', $client, 'run', 'typecheck')

Write-Host '-> Compilation'
Invoke-Npm @('--prefix', $client, 'run', 'build')

$index = Join-Path $sortie 'index.html'
if (-not (Test-Path $index)) {
    throw "Échec : $index est absent après compilation."
}

$taille = (Get-ChildItem $sortie -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("✓ Bundle prêt dans {0} ({1:N0} Ko)" -f $sortie, ($taille / 1KB))
