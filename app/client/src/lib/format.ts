/**
 * Mise en forme des valeurs — conventions françaises.
 *
 * Le formatage est purement présentationnel : la définition des indicateurs et
 * le nombre de décimales viennent du serveur.
 */

import type { FormatValeur, TypeColonne } from '@/api/types'

const LOCALE = 'fr-FR'

const cacheNombres = new Map<string, Intl.NumberFormat>()

function formateur(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const cle = JSON.stringify(options)
  let existant = cacheNombres.get(cle)
  if (!existant) {
    existant = new Intl.NumberFormat(LOCALE, options)
    cacheNombres.set(cle, existant)
  }
  return existant
}

/** Nombre avec séparateur de milliers et nombre de décimales imposé. */
export function nombre(valeur: number | null | undefined, decimales = 0): string {
  if (valeur === null || valeur === undefined || !Number.isFinite(valeur)) return '—'
  return formateur({
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  }).format(valeur)
}

/**
 * Montant en euros. Au-delà de 10 000 €, l'unité est abrégée : sur une tuile de
 * KPI, « 1,2 M€ » se lit d'un coup d'œil là où « 1 234 567,89 € » ne se lit pas.
 */
export function euro(valeur: number | null | undefined, decimales = 0, abreger = false): string {
  if (valeur === null || valeur === undefined || !Number.isFinite(valeur)) return '—'
  if (abreger) {
    const absolu = Math.abs(valeur)
    if (absolu >= 1_000_000) return `${nombre(valeur / 1_000_000, 1)} M€`
    if (absolu >= 10_000) return `${nombre(valeur / 1_000, 0)} k€`
  }
  return `${nombre(valeur, decimales)} €`
}

/** Pourcentage déjà exprimé en points (12.5 → « 12,5 % »). */
export function pourcent(valeur: number | null | undefined, decimales = 1): string {
  if (valeur === null || valeur === undefined || !Number.isFinite(valeur)) return '—'
  return `${nombre(valeur, decimales)} %`
}

/** Variation signée, toujours préfixée du signe pour lever toute ambiguïté. */
export function variation(valeur: number | null | undefined, decimales = 1): string {
  if (valeur === null || valeur === undefined || !Number.isFinite(valeur)) return '—'
  const signe = valeur > 0 ? '+' : ''
  return `${signe}${nombre(valeur, decimales)} %`
}

/** Date ISO (AAAA-MM-JJ) vers JJ/MM/AAAA. */
export function date(valeur: string | null | undefined): string {
  if (!valeur) return '—'
  const iso = valeur.slice(0, 10)
  const [annee, mois, jour] = iso.split('-')
  if (!annee || !mois || !jour) return valeur
  return `${jour}/${mois}/${annee}`
}

/** Horodatage complet, pour la fraîcheur des données. */
export function horodatage(valeur: string | null | undefined): string {
  if (!valeur) return '—'
  const instant = new Date(valeur)
  if (Number.isNaN(instant.getTime())) return valeur
  return instant.toLocaleString(LOCALE, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Ancienneté relative, en français (« il y a 3 h »). */
export function anciennete(valeur: string | null | undefined): string {
  if (!valeur) return 'inconnue'
  const instant = new Date(valeur).getTime()
  if (Number.isNaN(instant)) return 'inconnue'
  const minutes = Math.round((Date.now() - instant) / 60_000)
  if (minutes < 1) return "à l'instant"
  if (minutes < 60) return `il y a ${minutes} min`
  const heures = Math.round(minutes / 60)
  if (heures < 24) return `il y a ${heures} h`
  const jours = Math.round(heures / 24)
  return `il y a ${jours} j`
}

/** Mise en forme d'une valeur de KPI selon le format déclaré par le serveur. */
export function valeurIndicateur(valeur: number, format: FormatValeur): string {
  switch (format) {
    case 'euro':
      return euro(valeur, 0, true)
    case 'pourcent':
      return pourcent(valeur, 1)
    case 'decimal':
      return nombre(valeur, 1)
    case 'entier':
    default:
      return nombre(valeur, 0)
  }
}

/** Mise en forme d'une cellule de grille selon le type déclaré de la colonne. */
export function valeurCellule(
  valeur: unknown,
  type: TypeColonne,
  decimales: number,
): string {
  if (valeur === null || valeur === undefined || valeur === '') return '—'
  switch (type) {
    case 'euro':
      return euro(Number(valeur), decimales)
    case 'pourcent':
      return pourcent(Number(valeur), decimales)
    case 'decimal':
      return nombre(Number(valeur), decimales)
    case 'entier':
      return nombre(Number(valeur), 0)
    case 'date':
      return date(String(valeur))
    case 'booleen':
      return valeur === true || valeur === 'true' ? 'Oui' : 'Non'
    default:
      return String(valeur)
  }
}

/** Abrège un nombre pour un axe de graphique (2 400 000 → « 2,4 M »). */
export function axeCourt(valeur: number): string {
  const absolu = Math.abs(valeur)
  if (absolu >= 1_000_000) return `${nombre(valeur / 1_000_000, 1)} M`
  if (absolu >= 1_000) return `${nombre(valeur / 1_000, 0)} k`
  return nombre(valeur, absolu < 10 && absolu > 0 ? 1 : 0)
}

/** Date du jour au format ISO, sans dépendre du fuseau du navigateur. */
export function aujourdhuiIso(): string {
  const maintenant = new Date()
  const decalage = maintenant.getTimezoneOffset() * 60_000
  return new Date(maintenant.getTime() - decalage).toISOString().slice(0, 10)
}
