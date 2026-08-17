/**
 * Vue synthétique d'un périmètre — restitution en tableau croisé.
 *
 * Reprend la forme du rapport de pilotage d'atelier : une ligne par référence,
 * une colonne par semaine. Deux blocs :
 *
 *   1. PRODUCTION — une ligne par parent fabriqué (réf, désignation, BOM), puis
 *      la somme du périmètre ;
 *   2. ÉCART DE PRÉLÈVEMENT — une ligne par composant à coefficient uniforme,
 *      l'écart étant exprimé en équivalent produit fabriqué.
 *
 * Deux partis pris d'affichage, hérités de la lecture d'atelier :
 *
 * • Les zéros sont laissés vides. Sur une grille de trente références × treize
 *   semaines, une majorité de zéros noie les quelques cellules qui portent
 *   l'information. L'export Excel, lui, contient bien des zéros : un tableau
 *   destiné au calcul ne doit pas obliger à traiter les cases vides.
 * • Un seul périmètre à la fois. L'écart en équivalent produit se rapporte au
 *   volume produit d'UNE ligne de production ; le cumuler sur deux lignes
 *   reviendrait à additionner des unités différentes.
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api, telechargerSynthese } from '@/api/client'
import type {
  LigneEcartSynthese,
  LigneProductionSynthese,
  SemaineCalendrier,
} from '@/api/types'
import { EtatErreur, EtatVide, Squelette } from '@/components/Etats'
import { euro, nombre } from '@/lib/format'
import { useFiltres } from '@/state/filtres'
import { useMesure } from '@/state/mesure'

/** Colonne de semaine : clé de tri stable et libellé court (S24). */
interface Semaine {
  cle: string
  libelle: string
}

export function PerimetreSynthetique() {
  const { filtres, modifier } = useFiltres()
  const { mesure, enValeur } = useMesure()
  const [export_, setExport] = useState<{ occupe: boolean; erreur: string | null }>({
    occupe: false,
    erreur: null,
  })
  const perimetre = filtres.perimetres.length === 1 ? filtres.perimetres[0] : null

  const requete = useQuery({
    queryKey: ['synthese-perimetre', filtres, mesure],
    queryFn: () => api.synthesePerimetre(filtres, mesure),
    enabled: perimetre !== null,
  })

  const modele = useMemo(() => {
    if (!requete.data) return null
    return construireModele(
      requete.data.production,
      requete.data.ecarts,
      enValeur,
      requete.data.semaines,
    )
  }, [requete.data, enValeur])

  if (!perimetre) {
    return (
      <div className="carte">
        <div className="carte__corps">
          <EtatVide message="Sélectionnez un périmètre unique pour afficher la vue synthétique." />
          <p className="attenue" style={{ textAlign: 'center' }}>
            L'écart en équivalent produit se rapporte au volume produit d'une ligne de
            production : le cumuler sur plusieurs périmètres additionnerait des unités
            différentes.
          </p>
        </div>
      </div>
    )
  }

  if (requete.isError) {
    return <EtatErreur erreur={requete.error} onReessayer={() => void requete.refetch()} />
  }
  if (requete.isPending || !modele) return <Squelette hauteur={420} />
  if (modele.semaines.length === 0) {
    return <EtatVide message="Aucune production sur ce périmètre pour la période retenue." />
  }

  const unite = enValeur ? '€' : 'unités'
  const formater = (valeur: number) => (enValeur ? euro(valeur, 0) : nombre(valeur, 0))

  return (
    <div className="pile">
      <div className="carte">
        <div className="carte__entete">
          <div>
            <h2 className="carte__titre" style={{ color: 'var(--pole-surconso)' }}>
              Écart : {nombre(modele.partEcartPct, 2)} % du volume produit
            </h2>
            <p className="attenue" style={{ margin: 0 }}>
              Périmètre « {perimetre} » — {modele.semaines.length} semaine(s), mesure en {unite}.
              L'écart rapporté est la somme des |écarts| des composants à coefficient
              uniforme, en équivalent produit.
            </p>
          </div>
          <div className="rang" style={{ marginLeft: 'auto', gap: 8 }}>
            <button
              type="button"
              className="bouton bouton--principal"
              disabled={export_.occupe}
              title="Le classeur contient les mêmes chiffres, avec des zéros explicites pour rester directement calculable."
              onClick={() => {
                setExport({ occupe: true, erreur: null })
                telechargerSynthese({ filtres, mesure })
                  .then(() => setExport({ occupe: false, erreur: null }))
                  .catch((erreur: unknown) =>
                    setExport({
                      occupe: false,
                      erreur: erreur instanceof Error ? erreur.message : "L'export a échoué.",
                    }),
                  )
              }}
            >
              {export_.occupe ? 'Export…' : 'Exporter (Excel)'}
            </button>
            <button type="button" className="bouton" onClick={() => modifier({ perimetres: [] })}>
              Changer de périmètre
            </button>
          </div>
        </div>

        {export_.erreur && (
          <div className="bandeau bandeau--critique" role="alert">
            {export_.erreur}
          </div>
        )}

        <div className="carte__corps" style={{ overflowX: 'auto' }}>
          <table className="tableau tableau--croise">
            <thead>
              <tr>
                <th className="non-triable">Référence</th>
                <th className="non-triable">Désignation</th>
                <th className="non-triable">BOM / Coef</th>
                {modele.semaines.map((semaine) => (
                  <th key={semaine.cle} className="non-triable droite">
                    {semaine.libelle}
                  </th>
                ))}
                <th className="non-triable droite">Total</th>
              </tr>
            </thead>

            <tbody>
              <tr className="tableau__section">
                <td colSpan={4 + modele.semaines.length}>1. PRODUCTION</td>
              </tr>
              {modele.production.map((ligne) => (
                <LigneCroisee
                  key={ligne.cle}
                  ligne={ligne}
                  semaines={modele.semaines}
                  formater={formater}
                />
              ))}
              <tr className="tableau__total">
                <td colSpan={3}>
                  <strong>Total production</strong>
                </td>
                {modele.semaines.map((semaine) => (
                  <td key={semaine.cle} className="droite">
                    <strong>{cellule(modele.totalProduction[semaine.cle], formater)}</strong>
                  </td>
                ))}
                <td className="droite">
                  <strong>{formater(modele.totalProductionPeriode)}</strong>
                </td>
              </tr>

              <tr className="tableau__section">
                <td colSpan={4 + modele.semaines.length}>2. ÉCART DE PRÉLÈVEMENT</td>
              </tr>
              {modele.ecarts.length === 0 ? (
                <tr>
                  <td colSpan={4 + modele.semaines.length} className="attenue">
                    Aucun composant à coefficient uniforme sur ce périmètre : l'écart en
                    équivalent produit n'y est pas calculable.
                  </td>
                </tr>
              ) : (
                modele.ecarts.map((ligne) => (
                  <LigneCroisee
                    key={ligne.cle}
                    ligne={ligne}
                    semaines={modele.semaines}
                    formater={formater}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

interface LigneModele {
  cle: string
  reference: string
  designation: string
  /** Identifiant de nomenclature (production) ou coefficient (écart). */
  complement: string
  valeurs: Record<string, number>
  total: number
}

function LigneCroisee({
  ligne,
  semaines,
  formater,
}: {
  ligne: LigneModele
  semaines: Semaine[]
  formater: (valeur: number) => string
}) {
  return (
    <tr>
      <td className="mono">{ligne.reference}</td>
      <td title={ligne.designation}>{ligne.designation}</td>
      <td className="mono attenue">{ligne.complement}</td>
      {semaines.map((semaine) => {
        const valeur = ligne.valeurs[semaine.cle]
        return (
          <td
            key={semaine.cle}
            className={`droite${valeur !== undefined && valeur < 0 ? ' cellule--negatif' : ''}`}
          >
            {cellule(valeur, formater)}
          </td>
        )
      })}
      <td className={`droite${ligne.total < 0 ? ' cellule--negatif' : ''}`}>
        <strong>{cellule(ligne.total, formater)}</strong>
      </td>
    </tr>
  )
}

/** Une cellule nulle ou absente reste vide : c'est ce qui rend la grille lisible. */
function cellule(valeur: number | undefined, formater: (valeur: number) => string): string {
  if (valeur === undefined || Math.abs(valeur) < 1e-9) return ''
  return formater(valeur)
}

function libelleSemaine(annee: number, semaine: number): Semaine {
  return {
    cle: `${annee}-${String(semaine).padStart(2, '0')}`,
    libelle: `S${String(semaine).padStart(2, '0')}`,
  }
}

function construireModele(
  production: LigneProductionSynthese[],
  ecarts: LigneEcartSynthese[],
  enValeur: boolean,
  calendrier: SemaineCalendrier[],
) {
  const semaines = new Map<string, Semaine>()
  const ajouterSemaine = (annee: number, numero: number) => {
    const semaine = libelleSemaine(annee, numero)
    semaines.set(semaine.cle, semaine)
    return semaine.cle
  }
  // Les colonnes viennent du calendrier de la période, pas des seules lignes
  // rapportées : une semaine d'arrêt de ligne doit apparaître, vide. Un axe qui
  // saute de S25 à S27 se lit comme une suite continue et efface l'arrêt.
  for (const semaine of calendrier) ajouterSemaine(semaine.annee, semaine.semaine)

  const parents = new Map<string, LigneModele>()
  const totalProduction: Record<string, number> = {}
  for (const ligne of production) {
    const cle = ajouterSemaine(ligne.annee, ligne.semaine)
    const valeur = Number(enValeur ? ligne.valeur_produite : ligne.qty_produite) || 0
    const entree = parents.get(ligne.parent_itemid) ?? {
      cle: ligne.parent_itemid,
      reference: ligne.parent_itemid,
      designation: ligne.parent_name ?? '—',
      complement: ligne.bomid ?? '—',
      valeurs: {},
      total: 0,
    }
    entree.valeurs[cle] = (entree.valeurs[cle] ?? 0) + valeur
    entree.total += valeur
    parents.set(ligne.parent_itemid, entree)
    totalProduction[cle] = (totalProduction[cle] ?? 0) + valeur
  }

  const composants = new Map<string, LigneModele>()
  for (const ligne of ecarts) {
    const cle = ajouterSemaine(ligne.annee, ligne.semaine)
    // En quantité, l'écart est exprimé en équivalent produit fabriqué — c'est
    // la seule grandeur comparable au volume produit affiché au-dessus.
    const valeur =
      Number(enValeur ? ligne.ecart_valorise : ligne.ecart_equivalent_produit) || 0
    const entree = composants.get(ligne.child_itemid) ?? {
      cle: ligne.child_itemid,
      reference: ligne.child_itemid,
      designation: ligne.child_name ?? '—',
      complement: ligne.coef_bom !== null ? nombre(Number(ligne.coef_bom), 4) : '—',
      valeurs: {},
      total: 0,
    }
    entree.valeurs[cle] = (entree.valeurs[cle] ?? 0) + valeur
    entree.total += valeur
    composants.set(ligne.child_itemid, entree)
  }

  const ordonnees = [...semaines.values()].sort((a, b) => a.cle.localeCompare(b.cle))
  const totalProductionPeriode = Object.values(totalProduction).reduce((s, v) => s + v, 0)
  const totalEcartAbsolu = [...composants.values()].reduce(
    (somme, ligne) => somme + Math.abs(ligne.total),
    0,
  )

  return {
    semaines: ordonnees,
    production: [...parents.values()].sort((a, b) => b.total - a.total),
    ecarts: [...composants.values()].sort((a, b) => Math.abs(b.total) - Math.abs(a.total)),
    totalProduction,
    totalProductionPeriode,
    partEcartPct: totalProductionPeriode > 0 ? (totalEcartAbsolu / totalProductionPeriode) * 100 : 0,
  }
}
