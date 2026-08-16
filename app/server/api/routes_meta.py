"""Routes de métadonnées : santé, options de filtres, dictionnaire, fraîcheur."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter

from app.server.api.deps import PoolDep, RepositoryDep, SettingsDep, UtilisateurDep
from app.server.domain.dictionary import GRILLES

routeur = APIRouter(prefix="/api", tags=["méta"])


@routeur.get("/health", summary="État de l'application et de sa base")
def health(pool: PoolDep, settings: SettingsDep) -> dict:
    """Sonde de disponibilité. Répond 200 même base indisponible.

    Un ``503`` ici masquerait la cause : l'application distingue « je tourne »
    de « ma base répond », ce qui rend le diagnostic immédiat dans les journaux
    de la plateforme.
    """
    return {"application": settings.resume(), "base": pool.ping()}


@routeur.get("/whoami", summary="Identité du visiteur")
def whoami(utilisateur: UtilisateurDep) -> dict:
    identifie = bool(utilisateur.get("email") or utilisateur.get("utilisateur"))
    return {
        **utilisateur,
        "identifie": identifie,
        "contexte": "databricks_apps" if identifie else "developpement_local",
        # L'application interroge Lakebase avec le principal de service, pas avec
        # l'identité du visiteur : le dire explicitement évite un contresens sur
        # la portée des droits.
        "execution": "principal de service de l'application",
    }


@routeur.get("/meta/filtres", summary="Valeurs disponibles pour les filtres")
def options_filtres(depot: RepositoryDep, settings: SettingsDep) -> dict:
    options = depot.options_filtres()
    date_max = options.get("date_max")
    horizon = settings.horizon_defaut_semaines
    defaut_debut = None
    if isinstance(date_max, date):
        candidat = date_max - timedelta(weeks=horizon - 1)
        borne = options.get("date_min")
        defaut_debut = max(candidat, borne) if isinstance(borne, date) else candidat
    return {
        **options,
        "defauts": {
            "date_debut": defaut_debut,
            "date_fin": date_max,
            "seuil_conformite": settings.seuil_conformite_defaut,
            "horizon_semaines": horizon,
        },
    }


@routeur.get("/meta/grilles", summary="Dictionnaire des grilles et de leurs colonnes")
def dictionnaire_grilles() -> dict:
    """Définit les colonnes affichées par le frontend.

    Le React ne code aucune colonne en dur : ajouter une mesure se fait ici.
    """
    return {cle: grille.model_dump() for cle, grille in GRILLES.items()}


@routeur.get("/meta/fraicheur", summary="Fraîcheur des données et contrôles qualité")
def fraicheur(depot: RepositoryDep) -> dict:
    return depot.fraicheur()
