/**
 * États obligatoires de toute vue de données : chargement, vide, erreur.
 *
 * Un panneau blanc pendant qu'une requête tourne, ou après une erreur, est le
 * défaut le plus coûteux d'un tableau de bord : l'utilisateur ne sait pas s'il
 * doit attendre, recommencer, ou conclure que le chiffre est nul.
 */

import type { ReactNode } from 'react'

import { ErreurApi } from '@/api/client'

export function Squelette({ hauteur = 120 }: { hauteur?: number }) {
  return (
    <div
      className="squelette"
      style={{ height: hauteur, width: '100%' }}
      role="status"
      aria-label="Chargement des données"
    />
  )
}

export function SqueletteLignes({ lignes = 6 }: { lignes?: number }) {
  return (
    <div className="pile" style={{ gap: 6, padding: 'var(--espace-3)' }} role="status">
      <span className="invisible">Chargement des données</span>
      {Array.from({ length: lignes }, (_, index) => (
        <div key={index} className="squelette" style={{ height: 26, opacity: 1 - index * 0.1 }} />
      ))}
    </div>
  )
}

export function EtatVide({
  titre = 'Aucune donnée sur cette sélection',
  message = 'Élargissez la plage de dates ou retirez un filtre.',
  action,
}: {
  titre?: string
  message?: string
  action?: ReactNode
}) {
  return (
    <div className="etat-vide">
      <div className="etat-vide__titre">{titre}</div>
      <div>{message}</div>
      {action}
    </div>
  )
}

export function EtatErreur({
  erreur,
  onReessayer,
}: {
  erreur: unknown
  onReessayer?: () => void
}) {
  const estApi = erreur instanceof ErreurApi
  const message = estApi ? erreur.message : "Une erreur inattendue s'est produite."
  const indisponible = estApi && erreur.estIndisponibilite

  return (
    <div className="etat-erreur" role="alert">
      <div className="etat-vide__titre">
        {indisponible ? 'Données momentanément indisponibles' : 'Erreur de chargement'}
      </div>
      <div>{message}</div>
      {onReessayer && (
        <button type="button" className="bouton" onClick={onReessayer}>
          Réessayer
        </button>
      )}
    </div>
  )
}

/**
 * Enveloppe standard d'une vue de données : aiguille vers l'état adéquat et ne
 * rend le contenu que lorsqu'il existe vraiment.
 */
export function VueDonnees<T>({
  chargement,
  erreur,
  donnees,
  estVide,
  onReessayer,
  squelette,
  vide,
  children,
}: {
  chargement: boolean
  erreur: unknown
  donnees: T | undefined
  estVide?: (donnees: T) => boolean
  onReessayer?: () => void
  squelette?: ReactNode
  vide?: ReactNode
  children: (donnees: T) => ReactNode
}) {
  if (erreur) return <EtatErreur erreur={erreur} onReessayer={onReessayer} />
  if (chargement || donnees === undefined) return <>{squelette ?? <Squelette />}</>
  if (estVide?.(donnees)) return <>{vide ?? <EtatVide />}</>
  return <>{children(donnees)}</>
}
