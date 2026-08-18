"""Tests du chemin d'échec de l'assistant.

Une panne d'assistant est presque toujours une panne de CONFIGURATION : nom de
endpoint faux, principal de service sans droit d'interrogation, paramètre refusé
par le fournisseur. Ces trois causes se corrigent à trois endroits différents,
et aucune ne se corrige en réessayant.

L'application les rendait pourtant indistinguables : quelle que soit l'erreur du
fournisseur, l'écran affichait « Le modèle n'a pas répondu. Réessayez dans
quelques instants. » — un message qui, dans les trois cas, désigne le mauvais
geste. C'est ce silence que ces tests interdisent de revenir :

* la cause du fournisseur doit atteindre l'interface (``detail``), pas seulement
  les journaux ;
* le message doit nommer le geste de correction, différent par code HTTP ;
* le diagnostic doit répondre en 200 même quand tout échoue — un diagnostic qui
  remonte lui-même une erreur n'a rien diagnostiqué.

Aucun de ces tests n'appelle Databricks : le client de serving est remplacé par
un double qui lève ce qu'un fournisseur lèverait.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.server.core.config import Settings
from app.server.core.errors import AssistantIndisponibleError
from app.server.main import app
from app.server.services.assistant import (
    AssistantService,
    _cause_lisible,
    _parametre_refuse,
    _remede_probable,
)


class ErreurFournisseur(Exception):
    """Double d'une exception du client OpenAI : statut, corps, message."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.body = {"message": message}


class ClientQuiEchoue:
    """Client de serving dont chaque appel lève l'erreur fournie."""

    def __init__(self, erreur: Exception) -> None:
        self._erreur = erreur
        self.chat = self

    @property
    def completions(self) -> ClientQuiEchoue:
        return self

    def create(self, **_: Any) -> None:
        raise self._erreur


def _service(erreur: Exception | None = None) -> AssistantService:
    """Service dont le client est déjà posé : aucun appel au SDK Databricks."""
    service = AssistantService(repository=None, settings=Settings())  # type: ignore[arg-type]
    if erreur is not None:
        service._client = ClientQuiEchoue(erreur)
    return service


class TestLectureDeLaCause:
    """Traduire une exception de fournisseur en phrase actionnable."""

    def test_le_statut_et_le_message_sont_conserves(self) -> None:
        cause = _cause_lisible(ErreurFournisseur(404, "Endpoint not found"))
        assert "404" in cause
        assert "Endpoint not found" in cause

    def test_une_exception_nue_reste_lisible(self) -> None:
        assert "connexion refusée" in _cause_lisible(RuntimeError("connexion refusée"))

    def test_la_cause_est_bornee(self) -> None:
        """Un pavé de mille caractères dans une bulle n'informe pas mieux qu'un silence."""
        cause = _cause_lisible(ErreurFournisseur(400, "x" * 5_000))
        assert len(cause) < 450

    def test_une_erreur_applicative_rend_sa_cause_et_non_son_enveloppe(self) -> None:
        """Relire notre propre erreur donnerait « HTTP 503 — <notre message> ».

        C'est le pire des deux mondes : notre message, enveloppé dans notre code
        de statut, et la cause d'origine perdue. Le diagnostic afficherait alors
        exactement le texte que l'utilisateur vient de lire à l'écran.
        """
        enveloppe = AssistantIndisponibleError(
            "Impossible de joindre le endpoint.", detail="default auth: cannot configure"
        )
        assert _cause_lisible(enveloppe) == "default auth: cannot configure"
        assert "503" not in _cause_lisible(enveloppe)

    def test_une_erreur_applicative_sans_detail_rend_son_message(self) -> None:
        assert _cause_lisible(AssistantIndisponibleError("Assistant désactivé.")) == (
            "Assistant désactivé."
        )

    def test_les_retours_a_la_ligne_sont_aplatis(self) -> None:
        cause = _cause_lisible(ErreurFournisseur(400, "ligne 1\n\n   ligne 2"))
        assert "\n" not in cause
        assert "ligne 1 ligne 2" in cause

    @pytest.mark.parametrize(
        ("statut", "attendu"),
        [
            (404, "LLM_ENDPOINT"),
            (403, "Can Query"),
            (401, "Can Query"),
            (400, "max_tokens"),
            (429, "Quota"),
            (503, "fournisseur"),
        ],
    )
    def test_chaque_code_designe_son_geste(self, statut: int, attendu: str) -> None:
        """Trois causes, trois endroits où corriger : le message doit trancher."""
        assert attendu in _remede_probable(ErreurFournisseur(statut, "peu importe"))

    def test_seul_le_5xx_invite_a_reessayer(self) -> None:
        """« Réessayez » sur un 404 envoie l'utilisateur dans le mur."""
        assert "réessayer" in _remede_probable(ErreurFournisseur(503, "")).lower()
        for statut in (400, 401, 403, 404):
            remede = _remede_probable(ErreurFournisseur(statut, "")).lower()
            assert "réessay" not in remede, statut


class TestRemonteeDeLaCause:
    def test_l_echec_d_appel_porte_le_detail_et_le_remede(self) -> None:
        service = _service(ErreurFournisseur(404, "Endpoint not found"))
        with pytest.raises(AssistantIndisponibleError) as capture:
            service._appeler_modele(service._client, [{"role": "user", "content": "x"}])
        erreur = capture.value
        assert "404" in (erreur.detail or "")
        assert "LLM_ENDPOINT" in erreur.message
        # Le nom du endpoint est dans le message : sans lui, l'utilisateur ne
        # sait pas lequel corriger quand il en a plusieurs.
        assert Settings().llm_endpoint in erreur.message

    def test_le_detail_traverse_la_couche_http(self) -> None:
        """Le champ n'a d'intérêt que s'il arrive jusqu'à l'écran."""
        from app.server.api.deps import get_assistant

        app.dependency_overrides[get_assistant] = lambda: _service(
            ErreurFournisseur(403, "PERMISSION_DENIED")
        )
        try:
            with TestClient(app) as client:
                reponse = client.post(
                    "/api/assistant/chat", json={"messages": [{"role": "user", "contenu": "x"}]}
                )
            assert reponse.status_code == 503
            corps = reponse.json()
            assert "403" in corps["detail"]
            assert "Can Query" in corps["erreur"]
        finally:
            app.dependency_overrides.pop(get_assistant, None)

    def test_une_erreur_de_donnees_ne_porte_aucun_detail(self) -> None:
        """La transparence est ciblée : la cause d'une panne SQL ne se diffuse pas."""
        from app.server.core.errors import DonneesIndisponiblesError

        assert DonneesIndisponiblesError().detail is None


class TestParametreRefuse:
    """Un endpoint qui refuse un réglage ne doit pas mettre l'assistant à terre.

    Cas réel rencontré en production : `eu.anthropic.claude-opus-4-8` répond
    ``400 — Model … does not support the temperature parameter``. Le paramètre
    est parfaitement légitime ailleurs, et le client OpenAI l'envoie sans
    broncher. Le service doit apprendre le refus, pas le subir.
    """

    MESSAGE_REEL = (
        "BAD_REQUEST: Model eu.anthropic.claude-opus-4-8 does not support "
        "the temperature parameter."
    )

    def test_le_parametre_est_reconnu_dans_le_message_reel(self) -> None:
        assert _parametre_refuse(ErreurFournisseur(400, self.MESSAGE_REEL)) == "temperature"

    def test_un_400_ordinaire_ne_retire_aucun_parametre(self) -> None:
        """Retirer un réglage sur un 400 quelconque dégraderait tous les appels."""
        for message in (
            "BAD_REQUEST: input is too long",
            "invalid request: messages must not be empty",
            "temperature must be between 0 and 1",
        ):
            assert _parametre_refuse(ErreurFournisseur(400, message)) is None, message

    def test_seul_un_400_est_examine(self) -> None:
        assert _parametre_refuse(ErreurFournisseur(404, self.MESSAGE_REEL)) is None
        assert _parametre_refuse(ErreurFournisseur(500, self.MESSAGE_REEL)) is None

    @staticmethod
    def _client_pointilleux(interdits: set[str]) -> tuple[Any, list[dict]]:
        """Client qui refuse les paramètres nommés, un message d'erreur à la fois."""
        appels: list[dict] = []

        class Client:
            def __init__(self) -> None:
                self.chat = self

            @property
            def completions(self) -> Any:
                return self

            def create(self, **kwargs: Any) -> Any:
                appels.append(dict(kwargs))
                for nom in sorted(interdits):
                    if nom in kwargs:
                        raise ErreurFournisseur(
                            400, f"Model X does not support the {nom} parameter."
                        )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
                )

        return Client(), appels

    def test_le_service_retire_le_parametre_et_rejoue(self) -> None:
        client, appels = self._client_pointilleux({"temperature"})
        service = AssistantService(repository=None, settings=Settings())  # type: ignore[arg-type]
        service._client = client

        reponse = service._appeler_modele(client, [{"role": "user", "content": "x"}])
        assert reponse.choices[0].message.content == "ok"
        assert len(appels) == 2, "Un seul rejeu attendu."
        assert "temperature" in appels[0]
        assert "temperature" not in appels[1]

    def test_la_lecon_est_retenue_pour_les_appels_suivants(self) -> None:
        """Le service est un singleton : le coût doit être d'UN aller-retour."""
        client, appels = self._client_pointilleux({"temperature"})
        service = AssistantService(repository=None, settings=Settings())  # type: ignore[arg-type]
        service._client = client

        service._appeler_modele(client, [{"role": "user", "content": "x"}])
        service._appeler_modele(client, [{"role": "user", "content": "y"}])
        assert len(appels) == 3, "Le deuxième appel ne doit plus tenter le paramètre."
        assert "temperature" not in appels[2]

    def test_plusieurs_refus_convergent_sans_boucler(self) -> None:
        """La récursion est bornée : chaque tour retire un nom d'un ensemble fini."""
        client, appels = self._client_pointilleux({"temperature", "max_tokens"})
        service = AssistantService(repository=None, settings=Settings())  # type: ignore[arg-type]
        service._client = client

        reponse = service._appeler_modele(client, [{"role": "user", "content": "x"}])
        assert reponse.choices[0].message.content == "ok"
        assert len(appels) == 3
        assert service._parametres_refuses == {"temperature", "max_tokens"}

    def test_un_refus_persistant_finit_par_remonter(self) -> None:
        """Sans paramètre à retirer, l'erreur doit sortir — jamais boucler."""
        service = _service(ErreurFournisseur(400, "Model X does not support the frobnicator."))
        with pytest.raises(AssistantIndisponibleError):
            service._appeler_modele(service._client, [{"role": "user", "content": "x"}])

    def test_une_temperature_absente_de_la_configuration_n_est_pas_transmise(self) -> None:
        recu: dict[str, Any] = {}

        class Client:
            def __init__(self) -> None:
                self.chat = self

            @property
            def completions(self) -> Any:
                return self

            def create(self, **kwargs: Any) -> Any:
                recu.update(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
                )

        service = AssistantService(
            repository=None,  # type: ignore[arg-type]
            settings=Settings(llm_temperature=None),
        )
        service._client = Client()
        service._appeler_modele(service._client, [{"role": "user", "content": "x"}])
        assert "temperature" not in recu


class TestDiagnostic:
    @staticmethod
    def _etape(resultat: dict, nom: str) -> dict:
        return next(etape for etape in resultat["etapes"] if etape["etape"] == nom)

    def test_l_assistant_desactive_le_dit_sans_appeler_personne(self) -> None:
        service = AssistantService(
            repository=None,  # type: ignore[arg-type]
            settings=Settings(llm_enabled=False),
        )
        resultat = service.diagnostic()
        assert resultat["ok"] is False
        assert "désactivé" in self._etape(resultat, "configuration")["message"]

    def test_l_appel_en_echec_est_rapporte_sans_lever(self) -> None:
        """Le diagnostic ne doit JAMAIS échouer : c'est lui le filet."""
        service = _service(ErreurFournisseur(404, "Endpoint not found"))
        resultat = service.diagnostic()
        assert resultat["ok"] is False
        appel = self._etape(resultat, "appel")
        assert appel["ok"] is False
        assert "404" in appel["message"]
        assert "LLM_ENDPOINT" in appel["remede"]

    def test_la_route_repond_200_meme_quand_tout_echoue(self) -> None:
        from app.server.api.deps import get_assistant

        app.dependency_overrides[get_assistant] = lambda: _service(
            ErreurFournisseur(404, "Endpoint not found")
        )
        try:
            with TestClient(app) as client:
                reponse = client.get("/api/assistant/diagnostic")
            assert reponse.status_code == 200, (
                "Un diagnostic qui remonte une erreur HTTP n'a rien diagnostiqué."
            )
            assert reponse.json()["ok"] is False
        finally:
            app.dependency_overrides.pop(get_assistant, None)

    def test_un_appel_qui_passe_est_rapporte_comme_tel(self) -> None:
        class ClientQuiRepond:
            def __init__(self) -> None:
                self.chat = self

            @property
            def completions(self) -> ClientQuiRepond:
                return self

            def create(self, **_: Any) -> Any:
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))]
                )

        service = AssistantService(repository=None, settings=Settings())  # type: ignore[arg-type]
        service._client = ClientQuiRepond()
        resultat = service.diagnostic()
        appel = self._etape(resultat, "appel")
        assert appel["ok"] is True
        assert "pong" in appel["message"]

    def test_l_appel_de_sonde_n_emporte_aucun_outil(self) -> None:
        """La sonde teste le LIEN, pas le raisonnement : sans outils, 16 jetons.

        Un ping outillé échouerait sur un endpoint qui ne gère pas les appels de
        fonctions, et accuserait la connexion d'un défaut de capacité.
        """
        recu: dict[str, Any] = {}

        class ClientEspion:
            def __init__(self) -> None:
                self.chat = self

            @property
            def completions(self) -> ClientEspion:
                return self

            def create(self, **kwargs: Any) -> Any:
                recu.update(kwargs)
                raise ErreurFournisseur(500, "peu importe")

        service = AssistantService(repository=None, settings=Settings())  # type: ignore[arg-type]
        service._client = ClientEspion()
        service.diagnostic()
        assert "tools" not in recu
        assert recu["max_tokens"] <= 32
