/**
 * Slicer temporel — sélection d'une plage de semaines par brossage.
 *
 * Trois principes :
 *
 * 1. **Montrer ce qu'on exclut.** La piste couvre TOUT l'historique disponible,
 *    pas seulement la sélection : l'utilisateur voit d'un coup d'œil ce qu'il
 *    laisse de côté.
 * 2. **Manipulation directe.** Poignées glissables, plage déplaçable en bloc,
 *    et raccourcis pour les horizons usuels d'un pilotage hebdomadaire.
 * 3. **Accessible au clavier.** Chaque poignée est un `slider` ARIA : flèches
 *    pour une semaine, Origine/Fin pour les bornes. Un composant de brossage
 *    utilisable uniquement à la souris exclut une partie des utilisateurs.
 *
 * Chaque case porte son NUMÉRO DE SEMAINE, et rien d'autre. La version
 * précédente y encodait l'impact financier en histogramme : deux lectures se
 * disputaient alors le même objet — « où suis-je dans le temps » et « quand
 * est-ce que ça a coûté cher » —, la seconde étant déjà servie, en plus grand
 * et avec ses axes, par la tendance hebdomadaire juste en dessous. Repère
 * temporel d'un côté, mesure de l'autre.
 *
 * La piste est en HTML et non en SVG : un SVG étiré en largeur (`preserveAspect
 * Ratio="none"`, nécessaire pour occuper la largeur disponible) déforme aussi
 * le texte. Des cases en grille CSS restent nettes à toute largeur, et le
 * numéro de semaine est ici le contenu principal.
 */

import { useCallback, useMemo, useRef, useState } from 'react'

import type { SemaineAgregee } from '@/api/types'
import { date as formatDate } from '@/lib/format'

interface Preset {
  cle: string
  libelle: string
  semaines: number | 'ytd' | 'tout'
  aide: string
}

const PRESETS: Preset[] = [
  { cle: '4', libelle: '4 sem.', semaines: 4, aide: 'Le mois écoulé' },
  { cle: '8', libelle: '8 sem.', semaines: 8, aide: 'Deux mois' },
  { cle: '13', libelle: '13 sem.', semaines: 13, aide: 'Un trimestre' },
  { cle: '26', libelle: '26 sem.', semaines: 26, aide: 'Un semestre' },
  { cle: '52', libelle: '52 sem.', semaines: 52, aide: 'Douze mois glissants' },
  { cle: 'ytd', libelle: 'Année', semaines: 'ytd', aide: "Depuis le 1er janvier" },
  { cle: 'tout', libelle: 'Tout', semaines: 'tout', aide: "Tout l'historique disponible" },
]

export function SlicerTemporel({
  semaines,
  dateDebut,
  dateFin,
  onChangement,
}: {
  semaines: SemaineAgregee[]
  dateDebut: string | null
  dateFin: string | null
  onChangement: (debut: string | null, fin: string | null) => void
}) {
  const pisteRef = useRef<HTMLDivElement>(null)
  const [glissement, setGlissement] = useState<'debut' | 'fin' | 'plage' | null>(null)
  const ancre = useRef<{ index: number; debut: number; fin: number } | null>(null)

  const cles = useMemo(() => semaines.map((s) => s.semaine_debut), [semaines])

  const indexDebut = useMemo(() => indexPour(cles, dateDebut, 0), [cles, dateDebut])
  const indexFin = useMemo(() => indexPour(cles, dateFin, cles.length - 1), [cles, dateFin])

  const appliquer = useCallback(
    (debut: number, fin: number) => {
      if (semaines.length === 0) return
      const bas = Math.max(0, Math.min(debut, fin))
      const haut = Math.min(semaines.length - 1, Math.max(debut, fin))
      onChangement(cles[bas] ?? null, cles[haut] ?? null)
    },
    [cles, semaines.length, onChangement],
  )

  const indexDepuisPointeur = useCallback(
    (clientX: number): number => {
      const rect = pisteRef.current?.getBoundingClientRect()
      if (!rect || rect.width === 0 || semaines.length === 0) return 0
      const ratio = (clientX - rect.left) / rect.width
      return Math.max(0, Math.min(semaines.length - 1, Math.floor(ratio * semaines.length)))
    },
    [semaines.length],
  )

  const demarrer =
    (genre: 'debut' | 'fin' | 'plage') => (evenement: React.PointerEvent<HTMLElement>) => {
      evenement.preventDefault()
      evenement.stopPropagation()
      evenement.currentTarget.setPointerCapture(evenement.pointerId)
      setGlissement(genre)
      ancre.current = {
        index: indexDepuisPointeur(evenement.clientX),
        debut: indexDebut,
        fin: indexFin,
      }
    }

  const deplacer = (evenement: React.PointerEvent<HTMLElement>) => {
    if (!glissement || !ancre.current) return
    const index = indexDepuisPointeur(evenement.clientX)
    if (glissement === 'debut') appliquer(index, indexFin)
    else if (glissement === 'fin') appliquer(indexDebut, index)
    else {
      // Déplacement en bloc : la largeur de la fenêtre est préservée, et la
      // plage est retenue aux bornes de l'historique plutôt que rognée.
      const depart = ancre.current
      const largeurPlage = depart.fin - depart.debut
      let debut = depart.debut + (index - depart.index)
      debut = Math.max(0, Math.min(debut, semaines.length - 1 - largeurPlage))
      appliquer(debut, debut + largeurPlage)
    }
  }

  const arreter = (evenement: React.PointerEvent<HTMLElement>) => {
    if (evenement.currentTarget.hasPointerCapture(evenement.pointerId)) {
      evenement.currentTarget.releasePointerCapture(evenement.pointerId)
    }
    setGlissement(null)
    ancre.current = null
  }

  const clavier = (borne: 'debut' | 'fin') => (evenement: React.KeyboardEvent) => {
    const courant = borne === 'debut' ? indexDebut : indexFin
    const deltas: Record<string, number> = {
      ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1,
      PageDown: -4, PageUp: 4,
    }
    let cible: number | null = null
    if (evenement.key in deltas) cible = courant + (deltas[evenement.key] ?? 0)
    else if (evenement.key === 'Home') cible = 0
    else if (evenement.key === 'End') cible = semaines.length - 1
    if (cible === null) return
    evenement.preventDefault()
    if (borne === 'debut') appliquer(cible, indexFin)
    else appliquer(indexDebut, cible)
  }

  const appliquerPreset = (preset: Preset) => {
    if (semaines.length === 0) return
    const dernier = semaines.length - 1
    if (preset.semaines === 'tout') return appliquer(0, dernier)
    if (preset.semaines === 'ytd') {
      const anneeCourante = semaines[dernier]?.semaine_debut.slice(0, 4)
      const premier = semaines.findIndex((s) => s.semaine_debut.slice(0, 4) === anneeCourante)
      return appliquer(premier < 0 ? 0 : premier, dernier)
    }
    return appliquer(Math.max(0, dernier - preset.semaines + 1), dernier)
  }

  const presetActif = useMemo(() => {
    if (semaines.length === 0) return null
    if (indexDebut === 0 && indexFin === semaines.length - 1) return 'tout'
    if (indexFin !== semaines.length - 1) return null
    const nombreSemaines = indexFin - indexDebut + 1
    return PRESETS.find((p) => p.semaines === nombreSemaines)?.cle ?? null
  }, [indexDebut, indexFin, semaines.length])

  if (semaines.length === 0) {
    return (
      <div className="slicer">
        <div className="attenue">Aucune semaine disponible sur cette sélection.</div>
      </div>
    )
  }

  const nbSelectionnees = indexFin - indexDebut + 1
  const pourcent = (index: number) => `${(index / semaines.length) * 100}%`

  return (
    <div className="slicer">
      <div className="slicer__entete">
        <span className="slicer__periode">
          {formatDate(cles[indexDebut] ?? null)} → {formatDate(dimancheDeLaSemaine(cles[indexFin]))}
        </span>
        <span className="attenue">
          {nbSelectionnees} semaine{nbSelectionnees > 1 ? 's' : ''} sur {semaines.length}
        </span>
        <div className="slicer__presets" role="group" aria-label="Horizons prédéfinis">
          {PRESETS.map((preset) => (
            <button
              key={preset.cle}
              type="button"
              className="slicer__preset"
              aria-pressed={presetActif === preset.cle}
              title={preset.aide}
              onClick={() => appliquerPreset(preset)}
            >
              {preset.libelle}
            </button>
          ))}
        </div>
      </div>

      <div
        className="slicer__piste"
        ref={pisteRef}
        onPointerMove={deplacer}
        onPointerUp={arreter}
        onPointerCancel={arreter}
        role="group"
        aria-label="Sélection de la plage de semaines"
        style={{ gridTemplateColumns: `repeat(${semaines.length}, minmax(0, 1fr))` }}
      >
        {semaines.map((semaine, index) => {
          const dansSelection = index >= indexDebut && index <= indexFin
          return (
            <button
              key={semaine.semaine_debut}
              type="button"
              tabIndex={-1}
              className={`slicer__case${dansSelection ? ' slicer__case--active' : ''}`}
              title={`${semaine.semaine_libelle} — semaine du ${formatDate(semaine.semaine_debut)} au ${formatDate(dimancheDeLaSemaine(semaine.semaine_debut))}`}
              onClick={() => {
                // Cliquer une case hors sélection étend la plage de son côté :
                // plus direct que d'aller viser la poignée.
                if (index < indexDebut) appliquer(index, indexFin)
                else if (index > indexFin) appliquer(indexDebut, index)
              }}
            >
              {numeroSemaine(semaine)}
            </button>
          )
        })}

        {/* Plage sélectionnée, déplaçable en bloc */}
        <div
          className="slicer__plage"
          style={{
            left: pourcent(indexDebut),
            width: pourcent(indexFin + 1 - indexDebut),
            cursor: glissement === 'plage' ? 'grabbing' : 'grab',
          }}
          onPointerDown={demarrer('plage')}
        />

        {(['debut', 'fin'] as const).map((borne) => {
          const index = borne === 'debut' ? indexDebut : indexFin
          return (
            <div
              key={borne}
              className="slicer__poignee"
              style={{ left: pourcent(borne === 'debut' ? indexDebut : indexFin + 1) }}
              onPointerDown={demarrer(borne)}
              onKeyDown={clavier(borne)}
              tabIndex={0}
              role="slider"
              aria-label={borne === 'debut' ? 'Première semaine' : 'Dernière semaine'}
              aria-valuemin={0}
              aria-valuemax={semaines.length - 1}
              aria-valuenow={index}
              aria-valuetext={semaines[index]?.semaine_libelle ?? ''}
            />
          )
        })}
      </div>
    </div>
  )
}

/** « 2026-S14 » → « S14 ». Le libellé complet reste dans l'infobulle native. */
function numeroSemaine(semaine: SemaineAgregee): string {
  return semaine.semaine_libelle.replace(/^\d{4}-/, '')
}

/**
 * Dimanche de la semaine ISO commençant au lundi donné.
 *
 * La borne de sélection est le LUNDI de la dernière semaine retenue : l'afficher
 * tel quel laisserait croire que les six derniers jours sont exclus, alors que
 * la semaine entière est comprise. La période annoncée doit couvrir ce qui est
 * réellement analysé.
 */
export function dimancheDeLaSemaine(lundi: string | null | undefined): string | null {
  if (!lundi) return null
  const date = new Date(`${lundi}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return lundi
  date.setUTCDate(date.getUTCDate() + 6)
  return date.toISOString().slice(0, 10)
}

/** Index de la semaine correspondant à une date, ou une valeur de repli. */
function indexPour(cles: string[], valeur: string | null, defaut: number): number {
  if (!valeur || cles.length === 0) return Math.max(0, Math.min(defaut, cles.length - 1))
  const exact = cles.indexOf(valeur)
  if (exact >= 0) return exact
  // La date ne tombe pas sur un lundi présent dans l'historique (lien partagé,
  // saisie manuelle) : on retient la semaine la plus proche sans dépasser.
  let candidat = defaut
  for (let index = 0; index < cles.length; index += 1) {
    const cle = cles[index]
    if (cle !== undefined && cle <= valeur) candidat = index
  }
  return Math.max(0, Math.min(candidat, cles.length - 1))
}
