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
import { nombre, valeurCellule } from '@/lib/format'
import { EtatErreur, EtatVide, SqueletteLignes } from './Etats'
import { BoutonPli, usePli } from './Repliable'

const TAILLES_PAGE = [25, 50, 100, 250]

/** Colonnes ouvrant une fiche de détail au clic. */
const COLONNES_FICHE: Record<string, 'composant' | 'parent'> = {
  child_itemid: 'composant',
  parent_itemid: 'parent',
}

/**
 * Colonne portant un périmètre, quel que soit son nom selon la grille.
 *
 * Cliquer la valeur ouvre la vue synthétique de CE périmètre : c'est le chemin
 * le plus court entre « cette ligne a un problème » et le rapport d'atelier qui
 * le met en contexte. La vue synthétique exigeant un périmètre unique, le clic
 * remplace la sélection de périmètres au lieu de s'y ajouter — mais laisse tous
 * les autres filtres en place.
 */
const COLONNES_PERIMETRE = new Set(['parent_perimetre', 'perimetre'])

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
  onOuvrirPerimetre,
  hauteurSquelette = 10,
  clePli,
  triInitial,
  sensInitial,
}: {
  cle: CleGrille
  grille: Grille
  filtres: Filtres
  titre?: string
  actions?: ActionLot[]
  onOuvrirFiche?: (genre: 'composant' | 'parent', id: string) => void
  /** Absent sur la grille des périmètres : on y est déjà. */
  onOuvrirPerimetre?: (perimetre: string) => void
  hauteurSquelette?: number
  /**
   * Clé sous laquelle l'état plié/déplié est mémorisé, si elle doit différer de
   * la grille. Deux instances d'une MÊME grille dans des contextes différents
   * — l'écran de détail et le tiroir de contexte — se partageraient sinon un
   * seul état : replier l'une replierait l'autre, sans rapport visible.
   */
  clePli?: string
  /**
   * Tri de départ, quand le tri par défaut de la grille ne sert pas la lecture
   * du contexte. Le tri par défaut classe par impact, ce qui est juste pour
   * chercher une anomalie et faux pour en suivre une : les semaines d'un même
   * ordre s'y retrouvent dispersées dans la page.
   */
  triInitial?: string
  sensInitial?: 'asc' | 'desc'
}) {
  const [tri, setTri] = useState<string>(triInitial ?? grille.tri_defaut)
  const [sens, setSens] = useState<'asc' | 'desc'>(sensInitial ?? grille.sens_defaut)
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
  // Une grille repliée laisse sa barre d'outils et son compteur visibles : on
  // sait ce qu'on a masqué, et on peut toujours exporter sans rouvrir.
  const { ouvert, basculer } = usePli(clePli ?? `grille.${cle}`, true)

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
        <BoutonPli ouvert={ouvert} basculer={basculer} libelle={titre ?? grille.libelle} />
        <span
          className="grille__titre"
          onClick={basculer}
          style={{ cursor: 'pointer' }}
        >
          {titre ?? grille.libelle}
        </span>
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

      {ouvert && (
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
                        onOuvrirPerimetre={onOuvrirPerimetre}
                      />
                    ))}
                  </tr>
                )
              })}
              {requete.data?.totaux && (
                <tr className="tableau__cale" aria-hidden="true">
                  <td colSpan={colonnes.length + 1} />
                </tr>
              )}
            </tbody>
            <PiedTotaux
              colonnes={colonnes}
              totaux={requete.data?.totaux}
              nbLignes={total}
            />
          </table>
        )}
      </div>
      )}

      {ouvert && (
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
      )}
    </div>
  )
}

/**
 * Pied de grille : totaux de la SÉLECTION ENTIÈRE.
 *
 * Un total de page induirait en erreur — additionner cinquante lignes sur dix
 * mille ne veut rien dire. Les totaux viennent donc du serveur, calculés sur
 * toute la sélection filtrée.
 *
 * Deux familles de colonnes n'ont pas de total : les identifiants et libellés
 * (rien à additionner) et les taux, dont la moyenne des lignes n'est pas la
 * valeur d'ensemble. Pour ces derniers, le serveur renvoie le taux recalculé
 * sur la sélection, jamais une moyenne de moyennes.
 */
function PiedTotaux({
  colonnes,
  totaux,
  nbLignes,
}: {
  colonnes: Colonne[]
  totaux: Record<string, number | null> | undefined
  nbLignes: number
}) {
  if (!totaux) return null
  const cumulable = (colonne: Colonne) =>
    ['euro', 'decimal', 'entier', 'pourcent'].includes(colonne.type) &&
    totaux[colonne.cle] !== undefined &&
    totaux[colonne.cle] !== null

  if (!colonnes.some(cumulable)) return null

  return (
    <tfoot className="tableau__pied">
      <tr>
        <td className="tableau__case" />
        {colonnes.map((colonne, index) => {
          if (!cumulable(colonne)) {
            // La première colonne non cumulable porte l'étiquette : sans elle,
            // la ligne de totaux flotterait sans dire ce qu'elle totalise.
            const etiquette = index === 0 || !colonnes.slice(0, index).some((c) => !cumulable(c))
            return (
              <td key={colonne.cle} className={classeAlignement(colonne)}>
                {etiquette ? (
                  <strong title={`Totaux calculés sur les ${nbLignes} lignes de la sélection, pas sur la page.`}>
                    Total ({nombre(nbLignes)} lignes)
                  </strong>
                ) : null}
              </td>
            )
          }
          const valeur = totaux[colonne.cle] as number
          return (
            <td
              key={colonne.cle}
              className={`${classeAlignement(colonne)}${valeur < 0 ? ' cellule--negatif' : ''}`}
            >
              <strong>{valeurCellule(valeur, colonne.type, colonne.decimales)}</strong>
            </td>
          )
        })}
      </tr>
    </tfoot>
  )
}

function Cellule({
  colonne,
  valeur,
  onOuvrirFiche,
  onOuvrirPerimetre,
}: {
  colonne: Colonne
  valeur: unknown
  onOuvrirFiche?: (genre: 'composant' | 'parent', id: string) => void
  onOuvrirPerimetre?: (perimetre: string) => void
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

  if (COLONNES_PERIMETRE.has(colonne.cle) && onOuvrirPerimetre && valeur) {
    return (
      <td className={classeAlignement(colonne)}>
        <CelluleCliquable
          texte={texte}
          aide={`Ouvrir la vue synthétique du périmètre « ${String(valeur)} »`}
          onActiver={() => onOuvrirPerimetre(String(valeur))}
        />
      </td>
    )
  }

  if (genreFiche && onOuvrirFiche && valeur) {
    return (
      <td className={classeAlignement(colonne)}>
        <CelluleCliquable
          texte={texte}
          aide={`Ouvrir la fiche ${genreFiche} ${String(valeur)}`}
          onActiver={() => onOuvrirFiche(genreFiche, String(valeur))}
        />
      </td>
    )
  }

  return (
    <td className={`${classeAlignement(colonne)}${negatif ? ' cellule--negatif' : ''}`} title={texte}>
      {texte}
    </td>
  )
}

/** Valeur de cellule activable au clic comme au clavier. */
function CelluleCliquable({
  texte,
  aide,
  onActiver,
}: {
  texte: string
  aide: string
  onActiver: () => void
}) {
  return (
    <span
      className="cellule--lien"
      role="button"
      tabIndex={0}
      title={aide}
      onClick={onActiver}
      onKeyDown={(evenement) => {
        if (evenement.key === 'Enter' || evenement.key === ' ') {
          evenement.preventDefault()
          onActiver()
        }
      }}
    >
      {texte}
    </span>
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
