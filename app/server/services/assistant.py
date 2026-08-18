"""Assistant IA « écarts backflush ».

Choix structurant : **outils métier plutôt que texte-vers-SQL libre.**

Un assistant qui génère du SQL arbitraire sur une base de production cumule
trois risques : requêtes coûteuses non bornées, jointures fausses présentées
avec assurance, et surface d'injection. Ici, le modèle ne peut appeler qu'un
catalogue fermé de fonctions — les mêmes que celles qui alimentent les écrans —
chacune paramétrée, bornée en volume et déjà validée. Le modèle choisit *quoi*
demander ; il ne choisit jamais *comment* la base est interrogée.

Trois garanties visibles par l'utilisateur :

1. **Traçabilité** — chaque réponse expose la liste des outils appelés et leurs
   paramètres ; l'utilisateur voit d'où viennent les chiffres.
2. **Ancrage** — les outils héritent par défaut des filtres de l'écran courant :
   l'assistant répond sur ce que l'utilisateur regarde, pas sur tout l'historique.
3. **Réserve** — le prompt système impose d'annoncer explicitement les limites du
   modèle de données (pas de rebut, maille hebdomadaire, coûts manquants).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.server.core.config import Settings
from app.server.core.errors import AssistantIndisponibleError, BackflushError
from app.server.data.repository import Repository
from app.server.domain.dictionary import GRILLES, contexte_metier
from app.server.domain.filters import Filtres
from app.server.domain.metrics import construire_indicateurs

LOGGER = logging.getLogger("backflush.assistant")

#: Plafonds appliqués aux résultats d'outils — protègent la fenêtre de contexte
#: du modèle autant que la base.
LIMITE_LIGNES_OUTIL = 50
LIMITE_LIGNES_LOT = 300

AVERTISSEMENT = (
    "Réponse générée par IA à partir des données Backflush. "
    "Vérifiez les chiffres avant toute décision d'exploitation."
)


class MessageChat(BaseModel):
    role: Literal["user", "assistant"]
    contenu: str


class AppelOutil(BaseModel):
    """Trace d'un appel d'outil, renvoyée à l'interface pour inspection."""

    outil: str
    arguments: dict[str, Any]
    nb_lignes: int | None = None
    erreur: str | None = None


class ReponseAssistant(BaseModel):
    reponse: str
    appels: list[AppelOutil] = Field(default_factory=list)
    avertissement: str = AVERTISSEMENT
    modele: str


def _json_sur(valeur: Any) -> Any:
    """Rend une valeur SQL sérialisable en JSON pour le modèle."""
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, (date, datetime)):
        return valeur.isoformat()
    return valeur


def _serialiser(lignes: Iterable[dict[str, Any]], limite: int) -> list[dict[str, Any]]:
    resultat = []
    for index, ligne in enumerate(lignes):
        if index >= limite:
            break
        resultat.append({cle: _json_sur(valeur) for cle, valeur in ligne.items()})
    return resultat


# ---------------------------------------------------------------------------
# Catalogue d'outils
# ---------------------------------------------------------------------------
_SCHEMA_FILTRES = {
    "type": "object",
    "description": (
        "Surcharge partielle des filtres de l'écran courant. Omettre un champ "
        "conserve la valeur affichée par l'utilisateur."
    ),
    "properties": {
        "date_debut": {"type": "string", "description": "Lundi de début, AAAA-MM-JJ."},
        "date_fin": {"type": "string", "description": "Lundi de fin, AAAA-MM-JJ."},
        "programmes": {"type": "array", "items": {"type": "string"}},
        "categories": {"type": "array", "items": {"type": "string"}},
        "composants": {"type": "array", "items": {"type": "string"}},
        "parents": {"type": "array", "items": {"type": "string"}},
        "types_ecart": {
            "type": "array",
            "items": {"enum": ["Non-consommation", "Surconsommation", "Conforme"]},
        },
        "statuts_ligne": {
            "type": "array",
            "items": {"enum": ["Nominal", "Hors nomenclature", "Sans consommation"]},
        },
        "exclure_conforme": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def definitions_outils() -> list[dict[str, Any]]:
    """Schémas OpenAI des outils exposés au modèle."""
    def outil(nom: str, description: str, proprietes: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": nom,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {"filtres": _SCHEMA_FILTRES, **proprietes},
                    "additionalProperties": False,
                },
            },
        }

    return [
        outil(
            "indicateurs",
            "Indicateurs de synthèse de la sélection : écart net valorisé, non-consommation, "
            "surconsommation, fiabilité du backflush, taux de conformité, volumes. "
            "À utiliser en premier pour toute question de niveau global.",
            {},
        ),
        outil(
            "serie_hebdomadaire",
            "Évolution semaine par semaine (théorique, réel, écart, impact €). "
            "À utiliser pour toute question de tendance, de rupture ou de saisonnalité.",
            {},
        ),
        outil(
            "repartition",
            "Agrégat par dimension : programme, categorie, type ou statut. "
            "À utiliser pour situer où se concentre le problème.",
            {"dimension": {"enum": ["programme", "categorie", "type", "statut"]}},
        ),
        outil(
            "top_composants",
            "Composants classés par impact financier absolu, avec leur programme.",
            {"limite": {"type": "integer", "minimum": 1, "maximum": 50}},
        ),
        outil(
            "detail_lignes",
            "Lignes de détail parent × composant × semaine, triées par impact absolu. "
            "À utiliser pour instruire un cas précis, jamais pour compter.",
            {"limite": {"type": "integer", "minimum": 1, "maximum": 50}},
        ),
        outil(
            "fiche_article",
            "Fiche du référentiel : désignation, catégorie, programme, coût standard, unité.",
            {"item_id": {"type": "string"}},
        ),
        outil(
            "nomenclature_parent",
            "Composants d'un article parent avec leur coefficient de nomenclature.",
            {"parent_itemid": {"type": "string"}},
        ),
        outil(
            "parents_composant",
            "Articles parents utilisant un composant, avec leur coefficient. "
            "Indispensable pour juger si un coefficient est uniforme.",
            {"child_itemid": {"type": "string"}},
        ),
        outil(
            "qualite_donnees",
            "Contrôles qualité et fraîcheur de la dernière ingestion. "
            "À consulter avant d'affirmer qu'un écart est réel.",
            {},
        ),
    ]


PROMPT_SYSTEME = """\
Tu es l'assistant analytique d'une application de suivi des écarts de consommation
composant issus du backflush de production (ERP Dynamics 365 F&O, secteur moteurs
électriques). Tes interlocuteurs sont des key-users : responsables production,
gestionnaires de stock, contrôleurs de gestion industriels.

MÉTHODE — non négociable
1. Ne réponds JAMAIS de mémoire sur des chiffres : appelle les outils. Sans appel
   d'outil, tu n'as aucune donnée.
2. Commence par le niveau le plus agrégé qui répond à la question, puis descends.
3. Cite systématiquement les valeurs chiffrées avec leur unité (€, unités, %) et
   la période concernée.
4. Si les outils ne permettent pas de répondre, dis-le explicitement et indique
   quel écran ou quelle donnée manquante permettrait de conclure. N'invente rien.
5. Distingue toujours une CONSTATATION (ce que disent les données) d'une
   HYPOTHÈSE de cause (ce que tu supposes).

FORME
- Français professionnel, dense, sans emphase inutile.
- Réponse courte pour une question factuelle ; structurée en sections pour une
  analyse.
- Termine toute analyse par 2 à 4 actions concrètes et vérifiables, priorisées
  par impact financier.

CAUSES USUELLES À ENVISAGER (à confronter aux données, jamais à affirmer seules)
- Non-consommation : backflush non exécuté, OF non clôturé, déclaration de
  production en avance sur la sortie composant, composant remplacé sans mise à
  jour de nomenclature.
- Surconsommation : rebut atelier non déclaré, coefficient de nomenclature
  sous-évalué, servitude ou perte matière non modélisée, erreur d'unité
  (KG vs PCE), prélèvement pour retouche ou maintenance.
- Hors nomenclature : erreur de saisie d'ordre de fabrication, nomenclature
  obsolète, substitution non tracée.

{contexte}
"""


class AssistantService:
    """Orchestration du dialogue outillé avec le modèle de fondation."""

    def __init__(self, repository: Repository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings
        self._client: Any | None = None
        #: Paramètres que CE endpoint a explicitement refusés.
        #:
        #: Les endpoints de fondation n'acceptent pas tous les mêmes réglages :
        #: `eu.anthropic.claude-opus-4-8`, par exemple, rejette `temperature`
        #: — un paramètre parfaitement légitime ailleurs, et que le client
        #: OpenAI envoie sans broncher. Le refus est un `400` en bonne et due
        #: forme, qui nomme le paramètre en cause.
        #:
        #: Plutôt que de figer une liste par modèle — qui serait fausse à la
        #: première montée de version — le service APPREND du refus : il retire
        #: le paramètre, rejoue l'appel, et retient la leçon. Le service étant
        #: un singleton applicatif, le coût est d'un aller-retour par
        #: redémarrage de worker, pas par question posée.
        self._parametres_refuses: set[str] = set()

    # -- Client LLM --------------------------------------------------------
    def _openai(self) -> Any:
        """Client OpenAI pointé sur le endpoint de serving Databricks."""
        if self._client is not None:
            return self._client
        if not self._settings.llm_enabled:
            raise AssistantIndisponibleError("L'assistant est désactivé sur cette instance.")
        try:
            from databricks.sdk import WorkspaceClient

            self._client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        except Exception as exc:
            LOGGER.error("Client de serving indisponible : %s", exc, exc_info=True)
            raise AssistantIndisponibleError(
                "Impossible de joindre le endpoint de serving. Vérifiez la ressource "
                "« serving endpoint » de l'application et les droits du principal de service.",
                detail=_cause_lisible(exc),
            ) from exc
        return self._client

    # -- Exécution des outils ---------------------------------------------
    def _outils(self, filtres_base: Filtres) -> dict[str, Callable[[dict[str, Any]], Any]]:
        depot = self._repository

        def filtres_de(arguments: dict[str, Any]) -> Filtres:
            """Fusionne la surcharge proposée par le modèle avec les filtres de l'écran."""
            surcharge = arguments.get("filtres") or {}
            if not isinstance(surcharge, dict):
                return filtres_base
            propres = {cle: valeur for cle, valeur in surcharge.items() if valeur not in (None, [])}
            # La validation Pydantic s'applique : une surcharge malformée produite
            # par le modèle est rejetée plutôt qu'injectée.
            return Filtres.model_validate({**filtres_base.model_dump(mode="json"), **propres})

        def indicateurs(arguments: dict[str, Any]) -> Any:
            filtres = filtres_de(arguments)
            agregat = depot.agregat(filtres)
            precedent = depot.agregat(filtres.periode_precedente()) if filtres.date_debut else None
            return {
                "periode": {"debut": str(filtres.date_debut), "fin": str(filtres.date_fin)},
                "indicateurs": [
                    indicateur.model_dump() for indicateur in construire_indicateurs(agregat, precedent)
                ],
                "concentration": depot.concentration(filtres),
            }

        def serie(arguments: dict[str, Any]) -> Any:
            lignes = depot.serie_hebdomadaire(filtres_de(arguments))
            colonnes = (
                "semaine_libelle", "conso_theorique", "conso_reelle", "ecart_net",
                "non_consommation_valorisee", "surconsommation_valorisee",
                "ecart_valorise", "nb_lignes_ecart",
            )
            return _serialiser(
                ({cle: ligne.get(cle) for cle in colonnes} for ligne in lignes), 120
            )

        def repartition(arguments: dict[str, Any]) -> Any:
            return _serialiser(
                depot.repartition(filtres_de(arguments), arguments.get("dimension", "programme")),
                LIMITE_LIGNES_OUTIL,
            )

        def top(arguments: dict[str, Any]) -> Any:
            limite = min(int(arguments.get("limite", 10)), LIMITE_LIGNES_OUTIL)
            return _serialiser(depot.top_composants(filtres_de(arguments), limite), limite)

        def detail(arguments: dict[str, Any]) -> Any:
            limite = min(int(arguments.get("limite", 20)), LIMITE_LIGNES_OUTIL)
            page = depot.grille("details", filtres_de(arguments), taille=limite, page=1)
            return _serialiser(page["lignes"], limite)

        return {
            "indicateurs": indicateurs,
            "serie_hebdomadaire": serie,
            "repartition": repartition,
            "top_composants": top,
            "detail_lignes": detail,
            "fiche_article": lambda a: _json_dict(depot.fiche_article(str(a.get("item_id", "")))),
            "nomenclature_parent": lambda a: _serialiser(
                depot.nomenclature_du_parent(str(a.get("parent_itemid", ""))), LIMITE_LIGNES_OUTIL
            ),
            "parents_composant": lambda a: _serialiser(
                depot.parents_du_composant(str(a.get("child_itemid", ""))), LIMITE_LIGNES_OUTIL
            ),
            "qualite_donnees": lambda a: _json_dict(depot.fraicheur()),
        }

    # -- Dialogue ----------------------------------------------------------
    def repondre(
        self, historique: list[MessageChat], filtres: Filtres, page_active: str = "synthese"
    ) -> ReponseAssistant:
        """Conduit un tour de dialogue outillé et retourne la réponse finale."""
        client = self._openai()
        outils = self._outils(filtres)
        appels: list[AppelOutil] = []

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PROMPT_SYSTEME.format(contexte=contexte_metier())},
            {"role": "system", "content": self._contexte_ecran(filtres, page_active)},
        ]
        messages.extend({"role": message.role, "content": message.contenu} for message in historique)

        for tour in range(self._settings.llm_max_tool_rounds):
            reponse = self._appeler_modele(client, messages)
            message = reponse.choices[0].message
            demandes = getattr(message, "tool_calls", None) or []

            messages.append({
                "role": "assistant",
                "content": message.content or "",
                **({"tool_calls": [_dumper_appel(appel) for appel in demandes]} if demandes else {}),
            })

            if not demandes:
                return ReponseAssistant(
                    reponse=message.content or "Je n'ai pas pu produire de réponse.",
                    appels=appels,
                    modele=self._settings.llm_endpoint,
                )

            for demande in demandes:
                nom = demande.function.name
                arguments = _charger_arguments(demande.function.arguments)
                trace = AppelOutil(outil=nom, arguments=arguments)
                try:
                    resultat = outils[nom](arguments) if nom in outils else {
                        "erreur": f"Outil inconnu : {nom}"
                    }
                    trace.nb_lignes = len(resultat) if isinstance(resultat, list) else None
                except KeyError:
                    resultat = {"erreur": f"Outil inconnu : {nom}"}
                    trace.erreur = resultat["erreur"]
                except Exception as exc:
                    LOGGER.warning("Outil %s en échec : %s", nom, exc)
                    resultat = {"erreur": "L'outil a échoué ; reformule ou change d'approche."}
                    trace.erreur = str(exc)[:200]
                appels.append(trace)
                messages.append({
                    "role": "tool",
                    "tool_call_id": demande.id,
                    "content": json.dumps(resultat, ensure_ascii=False, default=str),
                })

            LOGGER.info("Tour %d : %d outil(s) appelé(s).", tour + 1, len(demandes))

        return ReponseAssistant(
            reponse=(
                "Je n'ai pas convergé vers une réponse dans le nombre d'étapes autorisé. "
                "Reformulez la question en la restreignant à un programme ou à une période."
            ),
            appels=appels,
            modele=self._settings.llm_endpoint,
        )

    def analyser_lot(
        self, grille_cle: str, lignes: list[dict[str, Any]], filtres: Filtres, question: str | None
    ) -> ReponseAssistant:
        """Analyse un lot de lignes sélectionnées dans une grille.

        Le lot est transmis tel quel au modèle (borné à
        :data:`LIMITE_LIGNES_LOT`) : l'utilisateur a explicitement choisi ces
        lignes, l'assistant n'a donc pas à les redécouvrir par des outils.
        """
        client = self._openai()
        grille = GRILLES.get(grille_cle)
        if grille is None:
            raise AssistantIndisponibleError(f"Grille inconnue : {grille_cle}.")

        echantillon = _serialiser(lignes, LIMITE_LIGNES_LOT)
        consigne = question or (
            "Analyse ce lot : quels sont les cas les plus coûteux, quels motifs se "
            "répètent, et quelles actions engager en priorité ?"
        )
        messages = [
            {"role": "system", "content": PROMPT_SYSTEME.format(contexte=contexte_metier())},
            {"role": "system", "content": self._contexte_ecran(filtres, grille_cle)},
            {
                "role": "user",
                "content": (
                    f"{consigne}\n\n"
                    f"Grille « {grille.libelle} » — {len(echantillon)} ligne(s) sélectionnée(s)"
                    f"{' (échantillon tronqué)' if len(lignes) > LIMITE_LIGNES_LOT else ''} :\n"
                    f"```json\n{json.dumps(echantillon, ensure_ascii=False, default=str)}\n```"
                ),
            },
        ]
        reponse = self._appeler_modele(client, messages, avec_outils=False)
        return ReponseAssistant(
            reponse=reponse.choices[0].message.content or "Analyse indisponible.",
            appels=[AppelOutil(outil="selection_utilisateur", arguments={"grille": grille_cle},
                               nb_lignes=len(echantillon))],
            modele=self._settings.llm_endpoint,
        )

    # -- Diagnostic --------------------------------------------------------
    def diagnostic(self) -> dict[str, Any]:
        """Pourquoi l'assistant ne répond pas — en une réponse, pas en un ticket.

        Une panne d'assistant a presque toujours une cause de configuration, et
        toutes ces causes se ressemblent vues de l'écran : le modèle ne répond
        pas. Elles ne se ressemblent pas du tout une fois nommées — un endpoint
        qui n'existe pas dans l'espace de travail, un principal de service sans
        droit d'interrogation, un paramètre refusé par le fournisseur — et
        chacune se corrige autrement.

        Les trois étapes sont menées dans l'ordre où elles s'excluent, et la
        suivante n'est tentée que si la précédente a tenu :

        1. **client** — le SDK arrive-t-il à construire un client de serving ?
        2. **catalogue** — le endpoint configuré figure-t-il parmi ceux que le
           principal de service voit ? Les endpoints visibles sont retournés,
           pour que la correction se fasse par copier-coller plutôt que de
           mémoire.
        3. **appel** — un appel minimal (quelques jetons, aucun outil) passe-t-il ?
           C'est ce qui distingue « endpoint absent » de « endpoint présent mais
           qui refuse notre requête ».

        La route ne renvoie JAMAIS d'erreur HTTP : un diagnostic qui échoue en
        503 n'aurait rien diagnostiqué. Chaque étape porte son propre verdict.
        """
        etapes: list[dict[str, Any]] = []

        def etape(nom: str, ok: bool, **reste: Any) -> None:
            etapes.append({"etape": nom, "ok": ok, **reste})

        if not self._settings.llm_enabled:
            etape("configuration", False,
                  message="L'assistant est désactivé (LLM_ENABLED=false).")
            return {"endpoint": self._settings.llm_endpoint, "ok": False, "etapes": etapes}

        # 1) Client de serving
        try:
            client = self._openai()
            etape("client", True, message="Client de serving construit.")
        except Exception as exc:
            etape("client", False, message=_cause_lisible(exc),
                  remede="Vérifiez que la ressource « serving endpoint » est attachée à "
                         "l'application et que le principal de service peut l'interroger.")
            return {"endpoint": self._settings.llm_endpoint, "ok": False, "etapes": etapes}

        # 2) Le endpoint configuré existe-t-il ?
        attendu = self._settings.llm_endpoint
        try:
            from databricks.sdk import WorkspaceClient

            noms = sorted(
                point.name
                for point in WorkspaceClient().serving_endpoints.list()
                if point.name
            )
        except Exception as exc:
            # Ne pas pouvoir LISTER n'empêche pas d'APPELER : le principal de
            # service peut avoir le droit d'interroger sans celui d'énumérer.
            # L'étape est donc informative, et le diagnostic continue.
            etape("catalogue", True, message="Catalogue non consultable : " + _cause_lisible(exc),
                  remede="Sans conséquence si l'appel ci-dessous aboutit.")
        else:
            proches = [nom for nom in noms if _RACINE_ENDPOINT.match(nom)]
            if attendu in noms:
                etape("catalogue", True, message=f"Le endpoint « {attendu} » existe.")
            else:
                etape(
                    "catalogue", False,
                    message=f"Le endpoint « {attendu} » n'existe pas dans cet espace de travail.",
                    remede="Reprenez un nom de la liste ci-contre dans la variable "
                           "LLM_ENDPOINT (app.yaml), puis redéployez l'application.",
                    endpoints_disponibles=proches or noms[:40],
                )

        # 3) Appel minimal — sans outils, quelques jetons : on teste le lien,
        #    pas le raisonnement.
        try:
            reponse = client.chat.completions.create(
                model=attendu,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=16,
            )
            contenu = (reponse.choices[0].message.content or "").strip()
            etape("appel", True, message=f"Le endpoint répond ({contenu[:60] or 'réponse vide'}).")
        except Exception as exc:
            etape("appel", False, message=_cause_lisible(exc),
                  remede=_remede_probable(exc))

        return {
            "endpoint": attendu,
            "ok": all(etape_["ok"] for etape_ in etapes),
            "etapes": etapes,
        }

    # -- Interne -----------------------------------------------------------
    def _appeler_modele(self, client: Any, messages: list[dict[str, Any]], *, avec_outils: bool = True):
        """Un appel au endpoint de serving, dont l'échec reste diagnosticable.

        Le message affiché nomme le endpoint et reprend la cause du fournisseur.
        La version précédente disait « Le modèle n'a pas répondu. Réessayez dans
        quelques instants. » quelle que soit la cause : un nom de endpoint faux,
        un droit manquant ou un paramètre refusé ne se corrigent pas en
        réessayant, et l'utilisateur n'avait aucun moyen de le savoir.
        """
        charge: dict[str, Any] = {
            "model": self._settings.llm_endpoint,
            "messages": messages,
            "max_tokens": self._settings.llm_max_tokens,
            **({"tools": definitions_outils(), "tool_choice": "auto"} if avec_outils else {}),
        }
        # `temperature` reste omissible par configuration (`LLM_TEMPERATURE`
        # vide), en plus de l'être par apprentissage : sur un endpoint connu
        # pour la refuser, autant ne jamais payer l'aller-retour.
        if self._settings.llm_temperature is not None:
            charge["temperature"] = self._settings.llm_temperature
        for refuse in self._parametres_refuses:
            charge.pop(refuse, None)

        try:
            return client.chat.completions.create(**charge)
        except Exception as exc:
            # Le fournisseur a-t-il nommé un paramètre qu'il ne supporte pas ?
            # Si oui, on le retire et on rejoue UNE fois. La récursion est
            # bornée : chaque tour ajoute un nom à un ensemble fini, et un
            # paramètre déjà retiré ne peut plus être mis en cause.
            refuse = _parametre_refuse(exc)
            if refuse and refuse not in self._parametres_refuses:
                self._parametres_refuses.add(refuse)
                LOGGER.warning(
                    "Le endpoint « %s » refuse le paramètre « %s » : il est retiré "
                    "des appels suivants.",
                    self._settings.llm_endpoint, refuse,
                )
                return self._appeler_modele(client, messages, avec_outils=avec_outils)

            cause = _cause_lisible(exc)
            # exc_info : la trace complète va dans les journaux de l'application,
            # où elle est consultable sans redéployer quoi que ce soit.
            LOGGER.error(
                "Appel au endpoint « %s » en échec : %s",
                self._settings.llm_endpoint, cause, exc_info=True,
            )
            raise AssistantIndisponibleError(
                f"Le endpoint « {self._settings.llm_endpoint} » n'a pas répondu. "
                f"{_remede_probable(exc)}",
                detail=cause,
            ) from exc

    @staticmethod
    def _contexte_ecran(filtres: Filtres, page_active: str) -> str:
        return (
            "CONTEXTE DE L'ÉCRAN — l'utilisateur regarde actuellement ces données. "
            "Sauf demande contraire explicite, raisonne sur ce périmètre.\n"
            f"Page : {page_active}\n"
            f"Filtres actifs : {filtres.model_dump_json(exclude_defaults=True)}\n"
            f"Date du jour : {date.today().isoformat()}"
        )


def _json_dict(valeur: dict[str, Any] | None) -> dict[str, Any]:
    if not valeur:
        return {}
    return json.loads(json.dumps(valeur, ensure_ascii=False, default=str))


def _charger_arguments(brut: str | None) -> dict[str, Any]:
    """Décode les arguments d'un appel d'outil, en tolérant un JSON malformé."""
    if not brut:
        return {}
    try:
        charge = json.loads(brut)
    except json.JSONDecodeError:
        LOGGER.warning("Arguments d'outil illisibles : %s", brut[:200])
        return {}
    return charge if isinstance(charge, dict) else {}


def _dumper_appel(appel: Any) -> dict[str, Any]:
    return {
        "id": appel.id,
        "type": "function",
        "function": {"name": appel.function.name, "arguments": appel.function.arguments},
    }


#: Familles de endpoints de serving à proposer en priorité quand le nom
#: configuré est introuvable. Une liste complète peut compter des dizaines
#: d'entrées (modèles maison, embeddings) sans rapport avec le besoin.
_RACINE_ENDPOINT = re.compile(r"^databricks-(claude|llama|gpt|gemma|mixtral|meta)", re.I)


def _cause_lisible(exc: Exception) -> str:
    """Réduit une exception de fournisseur à une phrase actionnable.

    Le client OpenAI enveloppe la réponse HTTP : le code de statut et le message
    du fournisseur y sont, mais noyés dans une représentation qui contient aussi
    l'URL complète et les en-têtes. On extrait les deux éléments qui portent
    l'information, et on borne la longueur — un pavé de mille caractères dans
    une bulle de conversation n'est pas plus lisible qu'un silence.
    """
    # Nos propres erreurs sont déjà une lecture : les relire produirait
    # « HTTP 503 — <notre message> », c'est-à-dire notre message enveloppé dans
    # notre code de statut, et la cause d'origine — celle du SDK ou du
    # fournisseur — serait perdue au profit de rien.
    if isinstance(exc, BackflushError):
        return exc.detail or exc.message

    statut = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    corps = getattr(exc, "body", None)
    message = None
    if isinstance(corps, dict):
        message = corps.get("message") or corps.get("error_code")
    message = message or getattr(exc, "message", None) or str(exc)
    message = " ".join(str(message).split())[:400]
    return f"HTTP {statut} — {message}" if statut else message


#: Paramètres facultatifs de la requête, qu'on sait retirer si le fournisseur
#: les refuse. `messages` et `model` n'y figurent pas : sans eux il n'y a pas
#: d'appel, et un refus les concernant est une erreur de code, pas de réglage.
_PARAMETRES_FACULTATIFS: tuple[str, ...] = ("temperature", "top_p", "max_tokens")

#: Formulations par lesquelles un fournisseur signale un paramètre non supporté.
#: Volontairement restrictif : un `400` pour une autre raison — jeton de trop,
#: message mal formé — ne doit PAS déclencher le retrait d'un paramètre, sous
#: peine de dégrader silencieusement tous les appels suivants.
_REFUS_DE_PARAMETRE = re.compile(
    r"does not support|not supported|unsupported (?:parameter|value)|"
    r"unrecognized (?:request )?argument",
    re.I,
)


def _parametre_refuse(exc: Exception) -> str | None:
    """Nom du paramètre que le fournisseur déclare ne pas supporter, s'il y en a.

    Exemple réel, sur `eu.anthropic.claude-opus-4-8` ::

        HTTP 400 — BAD_REQUEST: Model eu.anthropic.claude-opus-4-8 does not
        support the temperature parameter.
    """
    if getattr(exc, "status_code", None) != 400:
        return None
    message = _cause_lisible(exc)
    if not _REFUS_DE_PARAMETRE.search(message):
        return None
    minuscule = message.lower()
    for nom in _PARAMETRES_FACULTATIFS:
        if nom in minuscule:
            return nom
    return None


def _remede_probable(exc: Exception) -> str:
    """Traduit un code de statut en geste de correction.

    Les quatre codes qu'on rencontre ici ne se corrigent pas au même endroit, et
    aucun ne se corrige en réessayant — ce que le message générique invitait
    pourtant à faire.
    """
    statut = getattr(exc, "status_code", None)
    if statut == 404:
        return (
            "Endpoint introuvable. Vérifiez LLM_ENDPOINT dans app/app.yaml : le nom "
            "doit être celui d'un endpoint de CET espace de travail."
        )
    if statut in (401, 403):
        return (
            "Droits insuffisants. Le principal de service de l'application doit avoir "
            "le privilège « Can Query » sur le endpoint (Serving → Permissions)."
        )
    if statut == 400:
        return (
            "Requête refusée par le fournisseur. Le message ci-dessus nomme le "
            "paramètre en cause — le plus souvent max_tokens, temperature, ou "
            "l'usage des outils, tous trois réglables dans les paramètres de "
            "l'application."
        )
    if statut == 429:
        return "Quota atteint. Réessayez, ou passez sur un endpoint provisionné."
    if statut and statut >= 500:
        return "Panne côté fournisseur. Là, réessayer a du sens."
    return (
        "Cause non reconnue. Le message ci-dessus vient du fournisseur ; "
        "les journaux de l'application en portent la trace complète."
    )
