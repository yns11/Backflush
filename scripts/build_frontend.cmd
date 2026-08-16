@echo off
rem ===========================================================================
rem Compile le frontend — enveloppe pour les postes Windows dont la stratégie
rem d'exécution PowerShell interdit les scripts .ps1.
rem
rem Un fichier .cmd n'est pas soumis à cette stratégie ; il relance le script
rem PowerShell dans un processus dont la stratégie est contournée, sans rien
rem modifier durablement sur la machine.
rem
rem   scripts\build_frontend.cmd
rem
rem Si la stratégie est imposée par une règle de groupe, le contournement est
rem lui aussi refusé : utilisez alors la procédure manuelle de
rem docs\DEPLOIEMENT.md (§2).
rem ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_frontend.ps1" %*
exit /b %errorlevel%
