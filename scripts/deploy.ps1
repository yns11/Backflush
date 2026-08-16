<#
.SYNOPSIS
    Compile le frontend si nécessaire, valide le bundle, puis déploie.

.DESCRIPTION
    `databricks bundle deploy` ne vérifie pas que l'interface a été compilée.
    Un déploiement sans elle réussit, l'application démarre, l'API répond, et il
    ne manque « que » le frontend — panne silencieuse, coûteuse à diagnostiquer.
    Ce script rend l'oubli impossible.

    La compilation est ignorée si aucune source frontend n'a changé depuis le
    dernier build : l'usage courant reste immédiat.

.PARAMETER Cible
    Cible du bundle : dev, preprod ou prod.

.PARAMETER Profil
    Profil de la CLI Databricks.

.PARAMETER Variables
    Arguments supplémentaires transmis tels quels à la CLI, typiquement --var.

.EXAMPLE
    .\scripts\deploy.ps1 dev PROD `
        --var="lakebase_host=ep-xxx.database.eu-west-1.cloud.databricks.com" `
        --var="lakebase_endpoint=projects/backflush/branches/production/endpoints/primary" `
        --var="lakebase_branch=projects/backflush/branches/production" `
        --var="lakebase_database_path=projects/backflush/branches/production/databases/databricks-postgres"
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Cible,
    [Parameter(Mandatory)][string] $Profil,
    [Parameter(ValueFromRemainingArguments)][string[]] $Variables = @()
)

$ErrorActionPreference = 'Stop'

$racine = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $racine 'app\server\static\index.html'

function Test-BundleAJour {
    <#  Vrai si le bundle compilé est postérieur à toute source frontend.  #>
    if (-not (Test-Path $bundle)) {
        Write-Host '-> Aucune interface compilée.'
        return $false
    }
    $reference = (Get-Item $bundle).LastWriteTimeUtc
    $sources = @(
        (Join-Path $racine 'app\client\src'),
        (Join-Path $racine 'app\client\index.html'),
        (Join-Path $racine 'app\client\package.json')
    ) | Where-Object { Test-Path $_ }

    $plusRecent = Get-ChildItem $sources -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -gt $reference } |
        Select-Object -First 1

    if ($plusRecent) {
        Write-Host "-> Source plus récente que le bundle : $($plusRecent.Name)"
        return $false
    }
    return $true
}

if (Test-BundleAJour) {
    Write-Host '✓ Interface à jour, compilation ignorée.'
} else {
    & (Join-Path $PSScriptRoot 'build_frontend.ps1')
}

Write-Host "-> Validation du bundle (cible : $Cible)"
& databricks bundle validate -t $Cible --profile $Profil @Variables
if ($LASTEXITCODE -ne 0) { throw "La validation du bundle a échoué (code $LASTEXITCODE)." }

Write-Host '-> Déploiement'
& databricks bundle deploy -t $Cible --profile $Profil @Variables
if ($LASTEXITCODE -ne 0) { throw "Le déploiement a échoué (code $LASTEXITCODE)." }

Write-Host '✓ Déployé. Vérifiez l''état :'
Write-Host "  databricks apps get backflush-analytics-$Cible --profile $Profil -o json | ConvertFrom-Json | Select-Object -Expand app_status"
