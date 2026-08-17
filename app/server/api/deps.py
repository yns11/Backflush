"""Dépendances FastAPI partagées.

Le pool, le repository et l'assistant sont des singletons portés par
``app.state`` : ils sont créés au démarrage et réutilisés à chaque requête. Les
créer par requête rouvrirait un pool de connexions à chaque appel HTTP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.server.core.config import Settings, get_settings
from app.server.core.lakebase import LakebasePool
from app.server.data.parametrage import DepotParametrage
from app.server.data.repository import Repository
from app.server.services.assistant import AssistantService


def get_pool(request: Request) -> LakebasePool:
    return request.app.state.pool


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


def get_parametrage(request: Request) -> DepotParametrage:
    return request.app.state.parametrage


def get_assistant(request: Request) -> AssistantService:
    return request.app.state.assistant


def get_utilisateur(request: Request) -> dict[str, str | None]:
    """Identité du visiteur, telle que transmise par le proxy Databricks Apps.

    Ces en-têtes ne sont présents qu'une fois l'application déployée. En local,
    ils sont absents : l'interface affiche « développement local » plutôt que de
    prétendre connaître un utilisateur.
    """
    entetes = request.headers
    return {
        "email": entetes.get("x-forwarded-email"),
        "utilisateur": entetes.get("x-forwarded-preferred-username") or entetes.get("x-forwarded-user"),
        "identifiant": entetes.get("x-forwarded-user-id"),
    }


SettingsDep = Annotated[Settings, Depends(get_settings)]
PoolDep = Annotated[LakebasePool, Depends(get_pool)]
RepositoryDep = Annotated[Repository, Depends(get_repository)]
ParametrageDep = Annotated[DepotParametrage, Depends(get_parametrage)]
AssistantDep = Annotated[AssistantService, Depends(get_assistant)]
UtilisateurDep = Annotated[dict, Depends(get_utilisateur)]
