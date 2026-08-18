/**
 * Tiroir de contexte : les ordres de fabrication derrière UN chiffre d'écart.
 *
 * Une cellule du bloc « écart de prélèvement » de la vue synthétique vaut un
 * périmètre, une référence composant et une semaine. La question qu'elle pose
 * est toujours la même, et le tableau croisé ne peut pas y répondre :
 *
 *   cet écart est-il RÉSIDUEL — de la matière réellement manquante ou en trop —
 *   ou n'est-il que le DÉCALAGE d'un ordre à cheval sur deux semaines, dont la
 *   contrepartie se trouve la semaine d'à côté ?
 *
 * Le tiroir apporte les deux éléments qui tranchent :
 *
 *   1. les ordres à l'origine des mouvements de cette semaine sur ce composant,
 *      côté consommation déclarée comme côté production déclarée ;
 *   2. TOUTES les semaines où ces mêmes ordres ont mouvementé ce composant, et
 *      pas seulement celle du clic. Sans ce débordement, la contrepartie reste
 *      invisible et tout écart passe pour résiduel.
 *
 * Les quatre critères sont additifs (périmètre ET composant ET ordres ET
 * semaines) et vivent ICI : ils ne touchent jamais l'objet de filtres global,
 * qu'on retrouve intact à la fermeture. C'est la raison d'être du tiroir —
 * ouvrir une parenthèse d'investigation sans perdre l'analyse en cours.
 */

import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { ContexteOf, Filtres } from '@/api/types'
import { euro, nombre } from '@/lib/format'
import { EtatErreur, EtatVide, Squelette } from './Etats'
import { GrilleDonnees } from './GrilleDonnees'

/** Coordonnées de la cellule cliquée. */
export interface CelluleEcart {
  perimetre: string
  composant: string
  annee: number
  semaine: number
}

export function TiroirContexteOf({
  cellule,
  filtres,
  enValeur,
  onFermer,
}: {
  cellule: CelluleEcart
  /** Filtres globaux — seuls les seuils en sont repris (voir `filtresLocaux`). */
  filtres: Filtres
  enValeur: boolean
  onFermer: () => void
}) {
  useEffect(() => {
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === 'Escape') onFermer()
    }
    document.addEventListener('keydown', surTouche)
    return () => document.removeEventListener('keydown', surTouche)
  }, [onFermer])

  const contexte = useQuery({
    queryKey: ['contexte-of', cellule],
    queryFn: () => api.contexteOf(cellule),
  })

  const definitions = useQuery({ queryKey: ['grilles'], queryFn: api.grilles })

  const filtresLocaux = useMemo(
    () => (contexte.data ? construireFiltresLocaux(filtres, contexte.data) : null),
    [filtres, contexte.data],
  )

  const donnees = contexte.data

  return (
    <>
      <div className="tiroir-voile" onClick={onFermer} aria-hidden="true" />
      <aside
        className="tiroir tiroir--large"
        role="dialog"
        aria-modal="true"
        aria-label={`Ordres de fabrication — ${cellule.composant}, semaine ${cellule.semaine}`}
      >
        <header className="tiroir__entete">
          <div>
            <div className="mono">{cellule.composant}</div>
            <div className="attenue">
              {donnees?.child_name ?? '…'} — périmètre « {cellule.perimetre} », S
              {String(cellule.semaine).padStart(2, '0')} {cellule.annee}
            </div>
          </div>
          <button
            type="button"
            className="bouton"
            style={{ marginLeft: 'auto' }}
            onClick={onFermer}
          >
            Fermer
          </button>
        </header>

        {donnees && (
          <dl className="tiroir__bandeau">
            <div className="tiroir__fait">
              <dt>Chiffre cliqué</dt>
              <dd>
                {enValeur
                  ? euro(Number(donnees.ecart_valorise ?? 0), 0)
                  : `${nombre(Number(donnees.ecart_equivalent_produit ?? 0), 1)} éq. produit`}
              </dd>
            </div>
            <div className="tiroir__fait">
              <dt>Écart brut</dt>
              <dd>
                {nombre(Number(donnees.ecart_brut ?? 0), 1)} {donnees.child_unite ?? ''}
              </dd>
            </div>
            <div className="tiroir__fait">
              <dt>Ordres concernés</dt>
              <dd>
                {donnees.ofs.length}
                {donnees.tronque ? ' (liste écrêtée)' : ''}
              </dd>
            </div>
            <div className="tiroir__fait">
              <dt>Semaines mouvementées</dt>
              <dd>
                {donnees.semaines.length === 0
                  ? '—'
                  : donnees.semaines
                      .map((semaine) => `S${String(semaine.semaine).padStart(2, '0')}`)
                      .join(' · ')}
              </dd>
            </div>
          </dl>
        )}

        <div className="tiroir__corps">
          {donnees?.tronque && (
            <div className="bandeau bandeau--attention" role="status">
              Cette semaine porte plus d'ordres que le tiroir n'en peut filtrer : la liste a
              été écrêtée et le détail ci-dessous est donc partiel. Passez par l'écran
              « Détail par OF » pour une lecture complète.
            </div>
          )}

          {contexte.isError && (
            <EtatErreur erreur={contexte.error} onReessayer={() => void contexte.refetch()} />
          )}
          {definitions.isError && (
            <EtatErreur
              erreur={definitions.error}
              onReessayer={() => void definitions.refetch()}
            />
          )}

          {(contexte.isPending || definitions.isPending) && <Squelette hauteur={320} />}

          {donnees && definitions.data && filtresLocaux && (
            <>
              <p className="attenue" style={{ marginTop: 0, maxWidth: '95ch' }}>
                {donnees.ofs.length === 0
                  ? "Aucun ordre de fabrication n'a mouvementé ce composant cette semaine-là sur ce périmètre."
                  : "Toutes les lignes des ordres ci-dessous, sur toutes les semaines où ils ont mouvementé ce composant. " +
                    "Une non-consommation d'une semaine annulée par une surconsommation de la précédente, sur les mêmes " +
                    'ordres, est un décalage de calage et non de la matière manquante. Les filtres globaux de sélection ' +
                    'ne sont pas repris ici — seules les tolérances le sont — pour qu’aucun ordre expliquant le ' +
                    'chiffre ne soit masqué.'}
              </p>

              {donnees.ofs.length === 0 ? (
                <EtatVide message="Rien à instruire pour cette cellule." />
              ) : (
                <GrilleDonnees
                  // La clé lie l'instance à la cellule : deux ouvertures
                  // successives sur deux cellules différentes doivent repartir
                  // d'une pagination et d'une sélection neuves.
                  key={`${cellule.perimetre}§${cellule.composant}§${cellule.annee}-${cellule.semaine}`}
                  cle="details_of"
                  grille={definitions.data.details_of}
                  filtres={filtresLocaux}
                  hauteurSquelette={8}
                  // État de pliage propre au tiroir : replier la grille de
                  // l'écran Détail ne doit pas replier celle-ci, et l'inverse.
                  clePli="grille.contexte_of"
                  // Par ordre, et non par impact. Le tri par impact est celui
                  // qu'on veut pour CHERCHER une anomalie ; ici on en SUIT une,
                  // et la lecture consiste à comparer les semaines d'un même
                  // ordre. Classées par impact, elles se retrouvent dispersées
                  // dans la page et la compensation devient invisible — ce que
                  // le tiroir est précisément là pour montrer.
                  triInitial="prod_id"
                  sensInitial="asc"
                />
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}

/**
 * Filtres du tiroir : quatre critères additifs, et rien d'autre.
 *
 * Ce qui est REPRIS des filtres globaux : les deux tolérances. Elles ne
 * sélectionnent aucune ligne, elles décident seulement de l'étiquette
 * « Conforme » ; les emporter garde la lecture cohérente avec l'écran d'où l'on
 * vient.
 *
 * Ce qui ne l'est PAS : tout le reste — type d'écart, statut de ligne,
 * catégorie, recherche, impact minimum, exclusion des conformes, coefficient
 * uniforme, et les dates. Chacun de ces critères pourrait retirer une ligne
 * d'un ordre qui explique le chiffre, et le tiroir répondrait alors faux à la
 * seule question qu'on lui pose. Les dates, en particulier, sont REMPLACÉES par
 * l'étendue des semaines où les ordres ont bougé : c'est tout l'intérêt du
 * dispositif de sortir de la semaine cliquée.
 */
function construireFiltresLocaux(globaux: Filtres, contexte: ContexteOf): Filtres {
  return {
    date_debut: contexte.date_debut,
    date_fin: contexte.date_fin,
    programmes: [],
    perimetres: [contexte.perimetre],
    categories: [],
    types_ecart: [],
    statuts_ligne: [],
    parents: [],
    composants: [contexte.composant],
    ofs: contexte.ofs,
    statuts_of: [],
    recherche: null,
    seuil_conformite: globaux.seuil_conformite,
    seuil_pct: globaux.seuil_pct,
    impact_min: null,
    coef_uniforme_uniquement: false,
    exclure_conforme: false,
  }
}
