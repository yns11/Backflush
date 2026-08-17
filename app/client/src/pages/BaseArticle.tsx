/**
 * Écran « Base article » — exclure des références de l'analyse.
 *
 * Exclure une référence est un acte lourd : elle disparaît de tous les
 * indicateurs, de tous les graphiques et de tous les exports, immédiatement.
 * L'écran est donc construit pour rendre ce geste conscient plutôt que
 * commode :
 *
 * • l'impact porté par la référence et son nombre de semaines en écart sont
 *   affichés en face du bouton — on voit ce qu'on s'apprête à retirer ;
 * • un motif est demandé. Il n'est pas obligatoire techniquement, mais
 *   l'invite est systématique : dans six mois, une exclusion sans
 *   justification sera une anomalie que personne n'osera lever ;
 * • l'action est réversible d'un clic, et l'auteur est tracé.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { LigneArticle } from '@/api/types'
import { TableParametrage, type ColonneParametrage } from '@/components/TableParametrage'
import { euro, nombre } from '@/lib/format'

type Etat = 'tous' | 'exclus' | 'inclus'

export function BaseArticle() {
  const [recherche, setRecherche] = useState('')
  const [etat, setEtat] = useState<Etat>('tous')
  const [tri, setTri] = useState('impact_absolu')
  const [sens, setSens] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [taille, setTaille] = useState(50)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)

  const client = useQueryClient()
  const requete = useQuery({
    queryKey: ['articles', { recherche, etat, tri, sens, page, taille }],
    queryFn: () => api.articles({ recherche, etat, tri, sens, page, taille }),
    placeholderData: (precedent) => precedent,
  })

  const mutation = useMutation({
    mutationFn: api.basculerExclusion,
    onSuccess: (resultat) => {
      setSelection(new Set())
      setMessage(
        resultat.exclu
          ? `${resultat.lignes} référence(s) exclue(s) de l'analyse.`
          : `${resultat.lignes} référence(s) réintégrée(s).`,
      )
      // Le paramétrage change les CHIFFRES : invalider la seule liste laisserait
      // les indicateurs et les graphiques sur des valeurs périmées.
      void client.invalidateQueries()
    },
    onError: (erreur: unknown) =>
      setMessage(erreur instanceof Error ? erreur.message : "L'action a échoué."),
  })

  const appliquer = (lignes: LigneArticle[], exclu: boolean) => {
    const cibles = lignes.filter((ligne) => ligne.exclu !== exclu)
    if (cibles.length === 0) {
      setMessage(
        exclu
          ? 'Toutes les références visées sont déjà exclues.'
          : 'Aucune des références visées n\'est exclue.',
      )
      return
    }
    const motif = exclu
      ? window.prompt(
          `Motif de l'exclusion de ${cibles.length} référence(s) — il restera attaché à la décision :`,
          '',
        )
      : null
    // Annuler l'invite annule l'action : une exclusion en masse ne doit pas
    // partir sur un Échap distrait.
    if (exclu && motif === null) return
    mutation.mutate({ item_ids: cibles.map((ligne) => ligne.item_id), exclu, motif })
  }

  const colonnes: Array<ColonneParametrage<LigneArticle>> = [
    {
      cle: 'item_id',
      libelle: 'Référence',
      tri: 'item_id',
      rendu: (ligne) => (
        <span className={ligne.exclu ? 'mono attenue' : 'mono'}>{ligne.item_id}</span>
      ),
    },
    {
      cle: 'item_name',
      libelle: 'Désignation',
      tri: 'item_name',
      rendu: (ligne) => <span title={ligne.item_name ?? ''}>{ligne.item_name ?? '—'}</span>,
    },
    { cle: 'categorie', libelle: 'Catégorie', tri: 'categorie', rendu: (l) => l.categorie ?? '—' },
    { cle: 'programme', libelle: 'Programme', tri: 'programme', rendu: (l) => l.programme ?? '—' },
    { cle: 'perimetre', libelle: 'Périmètre', tri: 'perimetre', rendu: (l) => l.perimetre ?? '—' },
    {
      cle: 'std_cost_price',
      libelle: 'Coût std',
      tri: 'std_cost_price',
      alignement: 'droite',
      rendu: (ligne) =>
        ligne.std_cost_price === null ? (
          <span title="Coût standard absent : l'impact financier de cette référence est compté pour 0 €.">
            —
          </span>
        ) : (
          euro(ligne.std_cost_price, 4)
        ),
    },
    {
      cle: 'impact_absolu',
      libelle: 'Impact absolu',
      aide: "Somme des |écarts valorisés| portés par la référence, tous programmes confondus. C'est ce qui disparaît de l'analyse en cas d'exclusion.",
      tri: 'impact_absolu',
      alignement: 'droite',
      rendu: (ligne) => euro(ligne.impact_absolu, 0, true),
      total: (lignes) =>
        euro(lignes.reduce((somme, ligne) => somme + Number(ligne.impact_absolu), 0), 0, true),
    },
    {
      cle: 'nb_semaines_en_ecart',
      libelle: 'Sem. en écart',
      tri: 'nb_semaines_en_ecart',
      alignement: 'droite',
      rendu: (ligne) => nombre(ligne.nb_semaines_en_ecart),
      total: (lignes) =>
        nombre(lignes.reduce((somme, ligne) => somme + Number(ligne.nb_semaines_en_ecart), 0)),
    },
    {
      cle: 'exclu',
      libelle: 'État',
      tri: 'exclu',
      rendu: (ligne) =>
        ligne.exclu ? (
          <span className="pastille" style={{ background: 'var(--survol)' }}>
            <span className="pastille__point" style={{ background: 'var(--statut-critique)' }} />
            Exclue
          </span>
        ) : (
          <span className="attenue">Dans l'analyse</span>
        ),
    },
    {
      cle: 'motif',
      libelle: 'Motif / auteur',
      rendu: (ligne) =>
        ligne.exclu ? (
          <span
            className="attenue"
            title={`${ligne.motif ?? 'sans motif'} — ${ligne.modifie_par ?? 'auteur inconnu'}`}
          >
            {ligne.motif || <em>sans motif</em>}
            {ligne.modifie_par ? ` · ${ligne.modifie_par}` : ''}
          </span>
        ) : (
          ''
        ),
    },
  ]

  return (
    <TableParametrage
      titre="Base article"
      description="Référentiel article. Une référence exclue disparaît de tous les écrans d'analyse."
      colonnes={colonnes}
      page={requete.data}
      chargement={requete.isPending}
      erreur={requete.isError ? requete.error : null}
      onReessayer={() => void requete.refetch()}
      cleLigne={(ligne) => ligne.item_id}
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
          libelle: "Exclure de l'analyse",
          aide: "Retire les références des indicateurs, graphiques, grilles et exports. Réversible.",
          executer: (lignes) => appliquer(lignes, true),
          indisponible: (lignes) =>
            lignes.every((ligne) => ligne.exclu) ? 'Déjà exclues.' : null,
        },
        {
          libelle: 'Réintégrer',
          aide: "Remet les références dans l'analyse.",
          principale: true,
          executer: (lignes) => appliquer(lignes, false),
          indisponible: (lignes) =>
            lignes.every((ligne) => !ligne.exclu) ? 'Aucune exclusion à lever.' : null,
        },
      ]}
      actionsLigne={(ligne) => (
        <button
          type="button"
          className="bouton bouton--discret"
          disabled={mutation.isPending}
          onClick={() => appliquer([ligne], !ligne.exclu)}
        >
          {ligne.exclu ? 'Réintégrer' : 'Exclure'}
        </button>
      )}
      barre={
        <div className="rang" style={{ gap: 'var(--espace-2)' }}>
          <input
            className="champ"
            type="search"
            placeholder="Référence, désignation, catégorie…"
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
            aria-label="État des références"
          >
            <option value="tous">Toutes</option>
            <option value="exclus">Exclues seulement</option>
            <option value="inclus">Dans l'analyse seulement</option>
          </select>
        </div>
      }
    />
  )
}
