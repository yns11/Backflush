"""Configuration de l'application, lue depuis l'environnement.

Databricks Apps injecte automatiquement les variables ``PG*`` et
``LAKEBASE_ENDPOINT`` dès qu'une ressource de type *database* est attachée à
l'application. En développement local, ``LAKEBASE_PG_URL`` court-circuite tout
le mécanisme OAuth.

Aucun secret n'est écrit en dur ni journalisé : :meth:`Settings.resume` ne
produit qu'une empreinte non sensible de la configuration.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Pool de connexions --------------------------------------------------
    pool_min_size: int = 1
    pool_max_size: int = 8
    pool_timeout_s: float = 10.0
    #: Le jeton OAuth Lakebase vit 1 h ; on le renouvelle largement avant.
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
        """Vrai si l'un des deux modes de connexion est exploitable."""
        return bool(self.lakebase_pg_url) or bool(self.pghost and self.lakebase_endpoint)

    def resume(self) -> dict[str, object]:
        """Empreinte de configuration exposable (aucun secret)."""
        return {
            "application": self.app_name,
            "version": self.app_version,
            "environnement": self.environnement,
            "mode_connexion": (
                "url_directe" if self.lakebase_pg_url
                else "oauth_lakebase" if self.pghost
                else "non_configure"
            ),
            "schema": self.pg_schema,
            "assistant_actif": self.llm_enabled,
            "endpoint_llm": self.llm_endpoint if self.llm_enabled else None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique, mise en cache pour la durée du processus."""
    return Settings()
