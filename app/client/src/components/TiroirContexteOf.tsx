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
    () =>
      contexte.data ? construireFiltresLocaux(filtres, contexte.data, cellule) : null,
    [filtres, contexte.data, cellule],
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
                  : 'Les filtres globaux de sélection ne sont pas repris ici — seules les ' +
                    'tolérances le sont — pour qu’aucun ordre expliquant le chiffre ne soit masqué.'}
              </p>

              {donnees.ofs.length === 0 ? (
                <EtatVide message="Rien à instruire pour cette cellule." />
              ) : (
                <div className="pile">
                  {/* BLOC 1 — ce qui FAIT le chiffre.
                      Restreint à la semaine cliquée : le total de son pied de
                      page se somme exactement à l'écart affiché dans le bandeau.
                      C'est ce qui rend le tiroir vérifiable — on peut retrouver
                      le chiffre d'où l'on vient, ligne à ligne. */}
                  <section className="pile pile--serree">
                    <h3 className="tiroir__section">
                      1. Lignes du chiffre cliqué
                      <span className="attenue"> — S{numeroSemaine(cellule.semaine)}</span>
                    </h3>
                    <p className="attenue tiroir__amorce">
                      Les ordres qui ont mouvementé ce composant sur la semaine cliquée. Le
                      total de ce bloc est le chiffre affiché ci-dessus.
                    </p>
                    <GrilleDonnees
                      key={`${cleCellule(cellule)}§chiffre`}
                      cle="details_of"
                      grille={definitions.data.details_of}
                      // Le titre de la carte porte la PORTÉE, pas le propos :
                      // celui-ci est déjà dans l'intertitre juste au-dessus, et
                      // le répéter ferait deux titres pour un seul bloc. Replié,
                      // le bloc dit ainsi encore ce qu'il contient.
                      titre={`Semaine S${numeroSemaine(cellule.semaine)}`}
                      filtres={filtresLocaux.chiffre}
                      hauteurSquelette={4}
                      clePli="grille.contexte_of.chiffre"
                      triInitial="prod_id"
                      sensInitial="asc"
                    />
                  </section>

                  {/* BLOC 2 — ce qui l'ÉCLAIRE.
                      Les mêmes ordres, sur les autres semaines. Sa présence, ou
                      son absence, EST la réponse à la question posée : un écart
                      sans autre semaine est résiduel ; un écart de signe opposé
                      la semaine voisine, sur les mêmes ordres, est un décalage
                      de calage. */}
                  <section className="pile pile--serree">
                    <h3 className="tiroir__section">
                      2. Autres semaines des mêmes ordres
                      {autresSemaines(donnees, cellule).length > 0 && (
                        <span className="attenue">
                          {' — '}
                          {autresSemaines(donnees, cellule)
                            .map((semaine) => `S${numeroSemaine(semaine.semaine)}`)
                            .join(' · ')}
                        </span>
                      )}
                    </h3>
                    {filtresLocaux.autres === null ? (
                      <p className="attenue tiroir__amorce">
                        Ces ordres n'ont mouvementé ce composant sur aucune autre semaine :
                        l'écart ci-dessus n'a pas de contrepartie ailleurs, il est résiduel à
                        cette maille.
                      </p>
                    ) : (
                      <>
                        <p className="attenue tiroir__amorce">
                          Les mêmes ordres, sur les semaines voisines. Un écart de signe opposé
                          ici annule celui du bloc 1 : c'est un décalage de calage, non de la
                          matière manquante.
                        </p>
                        <GrilleDonnees
                          key={`${cleCellule(cellule)}§autres`}
                          cle="details_of"
                          grille={definitions.data.details_of}
                          titre={`Semaine${autresSemaines(donnees, cellule).length > 1 ? 's' : ''} ${autresSemaines(
                            donnees,
                            cellule,
                          )
                            .map((semaine) => `S${numeroSemaine(semaine.semaine)}`)
                            .join(' · ')}`}
                          filtres={filtresLocaux.autres}
                          hauteurSquelette={4}
                          clePli="grille.contexte_of.autres"
                          triInitial="prod_id"
                          sensInitial="asc"
                        />
                      </>
                    )}
                  </section>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}

/** Numéro de semaine sur deux chiffres, comme dans la vue synthétique. */
function numeroSemaine(numero: number): string {
  return String(numero).padStart(2, '0')
}

/** Identifiant stable d'une cellule — sert de clé de remontage aux grilles. */
function cleCellule(cellule: CelluleEcart): string {
  return `${cellule.perimetre}§${cellule.composant}§${cellule.annee}-${cellule.semaine}`
}

/** Les semaines du contexte autres que celle du clic. */
function autresSemaines(contexte: ContexteOf, cellule: CelluleEcart) {
  return contexte.semaines.filter(
    (semaine) => !(semaine.annee === cellule.annee && semaine.semaine === cellule.semaine),
  )
}

/**
 * Filtres du tiroir : une base commune, scindée en deux blocs disjoints.
 *
 * La base porte quatre critères additifs — le périmètre, le composant, les
 * ordres, et les semaines où ces ordres ont bougé.
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
 * seule question qu'on lui pose.
 *
 * LA SCISSION. Les deux blocs ne répondent pas à la même question — l'un montre
 * ce qui FAIT le chiffre, l'autre ce qui l'ÉCLAIRE — et ils doivent donc être
 * disjoints : une ligne comptée deux fois casserait la propriété qui rend le
 * tiroir vérifiable, à savoir que le total du bloc 1 est exactement le chiffre
 * cliqué. La séparation passe par une liste ÉNUMÉRÉE de semaines et non par des
 * bornes : la semaine du clic est le plus souvent au milieu de l'étendue, et un
 * intervalle continu ne sait pas l'exclure.
 *
 * `autres` vaut `null` quand il n'y a pas d'autre semaine — et non un filtre
 * vide, qui ne restreindrait rien et rejouerait tout le contexte dans le second
 * bloc.
 */
function construireFiltresLocaux(
  globaux: Filtres,
  contexte: ContexteOf,
  cellule: CelluleEcart,
): { chiffre: Filtres; autres: Filtres | null } {
  const base: Filtres = {
    // Les bornes restent posées sur l'étendue complète : elles bornent la
    // requête là où la liste énumérée la précise. L'une sans l'autre suffirait ;
    // les deux ensemble laissent le planificateur attaquer l'index de date.
    date_debut: contexte.date_debut,
    date_fin: contexte.date_fin,
    semaines_debut: [],
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

  const semaineCliquee = contexte.semaines.find(
    (semaine) => semaine.annee === cellule.annee && semaine.semaine === cellule.semaine,
  )
  const autres = autresSemaines(contexte, cellule)

  return {
    chiffre: {
      ...base,
      // Repli sur la borne basse si la semaine cliquée n'est pas dans la liste :
      // le cas ne se produit que si la cellule ne porte aucun mouvement, et le
      // bloc doit alors sortir vide, non montrer tout le contexte.
      semaines_debut: [semaineCliquee?.semaine_debut ?? contexte.date_debut ?? ''].filter(
        Boolean,
      ),
      date_debut: semaineCliquee?.semaine_debut ?? base.date_debut,
      date_fin: semaineCliquee?.semaine_debut ?? base.date_fin,
    },
    autres: autres.length
      ? { ...base, semaines_debut: autres.map((semaine) => semaine.semaine_debut) }
      : null,
  }
}
