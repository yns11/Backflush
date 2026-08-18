/**
 * Écrans de grille : Programmes, Références, Détail.
 *
 * Les trois partagent la même mécanique — seule change la grille interrogée.
 * Les colonnes, leur format et leur définition viennent du serveur : ajouter
 * une mesure ne demande aucune modification ici.
 */

import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { CleGrille, LigneGrille, ReponseAssistant } from '@/api/types'
import { EtatErreur, Squelette } from '@/components/Etats'
import { GrilleDonnees } from '@/components/GrilleDonnees'
import { useFiltres } from '@/state/filtres'
import { useNavigation } from '@/state/navigation'

/**
 * Écran « Détail » — au choix, à la maille parent ou à la maille OF.
 *
 * L'ordre de fabrication est la maille d'investigation réelle de l'atelier :
 * deux lancements du même parent sur la même semaine y sont deux histoires
 * distinctes, souvent sur des équipes ou des postes différents, que la maille
 * parent × semaine confond.
 *
 * Le basculement est explicite, et non un axe ajouté d'office, parce que les
 * deux lectures ne se valent pas et ne donnent pas les mêmes chiffres : à la
 * maille OF, la non-consommation et la surconsommation sont toutes deux plus
 * élevées. Le total, lui, est identique — c'est le décalage des OF à cheval sur
 * deux semaines qui cesse de se compenser. La description de la grille, servie
 * par le serveur, l'énonce : sans elle, l'écart entre les deux écrans passerait
 * pour une incohérence de l'application.
 */
export function PageDetail({
  onAnalyseIA,
}: {
  onAnalyseIA: (question: string, reponse: ReponseAssistant) => void
}) {
  // La granularité vit dans l'état de navigation, pas ici : la barre de filtres
  // en dépend pour activer ou griser les critères « Numéro OF » et
  // « Statut OF », et elle n'est pas dans l'arbre de cet écran.
  const { detailParOf: parOf, definirDetailParOf: setParOf } = useNavigation()

  return (
    <div className="pile">
      <div className="rang" style={{ alignItems: 'flex-start' }}>
        <div className="bascule" role="group" aria-label="Granularité du détail">
          {(
            [
              [false, 'Par parent', 'Une ligne par parent, composant et semaine.'],
              [true, 'Par OF', "Ajoute l'ordre de fabrication à la granularité."],
            ] as const
          ).map(([valeur, libelle, aide]) => (
            <button
              key={libelle}
              type="button"
              className="bascule__option"
              aria-pressed={parOf === valeur}
              title={aide}
              onClick={() => setParOf(valeur)}
            >
              {libelle}
            </button>
          ))}
        </div>
      </div>

      <PageGrille cle={parOf ? 'details_of' : 'details'} onAnalyseIA={onAnalyseIA} />
    </div>
  )
}

export function PageGrille({
  cle,
  onAnalyseIA,
}: {
  cle: CleGrille
  onAnalyseIA: (question: string, reponse: ReponseAssistant) => void
}) {
  const { filtres, modifier } = useFiltres()
  const { ouvrirFiche, aller } = useNavigation()
  const [erreurIA, setErreurIA] = useState<string | null>(null)

  const definitions = useQuery({ queryKey: ['grilles'], queryFn: api.grilles })
  const etatAssistant = useQuery({ queryKey: ['assistant-etat'], queryFn: api.etatAssistant })

  const analyse = useMutation({
    mutationFn: (lignes: LigneGrille[]) =>
      api.analyserLot({ grille: cle, lignes, filtres, question: null }),
    onSuccess: (reponse, lignes) => {
      setErreurIA(null)
      onAnalyseIA(
        `Analyse de ${lignes.length} ligne(s) sélectionnée(s) dans la grille « ${cle} ».`,
        reponse,
      )
    },
    onError: (erreur: unknown) =>
      setErreurIA(erreur instanceof Error ? erreur.message : "L'analyse a échoué."),
  })

  if (definitions.isError) {
    return <EtatErreur erreur={definitions.error} onReessayer={() => void definitions.refetch()} />
  }
  if (definitions.isPending) return <Squelette hauteur={420} />

  const grille = definitions.data[cle]

  return (
    <div className="pile">
      <p className="attenue" style={{ margin: 0, maxWidth: '90ch' }}>
        {grille.description}
      </p>

      {erreurIA && (
        <div className="bandeau bandeau--critique" role="alert">
          {erreurIA}
        </div>
      )}

      <GrilleDonnees
        // Change de grille = change de jeu de colonnes, de tris et de clé de
        // ligne. Sans cette clé React, le composant est réutilisé tel quel et
        // conserve l'état initialisé pour la grille précédente — les colonnes
        // propres à la nouvelle n'apparaissent jamais.
        key={cle}
        cle={cle}
        grille={grille}
        filtres={filtres}
        onOuvrirFiche={(genre, identifiant) => ouvrirFiche({ genre, id: identifiant })}
        // Sur la grille des périmètres, la colonne ne mène nulle part : on y est.
        onOuvrirPerimetre={
          cle === 'perimetres'
            ? undefined
            : (perimetre) => {
                // Sélection REMPLACÉE, pas complétée : la vue synthétique porte
                // sur un périmètre unique. Le reste des filtres est conservé.
                modifier({ perimetres: [perimetre] })
                aller('perimetres', { synthetique: true })
              }
        }
        actions={[
          {
            libelle: analyse.isPending ? 'Analyse en cours…' : 'Analyse IA',
            aide:
              "Transmet les lignes sélectionnées (ou la page affichée) à l'assistant pour " +
              'identifier les motifs récurrents et proposer des actions priorisées.',
            desactive: analyse.isPending || etatAssistant.data?.actif === false,
            executer: (lignes) => analyse.mutate(lignes),
          },
        ]}
      />
    </div>
  )
}
