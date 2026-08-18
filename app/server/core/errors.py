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

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        #: Cause technique, transmise à l'interface **quand elle est
        #: actionnable et sans secret**.
        #:
        #: Le principe général du module — ne rien exposer de technique — vise
        #: les erreurs de base de données, dont la cause ne dit rien à
        #: l'utilisateur et peut révéler la structure. Les erreurs de
        #: configuration du serving n'ont pas ce profil : « endpoint introuvable »
        #: ou « paramètre refusé » sont exactement ce qu'un key-user doit lire
        #: pour agir, et les taire transforme une panne d'une minute en ticket.
        #: Le champ reste donc vide par défaut, et n'est renseigné que là où
        #: l'appelant a jugé la cause diffusable.
        self.detail = detail


class ConfigurationError(BackflushError):
    """La connexion aux données n'est pas configurée."""

    status_code = 503
    message = (
        "La base Lakebase n'est pas configurée pour cette instance. "
        "Vérifiez la ressource « postgres » de l'application, puis "
        "consultez /api/health : il indique quelles variables manquent."
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
    """L'assistant IA n'est pas configuré ou le endpoint est injoignable.

    Porte volontairement un ``detail`` : la cause d'une panne d'assistant est
    presque toujours une question de configuration — nom de endpoint, droits du
    principal de service, paramètre refusé par le fournisseur — et c'est
    l'utilisateur qui a la main dessus.
    """

    status_code = 503
    message = "L'assistant IA est momentanément indisponible."


class VolumeExcessifError(BackflushError):
    """L'opération demandée dépasse les plafonds de volumétrie."""

    status_code = 413
    message = "Le volume demandé dépasse la limite autorisée. Affinez vos filtres."
