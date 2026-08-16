/**
 * Grille de données : tri, pagination et filtrage **côté serveur**, sélection
 * multiple et actions par lot.
 *
 * Point de conception le plus important : la grille ne trie jamais elle-même.
 * Trier la page courante donnerait un classement faux — c'est le défaut
 * classique des tableaux de bord maison, où le « top 10 » n'est que le top des
 * cinquante lignes déjà chargées. Chaque changement de tri redemande une page
 * au serveur.
 *
 * Les colonnes ne sont pas codées ici : elles viennent de `/api/meta/grilles`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api, telechargerExport } from '@/api/client'
import type { CleGrille, Colonne, Filtres, Grille, LigneGrille } from '@/api/types'
import { valeurCellule } from '@/lib/format'
import { EtatErreur, EtatVide, SqueletteLignes } from './Etats'

const TAILLES_PAGE = [25, 50, 100, 250]

/** Colonnes ouvrant une fiche de détail au clic. */
const COLONNES_FICHE: Record<string, 'composant' | 'parent'> = {
  child_itemid: 'composant',
  parent_itemid: 'parent',
}

export interface ActionLot {
  libelle: string
  aide?: string
  /** Reçoit les lignes sélectionnées, ou la page courante si rien n'est coché. */
  executer: (lignes: LigneGrille[]) => void
  desactive?: boolean
}

export function GrilleDonnees({
  cle,
  grille,
  filtres,
  titre,
  actions = [],
  onOuvrirFiche,
  hauteurSquelette = 10,
}: {
  cle: CleGrille
  grille: Grille
  filtres: Filtres
  titre?: string
  actions?: ActionLot[]
  onOuvrirFiche?: (genre: 'composant' | 'parent', id: string) => void
  hauteurSquelette?: number
}) {
  const [tri, setTri] = useState<string>(grille.tri_defaut)
  const [sens, setSens] = useState<'asc' | 'desc'>(grille.sens_defaut)
  const [page, setPage] = useState(1)
  const [taille, setTaille] = useState(50)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [colonnesVisibles, setColonnesVisibles] = useState<string[]>(() =>
    grille.colonnes.filter((colonne) => colonne.visible).map((colonne) => colonne.cle),
  )
  const [panneauColonnes, setPanneauColonnes] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)
  const zoneDefilement = useRef<HTMLDivElement>(null)

  // Un changement de filtre invalide la pagination ET la sélection : garder des
  // lignes cochées qui ne sont plus dans le périmètre produirait un export ou
  // une analyse portant sur des données que l'utilisateur ne voit plus.
  useEffect(() => {
    setPage(1)
    setSelection(new Set())
  }, [filtres])

  const requete = useQuery({
    queryKey: ['grille', cle, filtres, tri, sens, page, taille],
    queryFn: () => api.pageGrille(cle, { filtres, tri, sens, page, taille }),
    placeholderData: (precedent) => precedent,
  })

  const colonnes = useMemo(
    () => grille.colonnes.filter((colonne) => colonnesVisibles.includes(colonne.cle)),
    [grille.colonnes, colonnesVisibles],
  )

  const cleLigne = useCallback(
    (ligne: LigneGrille) => grille.cle_ligne.map((champ) => String(ligne[champ] ?? '')).join('§'),
    [grille.cle_ligne],
  )

  const lignes = requete.data?.lignes ?? []
  const lignesSelectionnees = useMemo(
    () => lignes.filter((ligne) => selection.has(cleLigne(ligne))),
    [lignes, selection, cleLigne],
  )
  const lignesCibles = lignesSelectionnees.length > 0 ? lignesSelectionnees : lignes

  const changerTri = (colonne: Colonne) => {
    if (!colonne.triable) return
    if (colonne.cle === tri) setSens((precedent) => (precedent === 'asc' ? 'desc' : 'asc'))
    else {
      setTri(colonne.cle)
      // Un nouveau tri part du plus significatif : décroissant pour une mesure,
      // croissant pour un libellé.
      setSens(colonne.type === 'texte' || colonne.type === 'date' ? 'asc' : 'desc')
    }
    setPage(1)
    zoneDefilement.current?.scrollTo({ top: 0 })
  }

  const basculerLigne = (ligne: LigneGrille) => {
    const identifiant = cleLigne(ligne)
    setSelection((precedent) => {
      const suivant = new Set(precedent)
      if (suivant.has(identifiant)) suivant.delete(identifiant)
      else suivant.add(identifiant)
      return suivant
    })
  }

  const toutePageCochee = lignes.length > 0 && lignes.every((ligne) => selection.has(cleLigne(ligne)))

  const basculerPage = () => {
    setSelection((precedent) => {
      const suivant = new Set(precedent)
      for (const ligne of lignes) {
        if (toutePageCochee) suivant.delete(cleLigne(ligne))
        else suivant.add(cleLigne(ligne))
      }
      return suivant
    })
  }

  const annoncer = (texte: string) => {
    setMessage(texte)
    setTimeout(() => setMessage(null), 4_000)
  }

  const copierLignes = async (source: LigneGrille[]) => {
    const entete = colonnes.map((colonne) => colonne.libelle).join('\t')
    const corps = source
      .map((ligne) =>
        colonnes
          .map((colonne) => texteBrut(ligne[colonne.cle], colonne))
          .join('\t'),
      )
      .join('\n')
    await ecrirePressePapiers(`${entete}\n${corps}`)
    annoncer(`${source.length} ligne(s) copiée(s) — collables dans Excel.`)
  }

  const copierToutFiltre = async () => {
    setOccupe(true)
    try {
      const resultat = await api.pressePapiers(cle, { filtres, tri, sens })
      await ecrirePressePapiers(resultat.tsv)
      annoncer(`${resultat.lignes} ligne(s) copiée(s) — l'ensemble du périmètre filtré.`)
    } catch (erreur) {
      annoncer(erreur instanceof Error ? erreur.message : 'La copie a échoué.')
    } finally {
      setOccupe(false)
    }
  }

  const exporter = async () => {
    setOccupe(true)
    try {
      const resultat = await telechargerExport(cle, {
        filtres,
        tri,
        sens,
        colonnes: colonnesVisibles,
      })
      annoncer(`Export généré : ${resultat.nom}`)
    } catch (erreur) {
      annoncer(erreur instanceof Error ? erreur.message : "L'export a échoué.")
    } finally {
      setOccupe(false)
    }
  }

  const total = requete.data?.total ?? 0
  const nbPages = requete.data?.nb_pages ?? 0

  return (
    <div className="grille">
      <div className="grille__barre">
        <span className="grille__titre">{titre ?? grille.libelle}</span>
        <span className="grille__compteur" title={grille.description}>
          {requete.isFetching && !requete.data
            ? 'chargement…'
            : `${total.toLocaleString('fr-FR')} ligne${total > 1 ? 's' : ''}`}
          {selection.size > 0 && ` · ${selection.size} sélectionnée${selection.size > 1 ? 's' : ''}`}
        </span>

        <div className="rang" style={{ marginLeft: 'auto', flexWrap: 'wrap' }}>
          {selection.size > 0 && (
            <button type="button" className="bouton bouton--discret" onClick={() => setSelection(new Set())}>
              Désélectionner
            </button>
          )}
          <button
            type="button"
            className="bouton"
            onClick={() => void copierLignes(lignesCibles)}
            disabled={lignes.length === 0}
            title="Copie les lignes sélectionnées, ou la page affichée si aucune sélection."
          >
            Copier {selection.size > 0 ? `(${selection.size})` : 'la page'}
          </button>
          <button
            type="button"
            className="bouton"
            onClick={() => void copierToutFiltre()}
            disabled={occupe || total === 0}
            title="Copie l'intégralité du périmètre filtré, au-delà de la page affichée."
          >
            Copier tout
          </button>
          <button
            type="button"
            className="bouton"
            onClick={() => void exporter()}
            disabled={occupe || total === 0}
            title="Classeur Excel formaté, avec un onglet rappelant les filtres appliqués."
          >
            Export Excel
          </button>
          {actions.map((action) => (
            <button
              key={action.libelle}
              type="button"
              className="bouton bouton--principal"
              onClick={() => action.executer(lignesCibles)}
              disabled={action.desactive || lignes.length === 0}
              title={action.aide}
            >
              {action.libelle}
              {selection.size > 0 ? ` (${selection.size})` : ''}
            </button>
          ))}
          <div style={{ position: 'relative' }}>
            <button
              type="button"
              className="bouton bouton--icone"
              onClick={() => setPanneauColonnes((precedent) => !precedent)}
              aria-expanded={panneauColonnes}
              title="Colonnes affichées"
            >
              ⚙
            </button>
            {panneauColonnes && (
              <div className="multi__panneau" style={{ right: 0, left: 'auto' }}>
                {grille.colonnes.map((colonne) => (
                  <label key={colonne.cle} className="multi__option" title={colonne.aide}>
                    <input
                      type="checkbox"
                      checked={colonnesVisibles.includes(colonne.cle)}
                      onChange={() =>
                        setColonnesVisibles((precedent) =>
                          precedent.includes(colonne.cle)
                            ? precedent.filter((valeur) => valeur !== colonne.cle)
                            : [...precedent, colonne.cle],
                        )
                      }
                    />
                    <span className="multi__valeur">{colonne.libelle}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {message && (
        <div className="bandeau bandeau--info" style={{ margin: 'var(--espace-2)' }} role="status">
          {message}
        </div>
      )}

      <div className="grille__defilement" ref={zoneDefilement}>
        {requete.isError ? (
          <EtatErreur erreur={requete.error} onReessayer={() => void requete.refetch()} />
        ) : requete.isPending ? (
          <SqueletteLignes lignes={hauteurSquelette} />
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
                    className={`${classeAlignement(colonne)} ${colonne.triable ? '' : 'non-triable'}`}
                    style={{ minWidth: colonne.largeur }}
                    onClick={() => changerTri(colonne)}
                    title={colonne.aide || undefined}
                    aria-sort={
                      tri === colonne.cle ? (sens === 'asc' ? 'ascending' : 'descending') : 'none'
                    }
                  >
                    {colonne.libelle}
                    {tri === colonne.cle && (
                      <span className="tableau__tri" aria-hidden="true">
                        {sens === 'asc' ? '▲' : '▼'}
                      </span>
                    )}
                  </th>
                ))}
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
                        onChange={() => basculerLigne(ligne)}
                        aria-label="Sélectionner la ligne"
                      />
                    </td>
                    {colonnes.map((colonne) => (
                      <Cellule
                        key={colonne.cle}
                        colonne={colonne}
                        valeur={ligne[colonne.cle]}
                        onOuvrirFiche={onOuvrirFiche}
                      />
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="grille__pied">
        <button
          type="button"
          className="bouton"
          onClick={() => setPage((precedent) => Math.max(1, precedent - 1))}
          disabled={page <= 1}
        >
          ← Précédent
        </button>
        <span className="attenue">
          Page {page} sur {Math.max(nbPages, 1)}
        </span>
        <button
          type="button"
          className="bouton"
          onClick={() => setPage((precedent) => Math.min(nbPages || 1, precedent + 1))}
          disabled={page >= nbPages}
        >
          Suivant →
        </button>
        <label className="rang attenue" style={{ marginLeft: 'auto' }}>
          Lignes par page
          <select
            className="champ"
            value={taille}
            onChange={(evenement) => {
              setTaille(Number(evenement.target.value))
              setPage(1)
            }}
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

function Cellule({
  colonne,
  valeur,
  onOuvrirFiche,
}: {
  colonne: Colonne
  valeur: unknown
  onOuvrirFiche?: (genre: 'composant' | 'parent', id: string) => void
}) {
  const genreFiche = COLONNES_FICHE[colonne.cle]
  const texte = valeurCellule(valeur, colonne.type, colonne.decimales)
  const negatif =
    (colonne.type === 'euro' || colonne.type === 'decimal' || colonne.type === 'pourcent') &&
    Number(valeur) < 0

  if (colonne.type === 'badge') {
    return (
      <td className={classeAlignement(colonne)}>
        <Pastille valeur={String(valeur ?? '')} />
      </td>
    )
  }

  if (genreFiche && onOuvrirFiche && valeur) {
    return (
      <td className={classeAlignement(colonne)}>
        <span
          className="cellule--lien"
          role="button"
          tabIndex={0}
          onClick={() => onOuvrirFiche(genreFiche, String(valeur))}
          onKeyDown={(evenement) => {
            if (evenement.key === 'Enter' || evenement.key === ' ') {
              evenement.preventDefault()
              onOuvrirFiche(genreFiche, String(valeur))
            }
          }}
        >
          {texte}
        </span>
      </td>
    )
  }

  return (
    <td className={`${classeAlignement(colonne)}${negatif ? ' cellule--negatif' : ''}`} title={texte}>
      {texte}
    </td>
  )
}

/**
 * Pastille de type / statut. La couleur encode la POLARITÉ de l'écart, jamais
 * un jugement ; elle est toujours doublée par le libellé, donc jamais seule
 * porteuse de sens.
 */
export function Pastille({ valeur }: { valeur: string }) {
  const couleurs: Record<string, string> = {
    'Non-consommation': 'var(--pole-non-conso)',
    Surconsommation: 'var(--pole-surconso)',
    Conforme: 'var(--encre-attenuee)',
    Nominal: 'var(--encre-attenuee)',
    'Hors nomenclature': 'var(--statut-critique)',
    'Sans consommation': 'var(--statut-serieux)',
  }
  const couleur = couleurs[valeur] ?? 'var(--encre-attenuee)'
  return (
    <span className="pastille" style={{ background: 'var(--survol)' }}>
      <span className="pastille__point" style={{ background: couleur }} />
      {valeur || '—'}
    </span>
  )
}

function classeAlignement(colonne: Colonne): string {
  return colonne.alignement === 'droite' ? 'droite' : colonne.alignement === 'centre' ? 'centre' : ''
}

function texteBrut(valeur: unknown, colonne: Colonne): string {
  if (valeur === null || valeur === undefined) return ''
  if (typeof valeur === 'boolean') return valeur ? 'Oui' : 'Non'
  if (typeof valeur === 'number') {
    // Virgule décimale sans séparateur de milliers : Excel reconnaît alors un
    // nombre en configuration française.
    return String(valeur).replace('.', ',')
  }
  if (colonne.type === 'date') return String(valeur).slice(0, 10)
  return String(valeur).replace(/[\t\n\r]/g, ' ')
}

/**
 * Écriture dans le presse-papiers, avec repli.
 *
 * `navigator.clipboard` exige un contexte sécurisé (HTTPS) : présent sur
 * Databricks Apps, absent en développement sur http://localhost dans certains
 * navigateurs. Le repli garantit que la fonctionnalité marche partout.
 */
async function ecrirePressePapiers(texte: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(texte)
      return
    } catch {
      /* permission refusée : on tente le repli */
    }
  }
  const zone = document.createElement('textarea')
  zone.value = texte
  zone.setAttribute('readonly', '')
  zone.style.position = 'fixed'
  zone.style.opacity = '0'
  document.body.appendChild(zone)
  zone.select()
  document.execCommand('copy')
  zone.remove()
}
