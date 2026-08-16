@echo off
rem ===========================================================================
rem Compile puis déploie — enveloppe pour les postes Windows dont la stratégie
rem d'exécution PowerShell interdit les scripts .ps1.
rem
rem   scripts\deploy.cmd dev PROD --var="lakebase_host=..." --var="..."
rem
rem Si la stratégie est imposée par une règle de groupe, le contournement est
rem lui aussi refusé : utilisez alors la procédure manuelle de
rem docs\DEPLOIEMENT.md (§3).
rem ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
exit /b %errorlevel%
