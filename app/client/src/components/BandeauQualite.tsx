/**
 * Bandeau de fraîcheur et de qualité des données.
 *
 * Un tableau de bord d'écarts n'est crédible que si l'on sait quand il a été
 * alimenté et ce qui est douteux dans le calcul lui-même. Le bandeau n'apparaît
 * que lorsqu'il a quelque chose à dire : anomalie de contrôle, ingestion en
 * échec, ou données vieillissantes.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import { anciennete, horodatage, nombre } from '@/lib/format'

/** Au-delà de ce délai, la donnée n'est plus « du jour ». */
const HEURES_AVANT_ALERTE = 30

export function BandeauQualite() {
  const [deplie, setDeplie] = useState(false)
  const requete = useQuery({
    queryKey: ['fraicheur'],
    queryFn: api.fraicheur,
    // La fraîcheur ne change qu'à chaque ingestion quotidienne : inutile de la
    // redemander à chaque navigation.
    staleTime: 5 * 60_000,
  })

  if (requete.isPending || requete.isError || !requete.data) return null

  const { derniere_ingestion, controles, en_echec } = requete.data
  const anomalies = controles.filter((controle) => controle.en_anomalie)
  const erreurs = anomalies.filter((controle) => controle.severite === 'ERREUR')
  const heures = derniere_ingestion
    ? (Date.now() - new Date(derniere_ingestion).getTime()) / 3_600_000
    : Number.POSITIVE_INFINITY
  const perimee = heures > HEURES_AVANT_ALERTE

  if (anomalies.length === 0 && en_echec.length === 0 && !perimee) return null

  const critique = erreurs.length > 0 || en_echec.length > 0

  return (
    <div className={`bandeau${critique ? ' bandeau--critique' : ''}`}>
      <strong>{critique ? 'Qualité des données' : 'Points de vigilance'}</strong>
      <span>
        {en_echec.length > 0 && (
          <>
            Ingestion en échec sur : {en_echec.join(', ')}.{' '}
          </>
        )}
        {perimee && derniere_ingestion && (
          <>Dernière ingestion {anciennete(derniere_ingestion)}. </>
        )}
        {anomalies.length > 0 && (
          <>
            {anomalies.length} contrôle{anomalies.length > 1 ? 's' : ''} en anomalie
            {erreurs.length > 0 ? ` dont ${erreurs.length} bloquant${erreurs.length > 1 ? 's' : ''}` : ''}.
          </>
        )}
      </span>
      <button
        type="button"
        className="bouton bouton--discret"
        style={{ marginLeft: 'auto' }}
        onClick={() => setDeplie((precedent) => !precedent)}
        aria-expanded={deplie}
      >
        {deplie ? 'Masquer' : 'Détail'}
      </button>

      {deplie && (
        <div style={{ flexBasis: '100%', marginTop: 'var(--espace-2)' }}>
          <p className="attenue" style={{ margin: '0 0 6px' }}>
            Dernière ingestion : {horodatage(derniere_ingestion)}
          </p>
          <table className="tableau">
            <thead>
              <tr>
                <th className="non-triable">Contrôle</th>
                <th className="non-triable">Sévérité</th>
                <th className="non-triable droite">Valeur</th>
                <th className="non-triable">Interprétation</th>
              </tr>
            </thead>
            <tbody>
              {anomalies.map((controle) => (
                <tr key={controle.controle}>
                  <td className="mono">{controle.controle}</td>
                  <td>{controle.severite}</td>
                  <td className="droite">{nombre(controle.valeur)}</td>
                  <td title={controle.message} style={{ whiteSpace: 'normal' }}>
                    {controle.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
