/**
 * Bandeau d'indicateurs.
 *
 * Chaque tuile porte sa définition (au survol), son unité, sa période et sa
 * variation par rapport à la période précédente. Un nombre sans définition ni
 * provenance n'est pas un indicateur : c'est une décoration.
 *
 * La polarité (`sens_favorable`) vient du serveur : le frontend ne décide pas
 * qu'une hausse est bonne ou mauvaise, il l'affiche.
 */

import type { Indicateur } from '@/api/types'
import { euro, nombre, valeurIndicateur, variation } from '@/lib/format'
import { Squelette } from './Etats'

export function BandeauIndicateurs({
  indicateurs,
  chargement,
  periode,
  onSelection,
}: {
  indicateurs: Indicateur[] | undefined
  chargement: boolean
  periode: string
  onSelection?: (indicateur: Indicateur) => void
}) {
  if (chargement || !indicateurs) {
    return (
      <div className="kpis">
        {Array.from({ length: 8 }, (_, index) => (
          <Squelette key={index} hauteur={86} />
        ))}
      </div>
    )
  }

  return (
    <div className="kpis">
      {indicateurs.map((indicateur) => (
        <TuileIndicateur
          key={indicateur.cle}
          indicateur={indicateur}
          periode={periode}
          onSelection={onSelection}
        />
      ))}
    </div>
  )
}

function TuileIndicateur({
  indicateur,
  periode,
  onSelection,
}: {
  indicateur: Indicateur
  periode: string
  onSelection?: (indicateur: Indicateur) => void
}) {
  const classeDelta = classerVariation(indicateur)

  return (
    <button
      type="button"
      className="kpi"
      onClick={() => onSelection?.(indicateur)}
      title={`${indicateur.aide}\n\nPériode : ${periode}`}
      aria-label={`${indicateur.libelle} : ${valeurIndicateur(indicateur.valeur, indicateur.format)} ${indicateur.unite}`}
    >
      <span className="kpi__libelle">
        {indicateur.libelle}
        <span className="attenue" aria-hidden="true">
          ⓘ
        </span>
      </span>
      <div className="kpi__valeur">
        {valeurIndicateur(indicateur.valeur, indicateur.format)}
        {indicateur.format !== 'euro' && indicateur.format !== 'pourcent' && (
          <span className="kpi__unite">{indicateur.unite}</span>
        )}
      </div>
      <div className="kpi__pied">
        {indicateur.variation_pct === null ? (
          <span>Pas de période comparable</span>
        ) : (
          <>
            <span className={classeDelta}>{variation(indicateur.variation_pct)}</span>
            <span>
              vs période précédente
              {indicateur.variation_absolue !== null && (
                <> ({formaterEcart(indicateur)})</>
              )}
            </span>
          </>
        )}
      </div>
    </button>
  )
}

/**
 * Couleur de la variation : verte si elle va dans le sens favorable déclaré,
 * rouge sinon, neutre lorsque le serveur ne se prononce pas — ce qui est le cas
 * de l'écart net, dont la hausse n'est ni bonne ni mauvaise dans l'absolu.
 */
function classerVariation(indicateur: Indicateur): string {
  if (indicateur.sens_favorable === 'neutre' || indicateur.variation_pct === null) {
    return 'kpi__delta--neutre'
  }
  const augmente = indicateur.variation_pct > 0
  const favorable = indicateur.sens_favorable === 'hausse' ? augmente : !augmente
  return favorable ? 'kpi__delta--favorable' : 'kpi__delta--defavorable'
}

function formaterEcart(indicateur: Indicateur): string {
  const valeur = indicateur.variation_absolue ?? 0
  const signe = valeur > 0 ? '+' : ''
  if (indicateur.format === 'euro') return `${signe}${euro(valeur, 0, true)}`
  if (indicateur.format === 'pourcent') return `${signe}${nombre(valeur, 1)} pt`
  return `${signe}${nombre(valeur, 0)}`
}
