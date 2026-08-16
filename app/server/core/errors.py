"""Erreurs applicatives et leur traduction en réponses HTTP.

Principe : le domaine et la couche d'accès aux données lèvent des exceptions
métier ; seule la couche API sait ce qu'est un code HTTP. Aucun message
d'exception technique (trace, requête SQL, hôte) n'est renvoyé au client.
"""

from __future__ import annotations


class BackflushError(Exception):
    """Erreur applicative de base."""

    #: Code HTTP associé.
    status_code: int = 500
    #: Message affichable par l'interface (français, orienté utilisateur).
    message: str = "Une erreur interne est survenue."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class ConfigurationError(BackflushError):
    """La connexion aux données n'est pas configurée."""

    status_code = 503
    message = (
        "La base Lakebase n'est pas configurée pour cette instance. "
        "Vérifiez la ressource « database » de l'application."
    )


class DonneesIndisponiblesError(BackflushError):
    """La base est configurée mais injoignable ou en erreur."""

    status_code = 503
    message = "Les données sont momentanément indisponibles. Réessayez dans quelques instants."


class RequeteInvalideError(BackflushError):
    """Paramètres de requête refusés (filtre, tri, colonne inconnue)."""

    status_code = 422
    message = "La requête contient des paramètres invalides."


class AssistantIndisponibleError(BackflushError):
    """L'assistant IA n'est pas configuré ou le endpoint est injoignable."""

    status_code = 503
    message = "L'assistant IA est momentanément indisponible."


class VolumeExcessifError(BackflushError):
    """L'opération demandée dépasse les plafonds de volumétrie."""

    status_code = 413
    message = "Le volume demandé dépasse la limite autorisée. Affinez vos filtres."
