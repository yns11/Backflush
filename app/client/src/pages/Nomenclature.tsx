/**
 * Écran « Nomenclature » — désactiver ou corriger des lignes de nomenclature.
 *
 * Une nomenclature fausse produit un écart entièrement fictif : le composant
 * n'a pas été sur-consommé, c'est le coefficient attendu qui est faux. Ces cas
 * sont fréquents (nomenclature obsolète, substitution non tracée, ligne
 * fantôme) et polluent le classement des références jusqu'à le rendre inutile.
 *
 * Deux corrections, de portées différentes :
 *
 * • **Désactiver** la ligne — le couple parent/composant sort du calcul.
 *   À réserver aux lignes qui n'auraient jamais dû exister.
 * • **Corriger le coefficient** — la consommation théorique est recalculée avec
 *   la valeur saisie, et l'écart avec elle. C'est la correction à privilégier :
 *   elle conserve la ligne dans l'analyse, avec la bonne référence.
 *
 * Le coefficient d'origine reste affiché à côté du coefficient effectif. Une
 * correction qu'on ne peut plus comparer à la valeur de l'ERP n'est plus
 * vérifiable — et deviendrait, à terme, une seconde source de vérité invisible.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { LigneNomenclatureParam } from '@/api/types'
import { TableParametrage, type ColonneParametrage } from '@/components/TableParametrage'
import { nombre } from '@/lib/format'

type Etat = 'tous' | 'surchargees' | 'desactivees'

/** Couple identifiant une ligne de nomenclature. */
const couple = (ligne: LigneNomenclatureParam) => ({
  parent_itemid: ligne.parent_itemid,
  child_itemid: ligne.child_itemid,
})

export function Nomenclature() {
  const [recherche, setRecherche] = useState('')
  const [etat, setEtat] = useState<Etat>('tous')
  const [tri, setTri] = useState('parent_itemid')
  const [sens, setSens] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState(1)
  const [taille, setTaille] = useState(50)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  const [edition, setEdition] = useState<LigneNomenclatureParam[] | null>(null)

  const client = useQueryClient()
  const requete = useQuery({
    queryKey: ['nomenclature', { recherche, etat, tri, sens, page, taille }],
    queryFn: () => api.nomenclature({ recherche, etat, tri, sens, page, taille }),
    placeholderData: (precedent) => precedent,
  })

  const apresAction = (texte: string) => {
    setSelection(new Set())
    setEdition(null)
    setMessage(texte)
    // Une surcharge change les écarts : tout l'écran d'analyse est à revoir.
    void client.invalidateQueries()
  }

  const surcharge = useMutation({
    mutationFn: api.surchargerNomenclature,
    onSuccess: (resultat) => apresAction(`${resultat.lignes} ligne(s) mise(s) à jour.`),
    onError: (erreur: unknown) =>
      setMessage(erreur instanceof Error ? erreur.message : "L'action a échoué."),
  })

  const reinitialisation = useMutation({
    mutationFn: api.reinitialiserNomenclature,
    onSuccess: (resultat) =>
      apresAction(`${resultat.lignes} ligne(s) rétablie(s) à la valeur de l'ERP.`),
    onError: (erreur: unknown) =>
      setMessage(erreur instanceof Error ? erreur.message : "L'action a échoué."),
  })

  const colonnes: Array<ColonneParametrage<LigneNomenclatureParam>> = [
    {
      cle: 'parent_itemid',
      libelle: 'Parent',
      tri: 'parent_itemid',
      rendu: (ligne) => (
        <span className={ligne.active ? 'mono' : 'mono attenue'}>{ligne.parent_itemid}</span>
      ),
    },
    {
      cle: 'parent_name',
      libelle: 'Désignation parent',
      tri: 'parent_name',
      rendu: (ligne) => <span title={ligne.parent_name ?? ''}>{ligne.parent_name ?? '—'}</span>,
    },
    { cle: 'perimetre', libelle: 'Périmètre', tri: 'perimetre', rendu: (l) => l.perimetre ?? '—' },
    {
      cle: 'child_itemid',
      libelle: 'Composant',
      tri: 'child_itemid',
      rendu: (ligne) => (
        <span className={ligne.active ? 'mono' : 'mono attenue'}>{ligne.child_itemid}</span>
      ),
    },
    {
      cle: 'child_name',
      libelle: 'Désignation composant',
      tri: 'child_name',
      rendu: (ligne) => <span title={ligne.child_name ?? ''}>{ligne.child_name ?? '—'}</span>,
    },
    {
      cle: 'coef_origine',
      libelle: 'Coef ERP',
      aide: "Coefficient de la nomenclature source. Jamais modifié par l'application.",
      tri: 'coef_origine',
      alignement: 'droite',
      rendu: (ligne) => nombre(Number(ligne.coef_origine ?? 0), 4),
    },
    {
      cle: 'coef_effectif',
      libelle: 'Coef retenu',
      aide: 'Coefficient utilisé pour le calcul de la consommation théorique.',
      tri: 'coef_effectif',
      alignement: 'droite',
      rendu: (ligne) =>
        ligne.coef_surcharge ? (
          <strong style={{ color: 'var(--serie-2)' }} title="Coefficient corrigé">
            {nombre(Number(ligne.coef_effectif ?? 0), 4)}
          </strong>
        ) : (
          nombre(Number(ligne.coef_effectif ?? 0), 4)
        ),
    },
    {
      cle: 'active',
      libelle: 'État',
      tri: 'active',
      // Sommer des coefficients n'aurait aucun sens ; ce qui se totalise ici,
      // c'est le nombre d'arbitrages en vigueur sur la page.
      total: (lignes) => {
        const corrigees = lignes.filter((ligne) => ligne.coef_surcharge).length
        const desactivees = lignes.filter((ligne) => !ligne.active).length
        if (corrigees === 0 && desactivees === 0) return 'aucune correction'
        return [
          corrigees > 0 ? `${corrigees} corrigée(s)` : null,
          desactivees > 0 ? `${desactivees} désactivée(s)` : null,
        ]
          .filter(Boolean)
          .join(', ')
      },
      rendu: (ligne) =>
        !ligne.active ? (
          <span className="pastille" style={{ background: 'var(--survol)' }}>
            <span className="pastille__point" style={{ background: 'var(--statut-critique)' }} />
            Désactivée
          </span>
        ) : ligne.coef_surcharge ? (
          <span className="pastille" style={{ background: 'var(--survol)' }}>
            <span className="pastille__point" style={{ background: 'var(--statut-attention)' }} />
            Corrigée
          </span>
        ) : (
          <span className="attenue">ERP</span>
        ),
    },
    {
      cle: 'motif',
      libelle: 'Motif / auteur',
      rendu: (ligne) =>
        ligne.motif || ligne.modifie_par ? (
          <span className="attenue" title={`${ligne.motif ?? ''} — ${ligne.modifie_par ?? ''}`}>
            {ligne.motif || <em>sans motif</em>}
            {ligne.modifie_par ? ` · ${ligne.modifie_par}` : ''}
          </span>
        ) : (
          ''
        ),
    },
  ]

  const occupe = surcharge.isPending || reinitialisation.isPending

  return (
    <>
      <TableParametrage
        titre="Nomenclature"
        description="Nomenclature active de l'ERP, avec les corrections appliquées par le key-user."
        colonnes={colonnes}
        page={requete.data}
        chargement={requete.isPending}
        erreur={requete.isError ? requete.error : null}
        onReessayer={() => void requete.refetch()}
        cleLigne={(ligne) => `${ligne.parent_itemid}§${ligne.child_itemid}`}
        selection={selection}
        onSelection={setSelection}
        tri={tri}
        sens={sens}
        onTri={(nouveauTri, nouveauSens) => {
          setTri(nouveauTri)
          setSens(nouveauSens)
          setPage(1)
        }}
        onPage={setPage}
        onTaille={(valeur) => {
          setTaille(valeur)
          setPage(1)
        }}
        message={message}
        actions={[
          {
            libelle: 'Modifier…',
            aide: 'Désactiver, réactiver ou corriger le coefficient des lignes visées.',
            principale: true,
            executer: (lignes) => setEdition(lignes),
          },
          {
            libelle: "Rétablir l'ERP",
            aide: "Lève la correction : la ligne reprend le coefficient et l'état de la nomenclature source.",
            executer: (lignes) => {
              const cibles = lignes.filter((ligne) => ligne.coef_surcharge || !ligne.active)
              if (cibles.length === 0) return
              reinitialisation.mutate({ lignes: cibles.map(couple) })
            },
            indisponible: (lignes) =>
              lignes.some((ligne) => ligne.coef_surcharge || !ligne.active)
                ? null
                : 'Aucune correction à lever.',
          },
        ]}
        actionsLigne={(ligne) => (
          <button
            type="button"
            className="bouton bouton--discret"
            disabled={occupe}
            onClick={() => setEdition([ligne])}
          >
            Modifier
          </button>
        )}
        barre={
          <div className="rang" style={{ gap: 'var(--espace-2)' }}>
            <input
              className="champ"
              type="search"
              placeholder="Parent, composant, désignation…"
              value={recherche}
              onChange={(evenement) => {
                setRecherche(evenement.target.value)
                setPage(1)
              }}
              style={{ minWidth: 220 }}
            />
            <select
              className="champ"
              value={etat}
              onChange={(evenement) => {
                setEtat(evenement.target.value as Etat)
                setPage(1)
              }}
              aria-label="État des lignes"
            >
              <option value="tous">Toutes</option>
              <option value="surchargees">Corrigées seulement</option>
              <option value="desactivees">Désactivées seulement</option>
            </select>
          </div>
        }
      />

      {edition && (
        <DialogueSurcharge
          lignes={edition}
          occupe={occupe}
          onAnnuler={() => setEdition(null)}
          onValider={(active, coef, motif) =>
            surcharge.mutate({
              lignes: edition.map(couple),
              active,
              coef_bom: coef,
              motif,
            })
          }
        />
      )}
    </>
  )
}

/**
 * Boîte de dialogue de correction.
 *
 * Trois actions plutôt qu'un formulaire libre : « désactiver » et « corriger le
 * coefficient » n'ont ni les mêmes champs ni les mêmes conséquences, et les
 * présenter ensemble laisserait croire qu'on peut faire les deux — alors qu'une
 * ligne désactivée n'utilise plus aucun coefficient.
 */
function DialogueSurcharge({
  lignes,
  occupe,
  onAnnuler,
  onValider,
}: {
  lignes: LigneNomenclatureParam[]
  occupe: boolean
  onAnnuler: () => void
  onValider: (active: boolean, coef: number | null, motif: string | null) => void
}) {
  const premiere = lignes[0]
  const [action, setAction] = useState<'coefficient' | 'desactiver' | 'reactiver'>(
    premiere && !premiere.active ? 'reactiver' : 'coefficient',
  )
  const [coef, setCoef] = useState<string>(
    lignes.length === 1 && premiere ? String(premiere.coef_effectif ?? '') : '',
  )
  const [motif, setMotif] = useState('')

  const valeur = Number(coef.replace(',', '.'))
  const coefValide = action !== 'coefficient' || (coef.trim() !== '' && valeur > 0)

  return (
    <>
      <div className="tiroir-voile" onClick={onAnnuler} aria-hidden="true" />
      <div className="dialogue" role="dialog" aria-modal="true" aria-label="Modifier la nomenclature">
        <h2 className="carte__titre" style={{ marginBottom: 4 }}>
          Modifier {lignes.length} ligne{lignes.length > 1 ? 's' : ''} de nomenclature
        </h2>
        <p className="attenue" style={{ margin: '0 0 var(--espace-3)', fontSize: 11.5 }}>
          {lignes.length === 1 && premiere
            ? `${premiere.parent_itemid} → ${premiere.child_itemid} (coef ERP ${nombre(Number(premiere.coef_origine ?? 0), 4)})`
            : "Le même arbitrage sera appliqué à toutes les lignes sélectionnées."}
        </p>

        <fieldset style={{ border: 0, padding: 0, margin: '0 0 var(--espace-3)' }}>
          {(
            [
              ['coefficient', 'Corriger le coefficient', 'La consommation théorique et l’écart sont recalculés avec cette valeur.'],
              ['desactiver', 'Désactiver la ligne', "Le couple parent / composant sort du calcul d'écart."],
              ['reactiver', "Réactiver la ligne", 'La ligne réintègre le calcul, avec son coefficient ERP.'],
            ] as const
          ).map(([cle, libelle, aide]) => (
            <label key={cle} className="multi__option" title={aide}>
              <input
                type="radio"
                name="action"
                checked={action === cle}
                onChange={() => setAction(cle)}
              />
              <span className="multi__valeur">
                {libelle}
                <span className="attenue" style={{ display: 'block', fontSize: 11 }}>
                  {aide}
                </span>
              </span>
            </label>
          ))}
        </fieldset>

        {action === 'coefficient' && (
          <label className="filtres__groupe" style={{ marginBottom: 'var(--espace-3)' }}>
            <span className="etiquette">Coefficient retenu</span>
            <input
              className="champ"
              type="text"
              inputMode="decimal"
              value={coef}
              autoFocus
              onChange={(evenement) => setCoef(evenement.target.value)}
              placeholder="ex. 4"
            />
            {!coefValide && (
              <span className="attenue" style={{ fontSize: 11, color: 'var(--statut-critique)' }}>
                Un coefficient doit être strictement positif. À zéro, la ligne n'a plus de
                théorique sans pour autant sortir de l'analyse : désactivez-la plutôt.
              </span>
            )}
          </label>
        )}

        <label className="filtres__groupe" style={{ marginBottom: 'var(--espace-4)' }}>
          <span className="etiquette">Motif</span>
          <input
            className="champ"
            type="text"
            maxLength={300}
            value={motif}
            onChange={(evenement) => setMotif(evenement.target.value)}
            placeholder="ex. nomenclature obsolète depuis l'indice C"
          />
        </label>

        <div className="rang" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="bouton" onClick={onAnnuler}>
            Annuler
          </button>
          <button
            type="button"
            className="bouton bouton--principal"
            disabled={occupe || !coefValide}
            onClick={() =>
              onValider(
                action !== 'desactiver',
                action === 'coefficient' ? valeur : null,
                motif.trim() || null,
              )
            }
          >
            Appliquer
          </button>
        </div>
      </div>
    </>
  )
}
