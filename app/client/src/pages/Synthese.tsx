/**
 * Écran de synthèse — genre « tableau de bord analytique ».
 *
 * Lecture descendante : d'abord l'impact financier (le langage du comité de
 * direction), puis sa dynamique dans le temps, puis sa localisation
 * (programme, catégorie), enfin les références à instruire. Chaque élément est
 * un point d'entrée de drill-through : cliquer restreint les filtres et ouvre
 * l'écran adéquat, plutôt que d'exiger de recomposer la sélection à la main.
 */

import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import { Carte, Legende } from '@/components/Carte'
import { BandeauIndicateurs } from '@/components/Indicateurs'
import { EtatVide, Squelette, VueDonnees } from '@/components/Etats'
import { BarresDivergentes, type ElementBarre } from '@/components/charts/BarresDivergentes'
import { TendanceHebdo } from '@/components/charts/TendanceHebdo'
import { euro, nombre, pourcent } from '@/lib/format'
import { useFiltres } from '@/state/filtres'
import { useNavigation } from '@/state/navigation'

export function Synthese() {
  const { filtres, modifier } = useFiltres()
  const { aller, ouvrirFiche } = useNavigation()

  const indicateurs = useQuery({
    queryKey: ['indicateurs', filtres],
    queryFn: () => api.indicateurs(filtres),
  })
  const tendance = useQuery({
    queryKey: ['tendance', filtres],
    queryFn: () => api.tendance(filtres),
  })
  const programmes = useQuery({
    queryKey: ['repartition', 'programme', filtres],
    queryFn: () => api.repartition('programme', filtres),
  })
  const categories = useQuery({
    queryKey: ['repartition', 'categorie', filtres],
    queryFn: () => api.repartition('categorie', filtres),
  })
  const statuts = useQuery({
    queryKey: ['repartition', 'statut', filtres],
    queryFn: () => api.repartition('statut', filtres),
  })
  const top = useQuery({
    queryKey: ['top-composants', filtres],
    queryFn: () => api.topComposants(filtres, 12),
  })

  const periode =
    filtres.date_debut && filtres.date_fin
      ? `${filtres.date_debut} → ${filtres.date_fin}`
      : "tout l'historique"

  const messageTendance = useMemo(() => {
    const semaines = tendance.data?.semaines ?? []
    if (semaines.length < 2) return undefined
    const derniere = semaines[semaines.length - 1]
    const precedente = semaines[semaines.length - 2]
    if (!derniere || !precedente) return undefined
    const ecart = Number(derniere.ecart_valorise_absolu) - Number(precedente.ecart_valorise_absolu)
    const sens = ecart >= 0 ? 'en hausse' : 'en baisse'
    return `Dernière semaine ${sens} de ${euro(Math.abs(ecart), 0, true)} en impact absolu.`
  }, [tendance.data])

  return (
    <div className="pile">
      <BandeauIndicateurs
        indicateurs={indicateurs.data?.indicateurs}
        chargement={indicateurs.isPending}
        periode={periode}
        onSelection={(indicateur) => {
          // Cliquer sur un KPI de décomposition applique le filtre correspondant
          // et bascule sur le détail : c'est le drill-through le plus direct.
          if (indicateur.cle === 'non_consommation_valorisee') {
            modifier({ types_ecart: ['Non-consommation'] })
            aller('detail')
          } else if (indicateur.cle === 'surconsommation_valorisee') {
            modifier({ types_ecart: ['Surconsommation'] })
            aller('detail')
          } else if (indicateur.cle === 'nb_lignes_ecart') {
            modifier({ exclure_conforme: true })
            aller('detail')
          } else {
            aller('detail')
          }
        }}
      />

      {indicateurs.data && (
        <ConcentrationCarte
          concentration={indicateurs.data.concentration}
          onVoirReferences={() => aller('references')}
        />
      )}

      <Carte
        titre="Tendance hebdomadaire"
        message={messageTendance}
        aide="Panneau haut : quantités théorique et réelle. Panneau bas : impact financier signé."
        legende={
          <Legende
            items={[
              { libelle: 'Conso. théorique (référence)', couleur: 'var(--serie-1)', motif: true },
              { libelle: 'Conso. réelle (mesure)', couleur: 'var(--serie-2)' },
              { libelle: 'Non-consommation (€)', couleur: 'var(--pole-non-conso)' },
              { libelle: 'Surconsommation (€)', couleur: 'var(--pole-surconso)' },
            ]}
          />
        }
      >
        <VueDonnees
          chargement={tendance.isPending}
          erreur={tendance.error}
          donnees={tendance.data}
          estVide={(donnees) => donnees.semaines.length === 0}
          onReessayer={() => void tendance.refetch()}
          squelette={<Squelette hauteur={300} />}
        >
          {(donnees) => (
            <TendanceHebdo
              semaines={donnees.semaines}
              onSelectionSemaine={(semaine) => {
                modifier({ date_debut: semaine.semaine_debut, date_fin: semaine.semaine_debut })
                aller('detail')
              }}
            />
          )}
        </VueDonnees>
      </Carte>

      <div className="grille-graphiques grille-graphiques--deux">
        <Carte
          titre="Impact par programme"
          message="Cliquer pour filtrer et descendre au détail"
          legende={
            <Legende
              items={[
                { libelle: 'Non-consommation', couleur: 'var(--pole-non-conso)' },
                { libelle: 'Surconsommation', couleur: 'var(--pole-surconso)' },
              ]}
            />
          }
        >
          <VueDonnees
            chargement={programmes.isPending}
            erreur={programmes.error}
            donnees={programmes.data}
            estVide={(donnees) => donnees.lignes.length === 0}
            onReessayer={() => void programmes.refetch()}
            squelette={<Squelette hauteur={200} />}
          >
            {(donnees) => (
              <BarresDivergentes
                elements={donnees.lignes.map(
                  (ligne): ElementBarre => ({
                    cle: ligne.libelle,
                    libelle: ligne.libelle,
                    valeur: Number(ligne.ecart_valorise),
                    details: [
                      { libelle: 'Lignes en écart', valeur: nombre(Number(ligne.nb_lignes_ecart)) },
                      { libelle: 'Composants', valeur: nombre(Number(ligne.nb_composants)) },
                      {
                        libelle: 'Impact absolu',
                        valeur: euro(Number(ligne.ecart_valorise_absolu), 0, true),
                      },
                    ],
                  }),
                )}
                onSelection={(element) => {
                  modifier({ programmes: [element.cle] })
                  aller('programmes')
                }}
              />
            )}
          </VueDonnees>
        </Carte>

        <Carte titre="Impact par catégorie de composant" message="Cliquer pour filtrer">
          <VueDonnees
            chargement={categories.isPending}
            erreur={categories.error}
            donnees={categories.data}
            estVide={(donnees) => donnees.lignes.length === 0}
            onReessayer={() => void categories.refetch()}
            squelette={<Squelette hauteur={200} />}
          >
            {(donnees) => (
              <BarresDivergentes
                elements={donnees.lignes.slice(0, 12).map(
                  (ligne): ElementBarre => ({
                    cle: ligne.libelle,
                    libelle: ligne.libelle,
                    valeur: Number(ligne.ecart_valorise),
                    details: [
                      { libelle: 'Lignes en écart', valeur: nombre(Number(ligne.nb_lignes_ecart)) },
                    ],
                  }),
                )}
                onSelection={(element) => {
                  modifier({ categories: [element.cle] })
                  aller('references')
                }}
              />
            )}
          </VueDonnees>
        </Carte>
      </div>

      <div className="grille-graphiques grille-graphiques--deux">
        <Carte
          titre="Top 12 des références par impact"
          message="Cliquer pour ouvrir la fiche de la référence"
        >
          <VueDonnees
            chargement={top.isPending}
            erreur={top.error}
            donnees={top.data}
            estVide={(donnees) => donnees.lignes.length === 0}
            onReessayer={() => void top.refetch()}
            squelette={<Squelette hauteur={300} />}
          >
            {(donnees) => (
              <BarresDivergentes
                elements={donnees.lignes.map(
                  (ligne): ElementBarre => ({
                    cle: `${ligne.child_itemid}§${ligne.parent_programme}`,
                    libelle: `${ligne.child_itemid} · ${ligne.parent_programme}`,
                    valeur: Number(ligne.ecart_valorise),
                    reference: ligne.child_itemid,
                    details: [
                      { libelle: 'Désignation', valeur: ligne.child_name ?? '—' },
                      { libelle: 'Catégorie', valeur: ligne.child_categorie ?? '—' },
                      { libelle: 'Écart net (qté)', valeur: nombre(Number(ligne.ecart_net), 1) },
                      { libelle: 'Lignes en écart', valeur: nombre(Number(ligne.nb_lignes_ecart)) },
                    ],
                  }),
                )}
                onSelection={(element) =>
                  ouvrirFiche({ genre: 'composant', id: element.reference ?? element.cle })
                }
              />
            )}
          </VueDonnees>
        </Carte>

        <Carte
          titre="Nature des anomalies"
          aide="Le statut de ligne complète le type d'écart : il indique si la nomenclature ou le mouvement de stock est en cause."
        >
          <VueDonnees
            chargement={statuts.isPending}
            erreur={statuts.error}
            donnees={statuts.data}
            estVide={(donnees) => donnees.lignes.length === 0}
            onReessayer={() => void statuts.refetch()}
            squelette={<Squelette hauteur={200} />}
            vide={<EtatVide message="Aucune anomalie de structure sur cette sélection." />}
          >
            {(donnees) => (
              <>
                <table className="tableau">
                  <thead>
                    <tr>
                      <th className="non-triable">Statut</th>
                      <th className="non-triable droite">Lignes</th>
                      <th className="non-triable droite">Impact net</th>
                      <th className="non-triable droite">Impact absolu</th>
                    </tr>
                  </thead>
                  <tbody>
                    {donnees.lignes.map((ligne) => (
                      <tr key={ligne.libelle}>
                        <td>{ligne.libelle}</td>
                        <td className="droite">{nombre(Number(ligne.nb_lignes))}</td>
                        <td
                          className={`droite${Number(ligne.ecart_valorise) < 0 ? ' cellule--negatif' : ''}`}
                        >
                          {euro(Number(ligne.ecart_valorise), 0, true)}
                        </td>
                        <td className="droite">
                          {euro(Number(ligne.ecart_valorise_absolu), 0, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="attenue" style={{ marginTop: 10, fontSize: 11.5 }}>
                  « Hors nomenclature » = composant sorti sans ligne de nomenclature (erreur de
                  saisie d'OF ou nomenclature obsolète). « Sans consommation » = ligne de
                  nomenclature sans aucune sortie sur la semaine (backflush non exécuté).
                </p>
              </>
            )}
          </VueDonnees>
        </Carte>
      </div>
    </div>
  )
}

function ConcentrationCarte({
  concentration,
  onVoirReferences,
}: {
  concentration: { nb_references: number; tete: number; impact_tete: number; part_tete_pct: number }
  onVoirReferences: () => void
}) {
  return (
    <div className="bandeau bandeau--info">
      <strong>Concentration</strong>
      <span>
        Les <strong>{concentration.tete}</strong> premières références concentrent{' '}
        <strong>{pourcent(concentration.part_tete_pct)}</strong> de l'impact absolu (
        {euro(concentration.impact_tete, 0, true)} sur {nombre(concentration.nb_references)}{' '}
        références). Une analyse ABC classique : traiter la tête de liste règle l'essentiel du
        montant.
      </span>
      <button
        type="button"
        className="bouton"
        style={{ marginLeft: 'auto' }}
        onClick={onVoirReferences}
      >
        Voir les références
      </button>
    </div>
  )
}
