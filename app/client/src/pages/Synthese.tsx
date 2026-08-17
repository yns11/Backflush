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
import type { Indicateur } from '@/api/types'
import { Carte, Legende } from '@/components/Carte'
import { BlocRepliable } from '@/components/Repliable'
import { BandeauIndicateurs } from '@/components/Indicateurs'
import { EtatVide, Squelette, VueDonnees } from '@/components/Etats'
import { BarresDivergentes, type ElementBarre } from '@/components/charts/BarresDivergentes'
import { TendanceHebdo } from '@/components/charts/TendanceHebdo'
import { euro, nombre, pourcent, valeurIndicateur } from '@/lib/format'
import { useFiltres } from '@/state/filtres'
import { useMesure } from '@/state/mesure'
import { useNavigation } from '@/state/navigation'

export function Synthese() {
  const { filtres, modifier } = useFiltres()
  const { aller, ouvrirFiche } = useNavigation()
  const { mesure, enValeur } = useMesure()

  /**
   * Grandeur portée par les barres, selon la mesure active.
   *
   * En euros comme en unités, la valeur reste SIGNÉE : le signe encode la
   * polarité de l'écart (non-consommation vs surconsommation), qui est la
   * lecture métier. Seul le classement, lui, se fait sur la valeur absolue —
   * il est décidé côté serveur.
   */
  const impact = (ligne: { ecart_valorise: number; ecart_net: number }) =>
    Number(enValeur ? ligne.ecart_valorise : ligne.ecart_net)
  const formaterImpact = (valeur: number) =>
    enValeur ? euro(valeur, 0, true) : nombre(valeur, 0)
  /**
   * Impact ABSOLU du groupe, dans l'unité active.
   *
   * Ce n'est pas la valeur absolue de l'impact net : dans un même groupe, une
   * non-consommation et une surconsommation se compenseraient, alors qu'elles
   * s'additionnent en volume d'anomalie. Le serveur fournit les deux sommes ;
   * il suffit de lire la bonne — utiliser celle en euros en mode quantité
   * afficherait un montant sous un libellé d'unités.
   */
  const impactAbsolu = (ligne: { ecart_valorise_absolu: number; ecart_absolu: number }) =>
    Number(enValeur ? ligne.ecart_valorise_absolu : ligne.ecart_absolu)

  const indicateurs = useQuery({
    queryKey: ['indicateurs', filtres, mesure],
    queryFn: () => api.indicateurs(filtres, mesure),
  })
  const tendance = useQuery({
    queryKey: ['tendance', filtres],
    queryFn: () => api.tendance(filtres),
  })
  const programmes = useQuery({
    queryKey: ['repartition', 'programme', filtres, mesure],
    queryFn: () => api.repartition('programme', filtres, mesure),
  })
  const perimetres = useQuery({
    queryKey: ['repartition', 'perimetre', filtres, mesure],
    queryFn: () => api.repartition('perimetre', filtres, mesure),
  })
  const categories = useQuery({
    queryKey: ['repartition', 'categorie', filtres, mesure],
    queryFn: () => api.repartition('categorie', filtres, mesure),
  })
  const statuts = useQuery({
    queryKey: ['repartition', 'statut', filtres],
    queryFn: () => api.repartition('statut', filtres),
  })
  const top = useQuery({
    queryKey: ['top-composants', filtres, mesure],
    queryFn: () => api.topComposants(filtres, 12, mesure),
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
      <BlocRepliable
        cle="synthese.kpis"
        titre="Indicateurs"
        resume={resumeIndicateurs(indicateurs.data?.indicateurs)}
      >
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
      </BlocRepliable>

      {indicateurs.data && (
        <ConcentrationCarte
          concentration={indicateurs.data.concentration}
          onVoirReferences={() => aller('references')}
        />
      )}

      <Carte
        pliCle="synthese.tendance"
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

      <div className="grille-graphiques grille-graphiques--trois">
        <Carte
          pliCle="synthese.programme"
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
                    valeur: impact(ligne),
                    details: [
                      { libelle: 'Lignes en écart', valeur: nombre(Number(ligne.nb_lignes_ecart)) },
                      { libelle: 'Composants', valeur: nombre(Number(ligne.nb_composants)) },
                      {
                        libelle: 'Impact absolu',
                        valeur: formaterImpact(impactAbsolu(ligne)),
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

        <Carte
          pliCle="synthese.perimetre"
          titre="Impact par périmètre"
          message="Cliquer pour filtrer et descendre au détail"
          aide="Le périmètre est la ligne de production du parent fabriqué. C'est la maille sur laquelle l'écart en équivalent produit est calculable."
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
            chargement={perimetres.isPending}
            erreur={perimetres.error}
            donnees={perimetres.data}
            estVide={(donnees) => donnees.lignes.length === 0}
            onReessayer={() => void perimetres.refetch()}
            squelette={<Squelette hauteur={200} />}
          >
            {(donnees) => (
              <BarresDivergentes
                elements={donnees.lignes.slice(0, 12).map(
                  (ligne): ElementBarre => ({
                    cle: ligne.libelle,
                    libelle: ligne.libelle,
                    valeur: impact(ligne),
                    details: [
                      { libelle: 'Lignes en écart', valeur: nombre(Number(ligne.nb_lignes_ecart)) },
                      { libelle: 'Composants', valeur: nombre(Number(ligne.nb_composants)) },
                      {
                        libelle: 'Impact absolu',
                        valeur: formaterImpact(impactAbsolu(ligne)),
                      },
                    ],
                  }),
                )}
                onSelection={(element) => {
                  modifier({ perimetres: [element.cle] })
                  aller('perimetres')
                }}
              />
            )}
          </VueDonnees>
        </Carte>

        <Carte
          pliCle="synthese.categorie"
          titre="Impact par catégorie de composant"
          message="Cliquer pour filtrer"
        >
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
                    valeur: impact(ligne),
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
          pliCle="synthese.top"
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
                    valeur: impact(ligne),
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
          pliCle="synthese.statuts"
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
                  <tfoot className="tableau__pied">
                    <tr>
                      <td>
                        <strong>Total ({donnees.lignes.length})</strong>
                      </td>
                      <td className="droite">
                        <strong>
                          {nombre(
                            donnees.lignes.reduce((s, l) => s + Number(l.nb_lignes), 0),
                          )}
                        </strong>
                      </td>
                      <td className="droite">
                        <strong>
                          {euro(
                            donnees.lignes.reduce((s, l) => s + Number(l.ecart_valorise), 0),
                            0,
                            true,
                          )}
                        </strong>
                      </td>
                      <td className="droite">
                        <strong>
                          {euro(
                            donnees.lignes.reduce(
                              (s, l) => s + Number(l.ecart_valorise_absolu),
                              0,
                            ),
                            0,
                            true,
                          )}
                        </strong>
                      </td>
                    </tr>
                  </tfoot>
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

/** Les deux chiffres qu'un responsable veut voir même bandeau replié. */
function resumeIndicateurs(indicateurs: Indicateur[] | undefined): string {
  if (!indicateurs) return ''
  const net = indicateurs.find((i) => i.cle === 'ecart_valorise_net')
  const lignes = indicateurs.find((i) => i.cle === 'nb_lignes_ecart')
  const parts: string[] = []
  if (net) parts.push(`${valeurIndicateur(net.valeur, net.format)} d'impact net`)
  if (lignes) parts.push(`${nombre(lignes.valeur)} ligne(s) en écart`)
  return parts.join(' · ')
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
