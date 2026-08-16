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
import { SlicerTemporel } from '@/components/SlicerTemporel'
import { TiroirFiche } from '@/components/TiroirFiche'
import { PageGrille } from '@/pages/Grilles'
import { Synthese } from '@/pages/Synthese'
import { FiltresProvider, useFiltres } from '@/state/filtres'
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
      <NavigationProvider>
        <Coquille optionsChargement={options.isPending} />
      </NavigationProvider>
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

  return (
    <div className="coquille">
      <Entete />
      <main className="contenu">
        <BandeauQualite />
        <BarreFiltres options={options.data} />

        {page !== 'assistant' && (
          <SlicerTemporel
            semaines={chronologie.data?.semaines ?? []}
            dateDebut={filtres.date_debut}
            dateFin={filtres.date_fin}
            onChangement={(debut, fin) => modifier({ date_debut: debut, date_fin: fin })}
          />
        )}

        {page === 'synthese' && <Synthese />}
        {page === 'programmes' && <PageGrille cle="programmes" onAnalyseIA={surAnalyseIA} />}
        {page === 'references' && <PageGrille cle="composants" onAnalyseIA={surAnalyseIA} />}
        {page === 'detail' && <PageGrille cle="details" onAnalyseIA={surAnalyseIA} />}
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

function Entete({ themeSeulement = false }: { themeSeulement?: boolean }) {
  const { theme, basculer } = useTheme()

  return (
    <header className="entete">
      <div className="entete__marque">
        <span className="entete__logo" aria-hidden="true">
          B
        </span>
        <span>
          Backflush Analytics
          <div className="entete__sous-titre">Écarts de consommation composant</div>
        </span>
      </div>

      {!themeSeulement && <Navigation />}

      <div className="entete__actions">
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
