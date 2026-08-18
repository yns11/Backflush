/**
 * Panneau de dialogue avec l'assistant IA.
 *
 * Quatre exigences de confiance, toutes visibles à l'écran :
 *
 * 1. **Traçabilité** — chaque réponse expose les outils appelés et leurs
 *    paramètres, dans un bloc dépliable. L'utilisateur voit d'où viennent les
 *    chiffres.
 * 2. **Ancrage** — les filtres de l'écran courant sont rappelés et transmis :
 *    l'assistant répond sur le périmètre affiché, pas sur tout l'historique.
 * 3. **Identité d'exécution** — l'application lit la base avec son principal de
 *    service, pas avec l'identité du visiteur. C'est écrit.
 * 4. **Réserve** — un avertissement accompagne chaque réponse.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { api, ErreurApi } from '@/api/client'
import type { AppelOutil, DiagnosticAssistant, Filtres, ReponseAssistant } from '@/api/types'
import { EtatErreur } from './Etats'

interface Echange {
  role: 'user' | 'assistant'
  contenu: string
  appels?: AppelOutil[]
  avertissement?: string
  /** Cause technique renvoyée par le serveur, sur une panne de configuration. */
  detail?: string
  /** Marque une bulle d'échec : elle propose alors le diagnostic. */
  echec?: boolean
}

export function Assistant({
  filtres,
  pageActive,
  amorce,
  onAmorceConsommee,
}: {
  filtres: Filtres
  pageActive: string
  /** Message injecté depuis un autre écran (analyse d'un lot, par exemple). */
  amorce?: { question: string; reponse: ReponseAssistant } | null
  onAmorceConsommee?: () => void
}) {
  const [echanges, setEchanges] = useState<Echange[]>([])
  const [saisie, setSaisie] = useState('')
  const filRef = useRef<HTMLDivElement>(null)

  // Le diagnostic n'est pas lancé au montage : il fait un vrai appel au
  // endpoint, et le payer à chaque ouverture de l'écran pour un assistant qui
  // fonctionne serait un coût pour rien. Il se déclenche à la demande.
  const [diagnostic, setDiagnostic] = useState<DiagnosticAssistant | null>(null)
  const sonde = useMutation({
    mutationFn: api.diagnosticAssistant,
    onSuccess: setDiagnostic,
  })

  const etat = useQuery({ queryKey: ['assistant-etat'], queryFn: api.etatAssistant })
  const suggestions = useQuery({ queryKey: ['assistant-suggestions'], queryFn: api.suggestions })

  const envoi = useMutation({
    mutationFn: (question: string) =>
      api.chat({
        // L'historique est borné : au-delà, le coût croît sans améliorer la
        // pertinence, et les premiers tours ne portent plus le contexte utile.
        messages: [...echanges, { role: 'user' as const, contenu: question }]
          .slice(-10)
          .map(({ role, contenu }) => ({ role, contenu })),
        filtres,
        page_active: pageActive,
      }),
    onSuccess: (reponse) => {
      setEchanges((precedents) => [
        ...precedents,
        {
          role: 'assistant',
          contenu: reponse.reponse,
          appels: reponse.appels,
          avertissement: reponse.avertissement,
        },
      ])
    },
    onError: (erreur: unknown) => {
      setEchanges((precedents) => [
        ...precedents,
        {
          role: 'assistant',
          contenu:
            erreur instanceof Error
              ? erreur.message
              : "L'assistant n'a pas pu répondre. Réessayez dans quelques instants.",
          // La cause technique est affichée telle quelle : sur une panne de
          // configuration, c'est elle qui nomme le paramètre à corriger.
          detail: erreur instanceof ErreurApi ? erreur.detail : undefined,
          echec: true,
        },
      ])
    },
  })

  // Injection d'un échange venu d'un autre écran (analyse de lot).
  useEffect(() => {
    if (!amorce) return
    setEchanges((precedents) => [
      ...precedents,
      { role: 'user', contenu: amorce.question },
      {
        role: 'assistant',
        contenu: amorce.reponse.reponse,
        appels: amorce.reponse.appels,
        avertissement: amorce.reponse.avertissement,
      },
    ])
    onAmorceConsommee?.()
  }, [amorce, onAmorceConsommee])

  useEffect(() => {
    filRef.current?.scrollTo({ top: filRef.current.scrollHeight, behavior: 'smooth' })
  }, [echanges, envoi.isPending])

  const envoyer = (question: string) => {
    const propre = question.trim()
    if (!propre || envoi.isPending) return
    setEchanges((precedents) => [...precedents, { role: 'user', contenu: propre }])
    setSaisie('')
    envoi.mutate(propre)
  }

  if (etat.isError) return <EtatErreur erreur={etat.error} onReessayer={() => void etat.refetch()} />

  if (etat.data && !etat.data.actif) {
    return (
      <div className="assistant">
        <div className="etat-vide">
          <div className="etat-vide__titre">Assistant désactivé sur cette instance</div>
          <div>
            Activez-le en attachant une ressource « serving endpoint » à l'application et en
            définissant <code className="mono">LLM_ENABLED=true</code>.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="assistant">
      <div className="grille__barre">
        <span className="grille__titre">Assistant écarts backflush</span>
        <span className="grille__compteur">
          {etat.data?.modele ? `modèle : ${etat.data.modele}` : ''}
          {etat.data?.execution ? ` · exécution : ${etat.data.execution}` : ''}
        </span>
        {echanges.length > 0 && (
          <button
            type="button"
            className="bouton bouton--discret"
            style={{ marginLeft: 'auto' }}
            onClick={() => setEchanges([])}
          >
            Nouvelle conversation
          </button>
        )}
      </div>

      {diagnostic && (
        <PanneauDiagnostic diagnostic={diagnostic} onFermer={() => setDiagnostic(null)} />
      )}
      {sonde.isError && (
        <div className="bandeau bandeau--critique" role="alert">
          Le diagnostic lui-même n'a pas abouti :{' '}
          {sonde.error instanceof Error ? sonde.error.message : 'cause inconnue'}. Cela désigne
          l'application, pas le endpoint — consultez <code className="mono">/api/health</code>.
        </div>
      )}

      <div className="assistant__fil" ref={filRef}>
        {echanges.length === 0 && (
          <div className="attenue" style={{ maxWidth: '70ch' }}>
            <p style={{ marginTop: 0 }}>
              Posez une question sur la production, la consommation, les écarts, la base article ou
              la nomenclature. L'assistant interroge les mêmes données que les écrans, en respectant
              les filtres actifs — il n'invente aucun chiffre et indique quand il ne peut pas
              conclure.
            </p>
            <p>
              Périmètre actuellement transmis : {resumeFiltres(filtres)}.
            </p>
          </div>
        )}

        {echanges.map((echange, index) => (
          <div
            key={index}
            className={`message message--${echange.role === 'user' ? 'utilisateur' : 'assistant'}`}
          >
            <div className="message__contenu">{echange.contenu}</div>
            {echange.detail && (
              <div className="message__cause mono">{echange.detail}</div>
            )}
            {echange.echec && (
              <button
                type="button"
                className="bouton bouton--discret"
                style={{ marginTop: 8 }}
                disabled={sonde.isPending}
                onClick={() => sonde.mutate()}
              >
                {sonde.isPending ? 'Diagnostic en cours…' : 'Diagnostiquer la connexion'}
              </button>
            )}
            {echange.appels && echange.appels.length > 0 && (
              <details className="message__trace">
                <summary>
                  {echange.appels.length} appel{echange.appels.length > 1 ? 's' : ''} de données —
                  voir le détail
                </summary>
                <pre>
                  {echange.appels
                    .map(
                      (appel) =>
                        `${appel.outil}(${JSON.stringify(appel.arguments)})` +
                        (appel.nb_lignes !== null ? ` → ${appel.nb_lignes} ligne(s)` : '') +
                        (appel.erreur ? ` ⚠ ${appel.erreur}` : ''),
                    )
                    .join('\n')}
                </pre>
              </details>
            )}
            {echange.avertissement && (
              <div className="message__avertissement">{echange.avertissement}</div>
            )}
          </div>
        ))}

        {envoi.isPending && (
          <div className="message message--assistant attenue" role="status">
            Interrogation des données en cours…
          </div>
        )}
      </div>

      {echanges.length === 0 && suggestions.data && (
        <div className="suggestions">
          {suggestions.data.suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="suggestion"
              onClick={() => envoyer(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <form
        className="assistant__saisie"
        onSubmit={(evenement) => {
          evenement.preventDefault()
          envoyer(saisie)
        }}
      >
        <textarea
          value={saisie}
          onChange={(evenement) => setSaisie(evenement.target.value)}
          onKeyDown={(evenement) => {
            // Entrée envoie, Maj+Entrée insère un retour à la ligne.
            if (evenement.key === 'Enter' && !evenement.shiftKey) {
              evenement.preventDefault()
              envoyer(saisie)
            }
          }}
          placeholder="Votre question… (Entrée pour envoyer, Maj+Entrée pour un retour à la ligne)"
          aria-label="Question à l'assistant"
          disabled={envoi.isPending}
        />
        <button
          type="submit"
          className="bouton bouton--principal"
          disabled={envoi.isPending || saisie.trim().length === 0}
        >
          Envoyer
        </button>
      </form>
    </div>
  )
}

function resumeFiltres(filtres: Filtres): string {
  const morceaux: string[] = []
  if (filtres.date_debut && filtres.date_fin) {
    morceaux.push(`semaines du ${filtres.date_debut} au ${filtres.date_fin}`)
  }
  if (filtres.programmes.length) morceaux.push(`programmes ${filtres.programmes.join(', ')}`)
  if (filtres.categories.length) morceaux.push(`catégories ${filtres.categories.join(', ')}`)
  if (filtres.composants.length) morceaux.push(`composants ${filtres.composants.join(', ')}`)
  return morceaux.length ? morceaux.join(' · ') : "tout l'historique disponible"
}

/**
 * Résultat du diagnostic : une ligne par étape, dans l'ordre où elles
 * s'excluent. La première en échec est la cause ; les suivantes n'ont pas été
 * tentées ou n'ont plus de sens, et c'est pourquoi l'ordre est conservé tel que
 * le serveur l'a produit plutôt que trié par gravité.
 */
function PanneauDiagnostic({
  diagnostic,
  onFermer,
}: {
  diagnostic: DiagnosticAssistant
  onFermer: () => void
}) {
  return (
    <div className="diagnostic" role="status">
      <div className="rang">
        <strong>
          Diagnostic — endpoint <span className="mono">{diagnostic.endpoint}</span>
        </strong>
        <button
          type="button"
          className="bouton bouton--discret"
          style={{ marginLeft: 'auto' }}
          onClick={onFermer}
        >
          Masquer
        </button>
      </div>

      <ol className="diagnostic__etapes">
        {diagnostic.etapes.map((etape) => (
          <li key={etape.etape} className={etape.ok ? '' : 'diagnostic--echec'}>
            <span className="diagnostic__verdict" aria-hidden="true">
              {etape.ok ? '✓' : '✕'}
            </span>
            <div>
              <div className="diagnostic__nom">{etape.etape}</div>
              <div>{etape.message}</div>
              {etape.remede && <div className="attenue">{etape.remede}</div>}
              {etape.endpoints_disponibles && etape.endpoints_disponibles.length > 0 && (
                <details className="message__trace">
                  <summary>
                    {etape.endpoints_disponibles.length} endpoint(s) disponible(s) — voir la liste
                  </summary>
                  <ul className="mono">
                    {etape.endpoints_disponibles.map((nom) => (
                      <li key={nom}>{nom}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
