"""Point d'entrée FastAPI de l'application Databricks « backflush-analytics ».

Assemblage uniquement : ouverture du pool, gestion d'erreurs, montage des
routeurs et service du bundle React. Aucune logique métier ici.

Démarrage local ::

    uvicorn app.server.main:app --reload --port 8000

Démarrage sur Databricks Apps : voir ``app/app.yaml``.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.server.api import (
    routes_analytics,
    routes_assistant,
    routes_export,
    routes_grid,
    routes_meta,
    routes_parametrage,
)
from app.server.core.config import get_settings
from app.server.core.errors import BackflushError
from app.server.core.lakebase import LakebasePool
from app.server.data.parametrage import DepotParametrage
from app.server.data.repository import Repository
from app.server.services.assistant import AssistantService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
LOGGER = logging.getLogger("backflush.api")


@asynccontextmanager
async def cycle_de_vie(application: FastAPI):
    """Ouvre les ressources au démarrage, les referme proprement à l'arrêt."""
    settings = get_settings()
    pool = LakebasePool(settings)
    pool.open()

    application.state.settings = settings
    application.state.pool = pool
    application.state.repository = Repository(pool)
    application.state.parametrage = DepotParametrage(pool)
    application.state.assistant = AssistantService(application.state.repository, settings)

    LOGGER.info("Démarrage : %s", settings.resume())
    try:
        yield
    finally:
        pool.close()


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Reporting et analytique des écarts de consommation composant issus du "
        "backflush de production (Dynamics 365 F&O → Lakehouse → Lakebase)."
    ),
    lifespan=cycle_de_vie,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Les réponses analytiques sont du JSON très répétitif : la compression divise
# typiquement leur taille par cinq sur une grille de 500 lignes.
app.add_middleware(GZipMiddleware, minimum_size=1_000)

# En production, le frontend est servi par cette même application : aucune
# origine tierce n'est nécessaire. Les origines déclarées ne servent qu'au
# serveur de développement Vite.
if settings.environnement != "prod" and settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def journaliser_duree(request: Request, call_next):
    """Journalise la durée des appels d'API et l'expose en en-tête.

    Rend immédiatement visible, côté navigateur comme côté journaux, une
    requête analytique qui dérive.
    """
    debut = time.perf_counter()
    reponse = await call_next(request)
    duree_ms = (time.perf_counter() - debut) * 1000
    if request.url.path.startswith("/api/"):
        reponse.headers["X-Duree-Ms"] = f"{duree_ms:.0f}"
        if duree_ms > 2_000:
            LOGGER.warning("Requête lente : %s %s — %.0f ms",
                           request.method, request.url.path, duree_ms)
    return reponse


@app.exception_handler(BackflushError)
async def gerer_erreur_metier(_: Request, exc: BackflushError) -> JSONResponse:
    """Traduit une erreur applicative en réponse exploitable par l'interface.

    Le message est rédigé pour un utilisateur métier. Le champ ``detail``, lui,
    n'est présent que si l'appelant l'a explicitement renseigné : les erreurs de
    données n'en portent pas — leur cause technique ne dirait rien et exposerait
    la structure — les erreurs de configuration si, parce que c'est
    l'utilisateur qui peut les corriger.
    """
    LOGGER.info(
        "Erreur applicative %s : %s%s",
        exc.status_code,
        exc.message,
        f" — {exc.detail}" if exc.detail else "",
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "erreur": exc.message,
            "type": type(exc).__name__,
            **({"detail": exc.detail} if exc.detail else {}),
        },
    )


for routeur in (
    routes_meta.routeur,
    routes_analytics.routeur,
    routes_grid.routeur,
    routes_export.routeur,
    routes_assistant.routeur,
    routes_parametrage.routeur,
):
    app.include_router(routeur)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
_STATIC: Path = settings.static_dir
_INDEX: Path = _STATIC / "index.html"

if _STATIC.is_dir():
    # Les fichiers de /assets portent une empreinte dans leur nom : ils sont
    # immuables et peuvent être mis en cache agressivement.
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.get("/{chemin:path}", include_in_schema=False)
    async def servir_spa(chemin: str):
        """Sert le bundle React ; toute route inconnue retombe sur index.html.

        Indispensable pour une application à navigation côté client : un
        rafraîchissement sur /references doit renvoyer l'application, pas un 404.
        """
        fichier = _STATIC / chemin
        if chemin and fichier.is_file():
            return FileResponse(fichier)
        return FileResponse(_INDEX)

else:  # pragma: no cover - chemin de développement
    LOGGER.warning(
        "Bundle frontend absent de %s. L'API reste disponible sur /api. "
        "Construisez-le avec scripts/build_frontend.sh.", _STATIC,
    )

    @app.get("/", include_in_schema=False)
    async def accueil_sans_bundle() -> JSONResponse:
        return JSONResponse({
            "application": settings.app_name,
            "message": (
                "Interface non compilée. Exécutez scripts/build_frontend.sh, ou "
                "utilisez le serveur de développement Vite (npm run dev)."
            ),
            "api": "/api/docs",
        })
