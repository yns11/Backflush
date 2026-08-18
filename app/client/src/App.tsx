/**
 * Coquille de l'application : en-tête, navigation, filtres transverses, slicer
 * temporel, écran actif et tiroir de détail.
 *
 * Aucune règle métier ici : uniquement de l'assemblage et de l'aiguillage.
 */

import { useCallback, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api, ErreurApi } from '@/api/client'
import type { ReponseAssistant } from '@/api/types'
import { Assistant } from '@/components/Assistant'
import { BandeauQualite } from '@/components/BandeauQualite'
import { BarreFiltres } from '@/components/BarreFiltres'
import { EtatErreur } from '@/components/Etats'
import { BlocRepliable } from '@/components/Repliable'
import { SlicerTemporel } from '@/components/SlicerTemporel'
import { TiroirFiche } from '@/components/TiroirFiche'
import { BaseArticle } from '@/pages/BaseArticle'
import { PageDetail, PageGrille } from '@/pages/Grilles'
import { Nomenclature } from '@/pages/Nomenclature'
import { PerimetreSynthetique } from '@/pages/PerimetreSynthetique'
import { Synthese } from '@/pages/Synthese'
import { FiltresProvider, useFiltres } from '@/state/filtres'
import { BasculeMesure, MesureProvider } from '@/state/mesure'
import { NavigationProvider, PAGES, useNavigation, type Page } from '@/state/navigation'
import { useTheme } from '@/state/theme'

export function App() {
  const options = useQuery({ queryKey: ['options-filtres'], queryFn: api.optionsFiltres })

  // Une base absente ou injoignable est un cas de première classe : on affiche
  // un message actionnable plutôt qu'une application vide.
  if (options.isError) {
    return (
      <div className="coquille">
        <Entete themeSeulement />
        <main className="contenu">
          <div className="carte">
            <div className="carte__corps">
              <EtatErreur erreur={options.error} onReessayer={() => void options.refetch()} />
              {options.error instanceof ErreurApi && options.error.estIndisponibilite && (
                <p className="attenue" style={{ textAlign: 'center' }}>
                  Vérifiez que la ressource « postgres » est attachée à l'application et que le job
                  d'ingestion a bien publié le schéma dans Lakebase. Le détail du diagnostic est
                  disponible sur <code>/api/health</code>.
                </p>
              )}
            </div>
          </div>
        </main>
      </div>
    )
  }

  return (
    <FiltresProvider options={options.data}>
      <MesureProvider>
        <NavigationProvider>
          <Coquille optionsChargement={options.isPending} />
        </NavigationProvider>
      </MesureProvider>
    </FiltresProvider>
  )
}

function Coquille({ optionsChargement }: { optionsChargement: boolean }) {
  const { filtres, modifier } = useFiltres()
  const { page, fiche, ouvrirFiche, fermerFiche, aller } = useNavigation()
  const [amorceAssistant, setAmorceAssistant] = useState<{
    question: string
    reponse: ReponseAssistant
  } | null>(null)

  const options = useQuery({ queryKey: ['options-filtres'], queryFn: api.optionsFiltres })

  // L'histogramme du slicer couvre TOUT l'historique : seules les dimensions
  // non temporelles du filtre s'y appliquent, pour montrer ce qui est exclu.
  const chronologie = useQuery({
    queryKey: ['chronologie', { ...filtres, date_debut: null, date_fin: null }],
    queryFn: () => api.chronologie(filtres),
    enabled: !optionsChargement,
  })

  const surAnalyseIA = useCallback((question: string, reponse: ReponseAssistant) => {
    setAmorceAssistant({ question, reponse })
    aller('assistant')
  }, [aller])

  // Les écrans de paramétrage portent sur le référentiel, pas sur une période :
  // leur imposer les filtres transverses et le slicer n'aurait aucun sens.
  const ecranReferentiel = page === 'articles' || page === 'nomenclature'

  return (
    <div className="coquille">
      <Entete />
      <main className="contenu">
        <BandeauQualite />

        {!ecranReferentiel && (
          <>
            <BlocRepliable
              cle="filtres"
              titre="Filtres"
              resume={<ResumeFiltres />}
            >
              <BarreFiltres options={options.data} />
            </BlocRepliable>

            {page !== 'assistant' && (
              <BlocRepliable
                cle="periode"
                titre="Période"
                resume={
                  filtres.date_debut && filtres.date_fin
                    ? `${filtres.date_debut} → ${filtres.date_fin}`
                    : "tout l'historique"
                }
              >
                <SlicerTemporel
                  semaines={chronologie.data?.semaines ?? []}
                  dateDebut={filtres.date_debut}
                  dateFin={filtres.date_fin}
                  onChangement={(debut, fin) => modifier({ date_debut: debut, date_fin: fin })}
                />
              </BlocRepliable>
            )}
          </>
        )}

        {page === 'synthese' && <Synthese />}
        {page === 'programmes' && <PageGrille cle="programmes" onAnalyseIA={surAnalyseIA} />}
        {page === 'perimetres' && <VuePerimetres onAnalyseIA={surAnalyseIA} />}
        {page === 'references' && <PageGrille cle="composants" onAnalyseIA={surAnalyseIA} />}
        {page === 'detail' && <PageDetail onAnalyseIA={surAnalyseIA} />}
        {page === 'articles' && <BaseArticle />}
        {page === 'nomenclature' && <Nomenclature />}
        {page === 'assistant' && (
          <Assistant
            filtres={filtres}
            pageActive={page}
            amorce={amorceAssistant}
            onAmorceConsommee={() => setAmorceAssistant(null)}
          />
        )}
      </main>

      {fiche && (
        <TiroirFiche
          genre={fiche.genre}
          identifiant={fiche.id}
          filtres={filtres}
          onFermer={fermerFiche}
          onFiltrerSur={(genre, identifiant) => {
            modifier(
              genre === 'composant' ? { composants: [identifiant] } : { parents: [identifiant] },
            )
            fermerFiche()
            aller('detail')
          }}
        />
      )}

      {/* Point d'entrée du drill-through depuis n'importe quel écran. */}
      <span className="invisible" aria-live="polite">
        {fiche ? `Fiche ${fiche.id} ouverte` : ''}
      </span>
      <RaccourciFiche onOuvrir={ouvrirFiche} />
    </div>
  )
}

/**
 * Résumé de la sélection, affiché quand le bloc de filtres est replié.
 *
 * Un bloc fermé qui ne dit rien de ce qu'il cache oblige à le rouvrir pour
 * vérifier qu'aucun filtre oublié ne fausse la lecture — ce qui annule le
 * bénéfice du pliage.
 */
function ResumeFiltres() {
  const { filtres } = useFiltres()
  const parts: string[] = []
  const ajouter = (libelle: string, valeurs: string[]) => {
    if (valeurs.length === 1) parts.push(`${libelle} : ${valeurs[0]}`)
    else if (valeurs.length > 1) parts.push(`${valeurs.length} ${libelle.toLowerCase()}s`)
  }
  ajouter('Programme', filtres.programmes)
  ajouter('Périmètre', filtres.perimetres)
  ajouter('Catégorie', filtres.categories)
  ajouter('Parent', filtres.parents)
  ajouter('Composant', filtres.composants)
  ajouter('OF', filtres.ofs)
  ajouter('Statut OF', filtres.statuts_of)
  if (filtres.recherche) parts.push(`recherche « ${filtres.recherche} »`)
  if (filtres.exclure_conforme) parts.push('conformes exclues')
  if (filtres.coef_uniforme_uniquement) parts.push('coef. uniforme seulement')
  if (filtres.impact_min) parts.push(`impact ≥ ${filtres.impact_min} €`)

  return <>{parts.length === 0 ? 'aucun filtre actif' : parts.join(' · ')}</>
}

/**
 * Écran « Périmètres » : la grille hebdomadaire, ou la vue synthétique.
 *
 * La vue synthétique n'est pas un autre écran mais une autre LECTURE des mêmes
 * données : tableau croisé semaine × référence, à la manière du rapport
 * d'atelier. Elle exige un périmètre unique — l'écart en équivalent produit s'y
 * rapporte au volume produit d'une seule ligne de production.
 */
function VuePerimetres({
  onAnalyseIA,
}: {
  onAnalyseIA: (question: string, reponse: ReponseAssistant) => void
}) {
  const { filtres } = useFiltres()
  const { synthetique, definirSynthetique } = useNavigation()
  const perimetreUnique = filtres.perimetres.length === 1

  return (
    <div className="pile">
      <div className="rang" style={{ justifyContent: 'flex-end' }}>
        <label
          className="rang attenue"
          title={
            perimetreUnique
              ? 'Bascule vers le tableau croisé production × semaine et écart × semaine.'
              : "Sélectionnez un périmètre unique : l'écart en équivalent produit se rapporte au volume d'une seule ligne de production."
          }
        >
          <input
            type="checkbox"
            checked={synthetique}
            onChange={(evenement) => definirSynthetique(evenement.target.checked)}
          />
          Vue synthétique
          {!perimetreUnique && synthetique && ' — périmètre unique requis'}
        </label>
      </div>

      {synthetique ? (
        <PerimetreSynthetique />
      ) : (
        <PageGrille cle="perimetres" onAnalyseIA={onAnalyseIA} />
      )}
    </div>
  )
}

function Entete({ themeSeulement = false }: { themeSeulement?: boolean }) {
  const { theme, basculer } = useTheme()

  return (
    <header className="entete">
      <div className="entete__marque">
        {/* Deux images plutôt qu'une seule commutée en JavaScript : le thème
            « système » n'est pas connu du composant (aucun attribut n'est posé
            sur la racine), c'est la requête média qui tranche. La bascule est
            donc faite en CSS, avec exactement la même logique à trois états que
            les jetons de couleur. */}
        <img src="/logo.png" alt="eMotors" className="entete__logo entete__logo--clair" />
        <img src="/logo-sombre.png" alt="eMotors" className="entete__logo entete__logo--sombre" />
        <span>
          Backflush Analytics
          <div className="entete__sous-titre">Écarts de consommation composant</div>
        </span>
      </div>

      {!themeSeulement && <Navigation />}

      <div className="entete__actions">
        {!themeSeulement && <BasculeMesure />}
        <button
          type="button"
          className="bouton bouton--icone"
          onClick={basculer}
          title={`Thème : ${theme === 'systeme' ? 'système' : theme === 'dark' ? 'sombre' : 'clair'}`}
          aria-label="Basculer le thème clair / sombre"
        >
          {theme === 'dark' ? '☾' : '☀'}
        </button>
      </div>
    </header>
  )
}

function Navigation() {
  const { page, aller } = useNavigation()
  return (
    <nav className="nav" aria-label="Navigation principale">
      {(Object.keys(PAGES) as Page[]).map((cle) => (
        <button
          key={cle}
          type="button"
          className="nav__lien"
          aria-current={page === cle ? 'page' : undefined}
          onClick={() => aller(cle)}
        >
          {PAGES[cle]}
        </button>
      ))}
    </nav>
  )
}

/**
 * Ouverture d'une fiche depuis l'URL (`#composant=CMP-VIS-0001`).
 * Permet de partager un lien pointant directement sur une référence.
 */
function RaccourciFiche({
  onOuvrir,
}: {
  onOuvrir: (fiche: { genre: 'composant' | 'parent'; id: string }) => void
}) {
  const [traite, setTraite] = useState(false)
  if (!traite && typeof window !== 'undefined' && window.location.hash) {
    const [genre, identifiant] = window.location.hash.slice(1).split('=')
    if ((genre === 'composant' || genre === 'parent') && identifiant) {
      onOuvrir({ genre, id: decodeURIComponent(identifiant) })
    }
    setTraite(true)
  }
  return null
}
