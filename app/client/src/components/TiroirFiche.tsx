/**
 * Tiroir de détail — dernier niveau du drill-through.
 *
 * Ouvert depuis n'importe quelle référence cliquable (graphique ou grille), il
 * réunit en un seul appel tout ce qu'il faut pour instruire un cas : fiche
 * article, indicateurs restreints à cette référence, série hebdomadaire, et le
 * contexte de nomenclature — qui consomme quoi, avec quel coefficient. Sans ce
 * contexte, on ne peut pas juger si un écart vient de la référence ou du
 * paramétrage.
 */

import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { FicheComposant, FicheParent, Filtres } from '@/api/types'
import { euro, nombre, valeurIndicateur } from '@/lib/format'
import { useMesure } from '@/state/mesure'
import { TendanceHebdo } from './charts/TendanceHebdo'
import { EtatErreur, EtatVide, Squelette } from './Etats'

/** Étendue d'un jeu de coefficients : une valeur, ou l'intervalle observé. */
function etendueCoef(valeurs: number[]): string {
  const propres = valeurs.filter((valeur) => Number.isFinite(valeur))
  if (propres.length === 0) return '—'
  const min = Math.min(...propres)
  const max = Math.max(...propres)
  return min === max ? nombre(min, 4) : `${nombre(min, 4)} – ${nombre(max, 4)}`
}

export function TiroirFiche({
  genre,
  identifiant,
  filtres,
  onFermer,
  onFiltrerSur,
}: {
  genre: 'composant' | 'parent'
  identifiant: string
  filtres: Filtres
  onFermer: () => void
  onFiltrerSur: (genre: 'composant' | 'parent', identifiant: string) => void
}) {
  // La fiche hérite de la mesure active : rouvrir une référence après avoir
  // basculé en quantité ne doit pas rebasculer son graphique en euros.
  const { enValeur } = useMesure()

  // Échap ferme le tiroir : attendu de tout panneau superposé.
  useEffect(() => {
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === 'Escape') onFermer()
    }
    document.addEventListener('keydown', surTouche)
    return () => document.removeEventListener('keydown', surTouche)
  }, [onFermer])

  // Le type de retour dépend du genre demandé : on l'annonce explicitement en
  // union, et les sections spécifiques sont ensuite discriminées par `in`.
  const requete = useQuery<FicheComposant | FicheParent>({
    queryKey: ['fiche', genre, identifiant, filtres],
    queryFn: () =>
      genre === 'composant'
        ? api.ficheComposant(identifiant, filtres)
        : api.ficheParent(identifiant, filtres),
  })

  const donnees = requete.data
  const article = donnees?.article ?? null

  return (
    <>
      <div className="tiroir-voile" onClick={onFermer} aria-hidden="true" />
      <aside className="tiroir" role="dialog" aria-modal="true" aria-label={`Fiche ${identifiant}`}>
        <header className="tiroir__entete">
          <div>
            <div style={{ fontWeight: 650 }}>{identifiant}</div>
            <div className="attenue" style={{ fontSize: 11.5 }}>
              {genre === 'composant' ? 'Composant' : 'Article parent'}
              {article?.item_name ? ` · ${article.item_name}` : ''}
            </div>
          </div>
          <div className="rang" style={{ marginLeft: 'auto' }}>
            <button
              type="button"
              className="bouton"
              onClick={() => onFiltrerSur(genre, identifiant)}
              title="Restreindre tous les écrans à cette référence"
            >
              Filtrer sur cette référence
            </button>
            <button type="button" className="bouton bouton--icone" onClick={onFermer} aria-label="Fermer">
              ✕
            </button>
          </div>
        </header>

        <div className="tiroir__corps">
          {requete.isError ? (
            <EtatErreur erreur={requete.error} onReessayer={() => void requete.refetch()} />
          ) : requete.isPending ? (
            <div className="pile">
              <Squelette hauteur={110} />
              <Squelette hauteur={160} />
            </div>
          ) : !donnees ? (
            <EtatVide />
          ) : (
            <div className="pile">
              <section>
                <h3 style={{ margin: '0 0 8px', fontSize: 12 }}>Référentiel</h3>
                <dl className="definition">
                  <dt>Désignation</dt>
                  <dd>{article?.item_name ?? '—'}</dd>
                  <dt>Catégorie</dt>
                  <dd>{article?.categorie ?? '—'}</dd>
                  <dt>Groupe</dt>
                  <dd>{article?.item_group_label ?? '—'}</dd>
                  <dt>Programme</dt>
                  <dd>{article?.programme ?? '—'}</dd>
                  <dt>Coût standard</dt>
                  <dd>
                    {article?.std_cost_price === null || article?.std_cost_price === undefined ? (
                      <span style={{ color: 'var(--statut-critique)' }}>
                        absent — impact financier sous-estimé
                      </span>
                    ) : (
                      `${euro(article.std_cost_price, 4)} / ${article.std_unit ?? 'PCE'}`
                    )}
                  </dd>
                </dl>
              </section>

              <section>
                <h3 style={{ margin: '0 0 8px', fontSize: 12 }}>
                  Indicateurs sur la période filtrée
                </h3>
                <dl className="definition">
                  {donnees.indicateurs.map((indicateur) => (
                    <div key={indicateur.cle} style={{ display: 'contents' }}>
                      <dt title={indicateur.aide}>{indicateur.libelle}</dt>
                      <dd>
                        {valeurIndicateur(indicateur.valeur, indicateur.format)}
                        {indicateur.format !== 'euro' && indicateur.format !== 'pourcent'
                          ? ` ${indicateur.unite}`
                          : ''}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>

              <section>
                <h3 style={{ margin: '0 0 8px', fontSize: 12 }}>Évolution hebdomadaire</h3>
                {donnees.semaines.length === 0 ? (
                  <EtatVide message="Aucun mouvement sur la période." />
                ) : (
                  <TendanceHebdo
                    semaines={donnees.semaines}
                    enValeur={enValeur}
                    largeur={520}
                  />
                )}
              </section>

              {genre === 'composant' && 'parents' in donnees && (
                <section>
                  <h3 style={{ margin: '0 0 8px', fontSize: 12 }}>
                    Articles parents consommant ce composant
                  </h3>
                  {donnees.parents.length === 0 ? (
                    <EtatVide
                      titre="Aucun parent"
                      message="Ce composant n'apparaît dans aucune nomenclature active — d'où un statut « Hors nomenclature »."
                    />
                  ) : (
                    <TableauSimple
                      entetes={['Parent', 'Désignation', 'Programme', 'Coef BOM']}
                      lignes={donnees.parents.map((parent) => [
                        parent.parent_itemid,
                        parent.parent_name ?? '—',
                        parent.programme ?? '—',
                        nombre(parent.coef_bom, 4),
                      ])}
                      // Un coefficient ne s'additionne pas : on affiche son
                      // étendue, qui est ce qui compte ici (uniforme ou non).
                      totaux={[
                        null,
                        null,
                        null,
                        etendueCoef(donnees.parents.map((parent) => Number(parent.coef_bom))),
                      ]}
                      note={
                        new Set(donnees.parents.map((parent) => Number(parent.coef_bom))).size > 1
                          ? "Coefficients différents selon les parents : l'écart en équivalent produit n'est pas calculable."
                          : undefined
                      }
                    />
                  )}
                </section>
              )}

              {genre === 'parent' && 'nomenclature' in donnees && (
                <section>
                  <h3 style={{ margin: '0 0 8px', fontSize: 12 }}>Nomenclature active</h3>
                  {donnees.nomenclature.length === 0 ? (
                    <EtatVide
                      titre="Aucune nomenclature active"
                      message="Aucun écart n'est calculable pour cet article parent."
                    />
                  ) : (
                    <TableauSimple
                      entetes={['Composant', 'Désignation', 'Coef retenu', 'Coût std']}
                      lignes={donnees.nomenclature.map((ligne) => [
                        ligne.child_itemid,
                        ligne.child_name ?? '—',
                        // Une correction est signalée avec la valeur d'origine :
                        // un coefficient corrigé sans repère n'est plus vérifiable.
                        ligne.coef_surcharge
                          ? `${nombre(ligne.coef_bom, 4)} ${ligne.unite ?? ''} (ERP ${nombre(ligne.coef_erp, 4)})`
                          : `${nombre(ligne.coef_bom, 4)} ${ligne.unite ?? ''}`,
                        ligne.std_cost_price === null ? '—' : euro(ligne.std_cost_price, 4),
                      ])}
                      // Le coût matière d'un parent : somme des coefficients
                      // multipliés par les coûts standards. C'est le total qui
                      // a un sens sur une nomenclature.
                      totaux={[
                        null,
                        null,
                        null,
                        euro(
                          donnees.nomenclature.reduce(
                            (somme, ligne) =>
                              somme + Number(ligne.coef_bom ?? 0) * Number(ligne.std_cost_price ?? 0),
                            0,
                          ),
                          2,
                        ),
                      ]}
                    />
                  )}
                </section>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  )
}

/**
 * Petite table du tiroir, avec pied de totaux.
 *
 * `totaux` porte une chaîne par colonne, ou `null` là où il n'y a rien à
 * additionner. Le calcul est fait par l'appelant, qui seul connaît la nature de
 * chaque colonne : sommer un coefficient de nomenclature n'aurait aucun sens,
 * alors que sommer des coûts en a un.
 */
function TableauSimple({
  entetes,
  lignes,
  totaux,
  note,
}: {
  entetes: string[]
  lignes: string[][]
  totaux?: Array<string | null>
  note?: string
}) {
  return (
    <>
      <table className="tableau">
        <thead>
          <tr>
            {entetes.map((entete, index) => (
              <th key={entete} className={`non-triable${index >= 2 ? ' droite' : ''}`}>
                {entete}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lignes.map((ligne, index) => (
            <tr key={`${ligne[0]}-${index}`}>
              {ligne.map((cellule, colonne) => (
                <td key={colonne} className={colonne >= 2 ? 'droite' : ''} title={cellule}>
                  {cellule}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {totaux && (
          <tfoot className="tableau__pied">
            <tr>
              {entetes.map((entete, index) => (
                <td key={entete} className={index >= 2 ? 'droite' : ''}>
                  <strong>
                    {index === 0 ? `Total (${lignes.length})` : (totaux[index] ?? '')}
                  </strong>
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
      {note && (
        <p className="attenue" style={{ marginTop: 6, fontSize: 11.5 }}>
          {note}
        </p>
      )}
    </>
  )
}
