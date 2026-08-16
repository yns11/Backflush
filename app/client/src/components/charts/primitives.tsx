/**
 * Primitives de dessin partagées par les graphiques.
 *
 * Les graphiques sont écrits en SVG plutôt qu'avec une bibliothèque : les
 * règles de notation retenues (extrémité de barre arrondie côté donnée
 * uniquement, séparateur de 2 px entre barres adjacentes, échelle honnête
 * passant par zéro, étiquettes directes sélectives, texture pour le scénario de
 * référence) ne sont pas exprimables par configuration dans les bibliothèques
 * courantes, et l'ensemble tient en quelques dizaines de lignes.
 */

import { useCallback, useState, type ReactNode } from 'react'

/** Rayon d'arrondi appliqué à l'extrémité « donnée » d'une barre. */
export const RAYON_BARRE = 4

/** Espace laissé entre deux remplissages adjacents, pour qu'ils ne fusionnent pas. */
export const ECART_BARRE = 2

export interface Marges {
  haut: number
  droite: number
  bas: number
  gauche: number
}

/**
 * Chemin d'une barre verticale arrondie **du seul côté de la donnée**.
 * La base reste franche : elle est ancrée au zéro de l'échelle, et un arrondi
 * y suggérerait à tort un flottement.
 */
export function barreVerticale(
  x: number,
  largeur: number,
  yValeur: number,
  yZero: number,
  rayon = RAYON_BARRE,
): string {
  const haut = Math.min(yValeur, yZero)
  const bas = Math.max(yValeur, yZero)
  const hauteur = Math.max(bas - haut, 0.5)
  const r = Math.min(rayon, largeur / 2, hauteur)
  const versLeHaut = yValeur <= yZero

  return versLeHaut
    ? `M ${x} ${bas} L ${x} ${haut + r} Q ${x} ${haut} ${x + r} ${haut} ` +
        `L ${x + largeur - r} ${haut} Q ${x + largeur} ${haut} ${x + largeur} ${haut + r} ` +
        `L ${x + largeur} ${bas} Z`
    : `M ${x} ${haut} L ${x} ${bas - r} Q ${x} ${bas} ${x + r} ${bas} ` +
        `L ${x + largeur - r} ${bas} Q ${x + largeur} ${bas} ${x + largeur} ${bas - r} ` +
        `L ${x + largeur} ${haut} Z`
}

/** Chemin d'une barre horizontale arrondie du seul côté de la donnée. */
export function barreHorizontale(
  y: number,
  hauteur: number,
  xValeur: number,
  xZero: number,
  rayon = RAYON_BARRE,
): string {
  const gauche = Math.min(xValeur, xZero)
  const droite = Math.max(xValeur, xZero)
  const largeur = Math.max(droite - gauche, 0.5)
  const r = Math.min(rayon, hauteur / 2, largeur)
  const versLaDroite = xValeur >= xZero

  return versLaDroite
    ? `M ${gauche} ${y} L ${droite - r} ${y} Q ${droite} ${y} ${droite} ${y + r} ` +
        `L ${droite} ${y + hauteur - r} Q ${droite} ${y + hauteur} ${droite - r} ${y + hauteur} ` +
        `L ${gauche} ${y + hauteur} Z`
    : `M ${droite} ${y} L ${gauche + r} ${y} Q ${gauche} ${y} ${gauche} ${y + r} ` +
        `L ${gauche} ${y + hauteur - r} Q ${gauche} ${y + hauteur} ${gauche + r} ${y + hauteur} ` +
        `L ${droite} ${y + hauteur} Z`
}

/**
 * Graduations « rondes » couvrant l'intervalle, zéro toujours inclus.
 * Une échelle qui ne passe pas par zéro exagère visuellement les variations :
 * sur des écarts signés, c'est une déformation inacceptable.
 */
export function graduations(min: number, max: number, cible = 4): number[] {
  const bas = Math.min(0, min)
  const haut = Math.max(0, max)
  if (bas === haut) return [0]

  const brut = (haut - bas) / cible
  const magnitude = 10 ** Math.floor(Math.log10(Math.abs(brut) || 1))
  const normalise = brut / magnitude
  const pas = (normalise >= 5 ? 10 : normalise >= 2 ? 5 : normalise >= 1 ? 2 : 1) * magnitude

  const valeurs: number[] = []
  for (let valeur = Math.floor(bas / pas) * pas; valeur <= haut + pas / 2; valeur += pas) {
    valeurs.push(Math.abs(valeur) < pas / 1e6 ? 0 : valeur)
  }
  return valeurs
}

/** Texture du scénario de référence (consommation théorique). */
export function MotifTheorique({ id, couleur }: { id: string; couleur: string }) {
  return (
    <pattern id={id} width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
      <rect width="6" height="6" fill="transparent" />
      <line x1="0" y1="0" x2="0" y2="6" stroke={couleur} strokeWidth="2.4" opacity="0.75" />
    </pattern>
  )
}

/* -------------------------------------------------------------------------
   Infobulle
   ------------------------------------------------------------------------- */

export interface ContenuInfobulle {
  titre: string
  lignes: Array<{ libelle: string; valeur: string; couleur?: string }>
  note?: string
}

interface EtatInfobulle extends ContenuInfobulle {
  x: number
  y: number
}

/**
 * Infobulle au survol. Positionnée en coordonnées écran (`position: fixed`),
 * elle n'est jamais rognée par le conteneur défilant de la carte.
 */
export function useInfobulle() {
  const [etat, setEtat] = useState<EtatInfobulle | null>(null)

  const afficher = useCallback(
    (evenement: { clientX: number; clientY: number }, contenu: ContenuInfobulle) => {
      setEtat({ ...contenu, x: evenement.clientX, y: evenement.clientY })
    },
    [],
  )

  const masquer = useCallback(() => setEtat(null), [])

  const element: ReactNode = etat ? (
    <div
      className="infobulle"
      role="tooltip"
      style={{
        // Décalage pour ne pas masquer la marque survolée, puis recadrage si
        // l'infobulle sortirait de la fenêtre.
        left: Math.min(etat.x + 14, window.innerWidth - 240),
        top: Math.min(etat.y + 14, window.innerHeight - 140),
      }}
    >
      <div className="infobulle__titre">{etat.titre}</div>
      {etat.lignes.map((ligne) => (
        <div key={ligne.libelle} className="infobulle__ligne">
          <span className="attenue">
            {ligne.couleur && (
              <span
                className="legende__marque"
                style={{ background: ligne.couleur, display: 'inline-block', marginRight: 5 }}
              />
            )}
            {ligne.libelle}
          </span>
          <span className="infobulle__valeur">{ligne.valeur}</span>
        </div>
      ))}
      {etat.note && (
        <div className="attenue" style={{ marginTop: 4, fontSize: 10.5 }}>
          {etat.note}
        </div>
      )}
    </div>
  ) : null

  return { afficher, masquer, element }
}
