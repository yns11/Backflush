"""Routes de l'assistant IA."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from app.server.api.deps import AssistantDep, SettingsDep
from app.server.domain.filters import Filtres
from app.server.services.assistant import MessageChat, ReponseAssistant

routeur = APIRouter(prefix="/api/assistant", tags=["assistant"])

#: Bornes du dialogue : au-delà, la conversation est tronquée côté client.
HISTORIQUE_MAX = 20


class RequeteChat(BaseModel):
    messages: list[MessageChat] = Field(..., min_length=1, max_length=HISTORIQUE_MAX)
    filtres: Filtres = Field(default_factory=Filtres)
    page_active: str = "synthese"


class RequeteAnalyseLot(BaseModel):
    grille: str
    lignes: list[dict[str, Any]] = Field(..., min_length=1, max_length=1_000)
    filtres: Filtres = Field(default_factory=Filtres)
    question: str | None = Field(default=None, max_length=1_000)


@routeur.get("/etat", summary="Disponibilité et configuration de l'assistant")
def etat(settings: SettingsDep) -> dict:
    return {
        "actif": settings.llm_enabled,
        "modele": settings.llm_endpoint if settings.llm_enabled else None,
        "tours_max": settings.llm_max_tool_rounds,
        # L'assistant lit la base avec le principal de service, pas avec
        # l'identité du visiteur : l'annoncer évite tout malentendu sur la
        # portée des droits.
        "execution": "principal de service de l'application",
    }


@routeur.get("/diagnostic", summary="Pourquoi l'assistant ne répond pas")
def diagnostic(assistant: AssistantDep) -> dict:
    """Vérifie la chaîne complète : client, existence du endpoint, appel réel.

    Répond toujours en 200, y compris — surtout — quand tout échoue : un
    diagnostic qui remonte lui-même une erreur 503 n'aurait rien diagnostiqué.
    Le verdict de chaque étape est dans le corps de la réponse.
    """
    return assistant.diagnostic()


@routeur.post("/chat", summary="Dialogue outillé sur les données backflush")
def chat(assistant: AssistantDep, requete: RequeteChat = Body(...)) -> ReponseAssistant:
    return assistant.repondre(requete.messages, requete.filtres, requete.page_active)


@routeur.post("/analyser-lot", summary="Analyse d'une sélection de lignes")
def analyser_lot(assistant: AssistantDep, requete: RequeteAnalyseLot = Body(...)) -> ReponseAssistant:
    return assistant.analyser_lot(
        requete.grille, requete.lignes, requete.filtres, requete.question
    )


@routeur.get("/suggestions", summary="Questions proposées à l'utilisateur")
def suggestions() -> dict:
    """Amorces de conversation orientées décision, pas démonstration technique."""
    return {
        "suggestions": [
            "Quelles sont les 5 références qui pèsent le plus sur l'écart de la période, et pourquoi ?",
            "L'écart s'est-il dégradé par rapport à la période précédente ? Sur quel programme ?",
            "Y a-t-il des composants sortis hors nomenclature ? Sur quels ordres de fabrication ?",
            "Quels composants présentent une surconsommation régulière plutôt qu'un accident isolé ?",
            "Sur le programme M3, quelle part de l'écart est concentrée sur les 10 premières références ?",
            "Quelles références ont un coefficient non uniforme, et quel impact sur l'analyse ?",
        ]
    }
