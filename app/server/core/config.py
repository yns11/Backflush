"""Configuration de l'application, lue depuis l'environnement.

Databricks Apps injecte automatiquement les variables ``PG*`` et
``LAKEBASE_ENDPOINT`` dès qu'une ressource de type *database* est attachée à
l'application. En développement local, ``LAKEBASE_PG_URL`` court-circuite tout
le mécanisme OAuth.

Aucun secret n'est écrit en dur ni journalisé : :meth:`Settings.resume` ne
produit qu'une empreinte non sensible de la configuration.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Variables que la plateforme injecte lorsqu'une ressource « postgres » est
#: attachée à l'application, plus l'échappatoire de développement local.
#:
#: ⚠️ L'injection a lieu à la CRÉATION DU DÉPLOIEMENT, pas au démarrage du
#: conteneur : attacher la ressource sans redéployer laisse l'application sans
#: aucune de ces variables. C'est le piège que ce diagnostic sert à révéler.
VARIABLES_LAKEBASE: tuple[str, ...] = (
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGSSLMODE",
    "LAKEBASE_ENDPOINT",
    "PGPASSWORD",
    "LAKEBASE_PG_URL",
)


def diagnostic_environnement() -> dict[str, list[str]]:
    """Liste les variables Lakebase présentes et absentes — **noms seuls**.

    Aucune valeur n'est retournée : ``PGPASSWORD`` et ``LAKEBASE_PG_URL`` sont
    des secrets, et ce diagnostic finit dans les journaux et dans
    ``/api/health``. Savoir *quelles* variables manquent suffit à trancher entre
    « ressource non attachée » et « déploiement antérieur à l'attachement ».
    """
    presentes = [nom for nom in VARIABLES_LAKEBASE if os.environ.get(nom)]
    return {
        "presentes": presentes,
        "absentes": [nom for nom in VARIABLES_LAKEBASE if nom not in presentes],
    }


class Settings(BaseSettings):
    """Paramètres d'exécution. Tous surchargables par variable d'environnement."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- Identité de l'application ------------------------------------------
    app_name: str = "Backflush Analytics"
    app_version: str = "1.0.0"
    environnement: str = Field(default="dev", description="dev | preprod | prod")

    # --- Lakebase / Postgres -------------------------------------------------
    lakebase_pg_url: str | None = Field(
        default=None,
        description="URL Postgres complète. Prioritaire ; réservée au développement local.",
    )
    pghost: str | None = None
    pgport: int = 5432
    pgdatabase: str = "databricks_postgres"
    pguser: str | None = None
    pg_schema: str = "backflush"
    lakebase_endpoint: str | None = Field(
        default=None, description="Chemin de ressource projects/<id>/branches/<b>/endpoints/<e>"
    )
    pgpassword: str | None = Field(
        default=None,
        description=(
            "Mot de passe injecté par la ressource « postgres » de l'application. "
            "Utilisé uniquement en l'absence de LAKEBASE_ENDPOINT : la génération "
            "de jeton à la demande est préférée, car elle garantit la rotation."
        ),
    )

    # --- Pool de connexions --------------------------------------------------
    pool_min_size: int = 1
    pool_max_size: int = 8
    pool_timeout_s: float = 10.0
    #: Durée au bout de laquelle le jeton en cache est régénéré, à l'occasion
    #: d'une nouvelle connexion physique. Le jeton OAuth Lakebase vit une heure :
    #: la moitié laisse une marge confortable sans multiplier les appels.
    token_refresh_s: int = 1_800
    #: Garde-fou : toute requête dépassant ce délai est annulée côté serveur.
    statement_timeout_ms: int = 25_000

    # --- Règles métier par défaut -------------------------------------------
    seuil_conformite_defaut: float = Field(
        default=0.5,
        description="Tolérance en unités en deçà de laquelle une ligne est conforme.",
    )
    horizon_defaut_semaines: int = 13
    #: Plafond de lignes renvoyées par une page de grille.
    page_size_max: int = 500
    #: Plafond de lignes exportées en une fois (protège la mémoire de l'App).
    export_rows_max: int = 100_000

    # --- Assistant IA --------------------------------------------------------
    llm_endpoint: str = Field(
        default="databricks-claude-sonnet-4-5",
        description="Nom du endpoint de serving utilisé par l'assistant.",
    )
    llm_max_tokens: int = 2_000
    llm_temperature: float = 0.0
    llm_max_tool_rounds: int = 5
    llm_enabled: bool = True

    # --- Frontend ------------------------------------------------------------
    static_dir: Path = Field(
        default=Path(__file__).resolve().parents[1] / "static",
        description="Bundle React compilé, servi par FastAPI.",
    )
    cors_origins: str = Field(
        default="http://localhost:5173",
        description="Origines autorisées en développement (séparées par des virgules).",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def base_de_donnees_configuree(self) -> bool:
        """Vrai si l'un des trois modes de connexion est exploitable.

        Voir :attr:`mode_connexion` pour leur ordre de priorité.
        """
        return bool(self.lakebase_pg_url) or bool(
            self.pghost and (self.lakebase_endpoint or self.pgpassword)
        )

    @property
    def mode_connexion(self) -> str:
        """Mode retenu, par priorité décroissante.

        * ``url_directe`` — ``LAKEBASE_PG_URL`` : développement local.
        * ``oauth_lakebase`` — hôte + endpoint : l'application génère elle-même
          un jeton et le renouvelle. **Mode recommandé en production**, car la
          rotation est garantie par l'application.
        * ``mot_de_passe_injecte`` — hôte + ``PGPASSWORD`` sans endpoint : la
          ressource « postgres » a fourni un identifiant, mais sa rotation
          dépend de la plateforme. Fonctionne, avec cette réserve.
        """
        if self.lakebase_pg_url:
            return "url_directe"
        if self.pghost and self.lakebase_endpoint:
            return "oauth_lakebase"
        if self.pghost and self.pgpassword:
            return "mot_de_passe_injecte"
        return "non_configure"

    def resume(self) -> dict[str, object]:
        """Empreinte de configuration exposable (aucun secret)."""
        return {
            "application": self.app_name,
            "version": self.app_version,
            "environnement": self.environnement,
            "mode_connexion": self.mode_connexion,
            "schema": self.pg_schema,
            "assistant_actif": self.llm_enabled,
            "endpoint_llm": self.llm_endpoint if self.llm_enabled else None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique, mise en cache pour la durée du processus."""
    return Settings()
