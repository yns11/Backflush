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

export function PageGrille({
  cle,
  onAnalyseIA,
}: {
  cle: CleGrille
  onAnalyseIA: (question: string, reponse: ReponseAssistant) => void
}) {
  const { filtres } = useFiltres()
  const { ouvrirFiche } = useNavigation()
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
        cle={cle}
        grille={grille}
        filtres={filtres}
        onOuvrirFiche={(genre, identifiant) => ouvrirFiche({ genre, id: identifiant })}
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
