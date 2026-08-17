/**
 * Table de paramétrage : recherche, tri et pagination serveur, sélection
 * multiple, actions par ligne et par lot.
 *
 * Distincte de `GrilleDonnees`, qui sert les grilles analytiques : celles-ci
 * lisent la table de faits et se pilotent par les filtres transverses, alors
 * qu'on est ici dans le référentiel, où l'on MODIFIE. Les deux mécanismes ne
 * partagent ni la source, ni les filtres, ni les actions ; les fondre aurait
 * produit un composant à deux modes exclusifs.
 *
 * Le pied de table porte le décompte de la sélection ENTIÈRE, pas de la page :
 * savoir qu'on s'apprête à agir sur 12 lignes visibles alors que le filtre en
 * retient 4 000 change la nature du geste.
 */

import type { ReactNode } from 'react'

import { EtatErreur, EtatVide, SqueletteLignes } from './Etats'

const TAILLES_PAGE = [25, 50, 100, 250]

export interface ColonneParametrage<T> {
  cle: string
  libelle: string
  aide?: string
  /** Clé de tri serveur. Absente : colonne non triable. */
  tri?: string
  alignement?: 'droite' | 'centre'
  rendu: (ligne: T) => ReactNode
  /** Total de la colonne, calculé sur la page. Absent : rien à totaliser. */
  total?: (lignes: T[]) => ReactNode
}

export interface ActionParametrage<T> {
  libelle: string
  aide?: string
  principale?: boolean
  executer: (lignes: T[]) => void
  /** Rend l'action indisponible, avec la raison en infobulle. */
  indisponible?: (lignes: T[]) => string | null
}

export function TableParametrage<T>({
  titre,
  description,
  colonnes,
  page,
  chargement,
  erreur,
  onReessayer,
  cleLigne,
  selection,
  onSelection,
  actions,
  actionsLigne,
  tri,
  sens,
  onTri,
  onPage,
  onTaille,
  barre,
  message,
}: {
  titre: string
  description?: string
  colonnes: Array<ColonneParametrage<T>>
  page: { lignes: T[]; total: number; page: number; taille: number; nb_pages: number } | undefined
  chargement: boolean
  erreur: unknown
  onReessayer: () => void
  cleLigne: (ligne: T) => string
  selection: Set<string>
  onSelection: (selection: Set<string>) => void
  actions?: Array<ActionParametrage<T>>
  actionsLigne?: (ligne: T) => ReactNode
  tri: string
  sens: 'asc' | 'desc'
  onTri: (tri: string, sens: 'asc' | 'desc') => void
  onPage: (page: number) => void
  onTaille: (taille: number) => void
  /** Contrôles propres à l'écran : recherche, filtre d'état… */
  barre?: ReactNode
  message?: string | null
}) {
  const lignes = page?.lignes ?? []
  const selectionnees = lignes.filter((ligne) => selection.has(cleLigne(ligne)))
  // Sans sélection explicite, les actions portent sur la page affichée : c'est
  // le comportement le moins surprenant, et il reste borné à ce qu'on voit.
  const cibles = selectionnees.length > 0 ? selectionnees : lignes

  const basculer = (ligne: T) => {
    const suivant = new Set(selection)
    const identifiant = cleLigne(ligne)
    if (suivant.has(identifiant)) suivant.delete(identifiant)
    else suivant.add(identifiant)
    onSelection(suivant)
  }

  const toutePageCochee = lignes.length > 0 && lignes.every((l) => selection.has(cleLigne(l)))

  const basculerPage = () => {
    const suivant = new Set(selection)
    for (const ligne of lignes) {
      if (toutePageCochee) suivant.delete(cleLigne(ligne))
      else suivant.add(cleLigne(ligne))
    }
    onSelection(suivant)
  }

  const changerTri = (colonne: ColonneParametrage<T>) => {
    if (!colonne.tri) return
    if (colonne.tri === tri) onTri(tri, sens === 'asc' ? 'desc' : 'asc')
    else onTri(colonne.tri, 'asc')
  }

  const total = page?.total ?? 0
  const nbPages = page?.nb_pages ?? 0
  const aDesTotaux = colonnes.some((colonne) => colonne.total)

  return (
    <div className="grille">
      <div className="grille__barre">
        <span className="grille__titre">{titre}</span>
        <span className="grille__compteur" title={description}>
          {chargement && !page ? 'chargement…' : `${total.toLocaleString('fr-FR')} ligne${total > 1 ? 's' : ''}`}
          {selection.size > 0 && ` · ${selection.size} sélectionnée${selection.size > 1 ? 's' : ''}`}
        </span>
        {barre}
        <div className="rang" style={{ marginLeft: 'auto', flexWrap: 'wrap' }}>
          {selection.size > 0 && (
            <button
              type="button"
              className="bouton bouton--discret"
              onClick={() => onSelection(new Set())}
            >
              Désélectionner
            </button>
          )}
          {(actions ?? []).map((action) => {
            const raison = action.indisponible?.(cibles) ?? null
            return (
              <button
                key={action.libelle}
                type="button"
                className={`bouton${action.principale ? ' bouton--principal' : ''}`}
                onClick={() => action.executer(cibles)}
                disabled={lignes.length === 0 || raison !== null}
                title={raison ?? action.aide}
              >
                {action.libelle}
                {selection.size > 0 ? ` (${selection.size})` : ''}
              </button>
            )
          })}
        </div>
      </div>

      {message && (
        <div className="bandeau bandeau--info" style={{ margin: 'var(--espace-2)' }} role="status">
          {message}
        </div>
      )}

      <div className="grille__defilement">
        {erreur ? (
          <EtatErreur erreur={erreur} onReessayer={onReessayer} />
        ) : chargement && !page ? (
          <SqueletteLignes lignes={10} />
        ) : lignes.length === 0 ? (
          <EtatVide />
        ) : (
          <table className="tableau">
            <thead>
              <tr>
                <th className="tableau__case non-triable">
                  <input
                    type="checkbox"
                    checked={toutePageCochee}
                    onChange={basculerPage}
                    aria-label="Sélectionner toutes les lignes de la page"
                  />
                </th>
                {colonnes.map((colonne) => (
                  <th
                    key={colonne.cle}
                    className={`${colonne.alignement ?? ''} ${colonne.tri ? '' : 'non-triable'}`}
                    title={colonne.aide}
                    onClick={() => changerTri(colonne)}
                    aria-sort={
                      colonne.tri === tri ? (sens === 'asc' ? 'ascending' : 'descending') : 'none'
                    }
                  >
                    {colonne.libelle}
                    {colonne.tri === tri && (
                      <span className="tableau__tri" aria-hidden="true">
                        {sens === 'asc' ? '▲' : '▼'}
                      </span>
                    )}
                  </th>
                ))}
                {actionsLigne && <th className="non-triable" />}
              </tr>
            </thead>
            <tbody>
              {lignes.map((ligne) => {
                const identifiant = cleLigne(ligne)
                const cochee = selection.has(identifiant)
                return (
                  <tr key={identifiant} aria-selected={cochee}>
                    <td className="tableau__case">
                      <input
                        type="checkbox"
                        checked={cochee}
                        onChange={() => basculer(ligne)}
                        aria-label="Sélectionner la ligne"
                      />
                    </td>
                    {colonnes.map((colonne) => (
                      <td key={colonne.cle} className={colonne.alignement ?? ''}>
                        {colonne.rendu(ligne)}
                      </td>
                    ))}
                    {actionsLigne && <td className="droite">{actionsLigne(ligne)}</td>}
                  </tr>
                )
              })}
              {aDesTotaux && (
                <tr className="tableau__cale" aria-hidden="true">
                  <td colSpan={colonnes.length + (actionsLigne ? 2 : 1)} />
                </tr>
              )}
            </tbody>
            {aDesTotaux && (
              <tfoot className="tableau__pied">
                <tr>
                  <td className="tableau__case" />
                  {colonnes.map((colonne, index) => (
                    <td key={colonne.cle} className={colonne.alignement ?? ''}>
                      {colonne.total ? (
                        <strong>{colonne.total(lignes)}</strong>
                      ) : index === 0 ? (
                        <strong title="Totaux de la page affichée : le référentiel n'est pas une mesure, l'additionner sur toute la sélection n'aurait pas de sens.">
                          Page ({lignes.length})
                        </strong>
                      ) : null}
                    </td>
                  ))}
                  {actionsLigne && <td />}
                </tr>
              </tfoot>
            )}
          </table>
        )}
      </div>

      <div className="grille__pied">
        <button
          type="button"
          className="bouton"
          onClick={() => onPage(Math.max(1, (page?.page ?? 1) - 1))}
          disabled={(page?.page ?? 1) <= 1}
        >
          ← Précédent
        </button>
        <span className="attenue">
          Page {page?.page ?? 1} sur {Math.max(nbPages, 1)}
        </span>
        <button
          type="button"
          className="bouton"
          onClick={() => onPage(Math.min(nbPages || 1, (page?.page ?? 1) + 1))}
          disabled={(page?.page ?? 1) >= nbPages}
        >
          Suivant →
        </button>
        <label className="rang attenue" style={{ marginLeft: 'auto' }}>
          Lignes par page
          <select
            className="champ"
            value={page?.taille ?? 50}
            onChange={(evenement) => onTaille(Number(evenement.target.value))}
          >
            {TAILLES_PAGE.map((valeur) => (
              <option key={valeur} value={valeur}>
                {valeur}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}
