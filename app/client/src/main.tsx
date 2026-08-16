import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { App } from './App'
import { ErreurApi } from './api/client'
import './styles/tokens.css'
import './styles/app.css'

/**
 * Politique de cache et de reprise.
 *
 * Les données sont rafraîchies une fois par jour par le job d'ingestion :
 * conserver une réponse une minute évite de rejouer les mêmes agrégats à chaque
 * navigation, sans jamais afficher un chiffre périmé de plus d'une minute.
 *
 * Une erreur 4xx (filtre invalide, grille inconnue) n'est jamais rejouée : elle
 * se reproduirait à l'identique. Seules les erreurs transitoires sont réessayées.
 */
const client = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 10 * 60_000,
      refetchOnWindowFocus: false,
      retry: (nombreEchecs, erreur) => {
        if (erreur instanceof ErreurApi && erreur.statut >= 400 && erreur.statut < 500) return false
        return nombreEchecs < 2
      },
    },
  },
})

const racine = document.getElementById('root')
if (!racine) throw new Error("Élément racine introuvable : vérifiez index.html.")

createRoot(racine).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
