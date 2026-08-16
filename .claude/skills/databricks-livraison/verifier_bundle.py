#!/usr/bin/env python3
"""Contrôles avant déploiement d'un bundle Databricks (DAB).

Chaque contrôle correspond à une panne réellement observée en production, dont
le symptôme ne désignait pas la cause. Tous sont **statiques** : ils lisent le
dépôt, ne contactent aucun espace de travail, et s'exécutent en moins d'une
seconde. Ils ont donc leur place dans la suite de tests, pas seulement dans les
mains d'un opérateur.

    python .claude/skills/databricks-livraison/verifier_bundle.py [racine]

Sortie : liste des anomalies, code de retour 1 s'il y en a. Aucune anomalie ne
garantit un déploiement réussi — elle garantit seulement que les erreurs déjà
payées ne se reproduisent pas.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Anomalie:
    """Un défaut détecté, avec le symptôme qu'il produirait en production.

    ``bloquant`` distingue ce qui est certainement faux de ce qui mérite un
    regard. Un outil qui crie au loup sur des cas légitimes finit ignoré : les
    points de vigilance s'affichent, mais ne font pas échouer la vérification.
    """

    ou: str
    quoi: str
    symptome: str
    bloquant: bool = True

    def __str__(self) -> str:
        marque = "✗" if self.bloquant else "▲"
        prefixe = "en production" if self.bloquant else "à vérifier"
        return f"  {marque} {self.ou}\n    {self.quoi}\n    → {prefixe} : {self.symptome}"


# ---------------------------------------------------------------------------
# Lecture du bundle
# ---------------------------------------------------------------------------
def charger_yaml(chemin: Path) -> dict:
    try:
        return yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as erreur:
        raise SystemExit(f"YAML illisible : {chemin} — {erreur}") from erreur


def fichiers_bundle(racine: Path) -> list[Path]:
    """databricks.yml et tous les fichiers de resources/."""
    fichiers = [racine / "databricks.yml"]
    fichiers += sorted((racine / "resources").glob("*.yml"))
    fichiers += sorted((racine / "resources").glob("*.yaml"))
    return [f for f in fichiers if f.is_file()]


def collecter(racine: Path, genre: str) -> list[tuple[Path, str, dict]]:
    """Retourne (fichier, clé, définition) pour chaque ressource du genre donné."""
    trouvees: list[tuple[Path, str, dict]] = []
    for fichier in fichiers_bundle(racine):
        ressources = charger_yaml(fichier).get("resources") or {}
        for cle, definition in (ressources.get(genre) or {}).items():
            trouvees.append((fichier, cle, definition or {}))
    return trouvees


# ---------------------------------------------------------------------------
# Contrôles — applications
# ---------------------------------------------------------------------------
def controler_applications(racine: Path) -> list[Anomalie]:
    anomalies: list[Anomalie] = []

    for fichier, cle, application in collecter(racine, "apps"):
        origine = f"{fichier.relative_to(racine)} › apps.{cle}"
        chemin_source = application.get("source_code_path")
        if not chemin_source:
            anomalies.append(Anomalie(origine, "source_code_path absent.",
                                      "déploiement refusé."))
            continue

        dossier = (fichier.parent / chemin_source).resolve()
        manifeste_chemin = dossier / "app.yaml"
        if not manifeste_chemin.is_file():
            anomalies.append(Anomalie(
                origine, f"{manifeste_chemin} introuvable.",
                "l'application n'a pas de manifeste et ne démarre pas."))
            continue

        manifeste = charger_yaml(manifeste_chemin)
        anomalies += _controler_commande(origine, dossier, manifeste)
        anomalies += _controler_ressources(origine, application, manifeste)

    return anomalies


def _controler_commande(origine: str, dossier: Path, manifeste: dict) -> list[Anomalie]:
    """La cible de démarrage doit être résolvable DANS le conteneur.

    Databricks Apps déploie le CONTENU de source_code_path à la racine : un
    chemin de module valide depuis la racine du dépôt (« app.server.main:app »)
    ne l'est plus une fois déployé.
    """
    commande = manifeste.get("command") or []
    if len(commande) < 2 or commande[0] not in ("uvicorn", "gunicorn"):
        return []           # Autre serveur : hors du périmètre de ce contrôle.

    module = str(commande[1]).split(":")[0]
    fichier_module = dossier / (module.replace(".", "/") + ".py")
    paquet_module = dossier / module.replace(".", "/") / "__init__.py"
    if fichier_module.is_file() or paquet_module.is_file():
        return []

    return [Anomalie(
        f"{origine} › app.yaml",
        f"La cible « {commande[1]} » ne se résout pas depuis {dossier.name}/ : "
        f"{fichier_module.name} y est introuvable. Le CONTENU de ce dossier est "
        "déployé à la racine du conteneur ; le dossier lui-même n'est pas un paquet.",
        "ModuleNotFoundError en boucle, chaque worker meurt, « App Not Available ».")]


#: Variables qu'une ressource « postgres » N'INJECTE PAS d'elle-même : elles
#: doivent être réclamées par un `valueFrom`. Les autres (PGHOST, PGPORT,
#: PGDATABASE, PGUSER, PGSSLMODE) sont fournies automatiquement.
A_RECLAMER = {"postgres": "LAKEBASE_ENDPOINT"}


def _controler_ressources(origine: str, application: dict, manifeste: dict) -> list[Anomalie]:
    anomalies: list[Anomalie] = []
    declarees: dict[str, str] = {}

    for ressource in application.get("resources") or []:
        nom = ressource.get("name", "?")
        genres = [c for c in ressource if c not in ("name", "description")]
        if len(genres) != 1:
            anomalies.append(Anomalie(f"{origine} › resources.{nom}",
                                      f"Type de ressource ambigu : {genres}.",
                                      "déploiement refusé."))
            continue
        genre = genres[0]
        declarees[nom] = genre

        if genre == "database":
            anomalies.append(Anomalie(
                f"{origine} › resources.{nom}",
                "Clé « database » dépréciée (génération « database instance »).",
                "« Database instance <nom> does not exist » (404) au déploiement, "
                "alors même que le projet Lakebase existe. Utiliser « postgres »."))

        if genre == "postgres":
            for champ in ("branch", "database"):
                valeur = str((ressource[genre] or {}).get(champ, ""))
                if valeur and "${" not in valeur and not valeur.startswith("projects/"):
                    anomalies.append(Anomalie(
                        f"{origine} › resources.{nom}.{champ}",
                        f"« {valeur} » n'est pas un chemin de ressource complet.",
                        "404 au déploiement : ces champs attendent "
                        "projects/<projet>/branches/<branche>[/databases/<base>]."))

    injections = {
        entree["name"]: entree["valueFrom"]
        for entree in (manifeste.get("env") or [])
        if isinstance(entree, dict) and "valueFrom" in entree
    }

    for variable, ressource in injections.items():
        if ressource not in declarees:
            anomalies.append(Anomalie(
                f"{origine} › app.yaml › env.{variable}",
                f"valueFrom « {ressource} » ne désigne aucune ressource déclarée "
                f"(déclarées : {sorted(declarees) or 'aucune'}).",
                "variable vide au démarrage, panne fonctionnelle silencieuse."))

    for nom, genre in declarees.items():
        attendue = A_RECLAMER.get(genre)
        if attendue and injections.get(attendue) != nom:
            anomalies.append(Anomalie(
                f"{origine} › app.yaml › env",
                f"La ressource « {nom} » ({genre}) est attachée, mais {attendue} "
                f"n'est pas réclamée (« - name: {attendue} / valueFrom: {nom} »).",
                "l'application démarre, obtient l'hôte, mais aucun moyen de "
                "générer un jeton : 503 sur toutes les routes de données — "
                "symptôme indiscernable d'une ressource non attachée."))

    return anomalies


# ---------------------------------------------------------------------------
# Contrôles — synchronisation des artefacts de build
# ---------------------------------------------------------------------------
def controler_synchronisation(racine: Path) -> list[Anomalie]:
    """Un bundle exclut par défaut tout ce que .gitignore ignore.

    Un frontend compilé, un wheel, un fichier généré : présents localement,
    absents du déploiement, sans le moindre message d'erreur.
    """
    gitignore = racine / ".gitignore"
    if not gitignore.is_file():
        return []

    inclusions = " ".join(
        str(motif) for fichier in fichiers_bundle(racine)
        for motif in ((charger_yaml(fichier).get("sync") or {}).get("include") or [])
    )

    anomalies: list[Anomalie] = []
    for _, cle, application in collecter(racine, "apps"):
        chemin_source = application.get("source_code_path")
        if not chemin_source:
            continue
        dossier = (racine / "resources" / chemin_source).resolve()
        if not dossier.is_dir():
            continue

        for ligne in gitignore.read_text(encoding="utf-8").splitlines():
            motif = ligne.strip().rstrip("/")
            if not motif or motif.startswith(("#", "!")):
                continue
            candidat = racine / motif.lstrip("/")
            # Seuls les chemins ignorés SITUÉS dans le code de l'application
            # comptent : eux seuls manqueraient au déploiement.
            if not candidat.exists() or dossier not in candidat.resolve().parents:
                continue
            if motif.split("/")[-1] not in inclusions:
                anomalies.append(Anomalie(
                    f"apps.{cle} › {motif}",
                    "Chemin ignoré par Git et situé dans le code de l'application, "
                    "sans `sync.include` correspondant dans le bundle.",
                    "déploiement réussi, application démarrée, API fonctionnelle… "
                    "et ces fichiers absents. Aucune erreur nulle part."))
    return anomalies


# ---------------------------------------------------------------------------
# Contrôles — jobs
# ---------------------------------------------------------------------------
MOTIF_SORTIE = re.compile(r"^\s*(raise SystemExit\(|sys\.exit\()", re.MULTILINE)
MOTIF_AMORCE = re.compile(r"sys\.path")


def controler_jobs(racine: Path) -> list[Anomalie]:
    anomalies: list[Anomalie] = []
    # Paquets de premier niveau : ceux du dépôt qu'une tâche pourrait importer.
    paquets = {
        chemin.name for chemin in racine.iterdir()
        if chemin.is_dir()
        and ((chemin / "__init__.py").is_file() or chemin.name in ("src", "app"))
    }

    for fichier, cle, job in collecter(racine, "jobs"):
        for tache in job.get("tasks") or []:
            specification = tache.get("spark_python_task") or {}
            chemin_relatif = specification.get("python_file")
            if not chemin_relatif:
                continue

            origine = f"{fichier.relative_to(racine)} › jobs.{cle}.{tache.get('task_key', '?')}"
            script = (fichier.parent / chemin_relatif).resolve()
            if not script.is_file():
                anomalies.append(Anomalie(
                    origine, f"python_file introuvable : {chemin_relatif}.",
                    "la tâche échoue immédiatement au lancement."))
                continue

            source = script.read_text(encoding="utf-8")

            if MOTIF_SORTIE.search(source):
                anomalies.append(Anomalie(
                    origine,
                    f"{script.name} sort par SystemExit / sys.exit.",
                    "le noyau qui exécute la tâche traite SystemExit comme une "
                    "exception : la tâche est rouge même après un succès complet."))

            importe_le_depot = any(
                re.search(rf"^\s*(from|import)\s+{paquet}\b", source, re.MULTILINE)
                for paquet in paquets
            )
            if importe_le_depot and not MOTIF_AMORCE.search(source):
                anomalies.append(Anomalie(
                    origine,
                    f"{script.name} importe un paquet du dépôt sans amorcer sys.path.",
                    "ModuleNotFoundError : une tâche spark_python_task évalue le "
                    "fichier sans la racine du bundle dans sys.path."))

    return anomalies


# ---------------------------------------------------------------------------
# Contrôles — variables vides transmises en paramètre
# ---------------------------------------------------------------------------
MOTIF_PARAMETRE = re.compile(r"--([a-z0-9-]+)=\$\{var\.([a-z0-9_]+)\}")


def controler_variables_vides(racine: Path) -> list[Anomalie]:
    """Une variable de bundle vide n'arrive pas « absente » : elle arrive vide.

    ``--app-role=${var.x}`` avec ``x`` vide produit ``--app-role=``, donc un
    argument présent et vide — que le script doit écarter explicitement.
    """
    defauts: dict[str, Any] = {}
    surchargees: set[str] = set()
    for fichier in fichiers_bundle(racine):
        contenu = charger_yaml(fichier)
        for nom, definition in (contenu.get("variables") or {}).items():
            if isinstance(definition, dict):
                defauts[nom] = definition.get("default")
        # Une variable renseignée dans une cible n'est pas « vide » en pratique.
        for cible in (contenu.get("targets") or {}).values():
            surchargees |= set((cible or {}).get("variables") or {})

    vides = {
        nom for nom, valeur in defauts.items()
        if valeur == "" and nom not in surchargees
    }
    if not vides:
        return []

    anomalies: list[Anomalie] = []
    for fichier in fichiers_bundle(racine):
        for parametre, variable in MOTIF_PARAMETRE.findall(fichier.read_text(encoding="utf-8")):
            if variable in vides:
                anomalies.append(Anomalie(
                    f"{fichier.relative_to(racine)} › --{parametre}",
                    f"La variable « {variable} » vaut \"\" par défaut, n'est "
                    "surchargée dans aucune cible, et est transmise telle quelle.",
                    "le script reçoit une chaîne vide, pas une absence. S'assurer "
                    "qu'il l'écarte — une chaîne vide utilisée comme identifiant "
                    "SQL produit « zero-length delimited identifier ».",
                    bloquant=False))
    return anomalies


# ---------------------------------------------------------------------------
def verifier(racine: Path) -> list[Anomalie]:
    return [
        *controler_applications(racine),
        *controler_synchronisation(racine),
        *controler_jobs(racine),
        *controler_variables_vides(racine),
    ]


def main() -> None:
    racine = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not (racine / "databricks.yml").is_file():
        raise SystemExit(f"Aucun databricks.yml dans {racine}.")

    anomalies = verifier(racine)
    bloquantes = [a for a in anomalies if a.bloquant]

    if not anomalies:
        print(f"✓ {racine.name} : aucune anomalie connue avant déploiement.")
        return

    for anomalie in anomalies:
        print(anomalie, end="\n\n")

    if bloquantes:
        print(f"✗ {len(bloquantes)} anomalie(s) bloquante(s) à corriger avant de déployer.")
        raise SystemExit(1)
    print(f"✓ Aucune anomalie bloquante ({len(anomalies)} point(s) de vigilance).")


if __name__ == "__main__":
    main()
