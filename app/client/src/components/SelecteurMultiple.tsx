/**
 * Sélecteur à choix multiples, avec recherche incrémentale.
 *
 * Composant maison plutôt qu'une dépendance : le besoin tient en un menu
 * filtrable, et une bibliothèque de sélection apporterait un modèle de style
 * concurrent de celui de l'application.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

export function SelecteurMultiple({
  libelle,
  options,
  valeurs,
  onChangement,
  placeholder = 'Tous',
  largeur,
}: {
  libelle: string
  options: string[]
  valeurs: string[]
  onChangement: (valeurs: string[]) => void
  placeholder?: string
  largeur?: number
}) {
  const [ouvert, setOuvert] = useState(false)
  const [recherche, setRecherche] = useState('')
  const conteneur = useRef<HTMLDivElement>(null)

  // Fermeture au clic extérieur et à la touche Échap : sans cela, plusieurs
  // panneaux peuvent rester ouverts et masquer la barre de filtres.
  useEffect(() => {
    if (!ouvert) return
    const surClic = (evenement: MouseEvent) => {
      if (!conteneur.current?.contains(evenement.target as Node)) setOuvert(false)
    }
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === 'Escape') setOuvert(false)
    }
    document.addEventListener('mousedown', surClic)
    document.addEventListener('keydown', surTouche)
    return () => {
      document.removeEventListener('mousedown', surClic)
      document.removeEventListener('keydown', surTouche)
    }
  }, [ouvert])

  const filtrees = useMemo(() => {
    const terme = recherche.trim().toLowerCase()
    return terme ? options.filter((option) => option.toLowerCase().includes(terme)) : options
  }, [options, recherche])

  const basculer = (option: string) => {
    onChangement(
      valeurs.includes(option) ? valeurs.filter((v) => v !== option) : [...valeurs, option],
    )
  }

  const resume =
    valeurs.length === 0
      ? placeholder
      : valeurs.length === 1
        ? valeurs[0]
        : `${valeurs.length} sélectionnés`

  return (
    <div className="filtres__groupe" style={largeur ? { width: largeur } : undefined}>
      <label className="etiquette" id={`lbl-${libelle}`}>
        {libelle}
      </label>
      <div className="multi" ref={conteneur}>
        <button
          type="button"
          className="multi__declencheur"
          onClick={() => setOuvert((precedent) => !precedent)}
          aria-expanded={ouvert}
          aria-haspopup="listbox"
          aria-labelledby={`lbl-${libelle}`}
        >
          <span className="multi__valeur">{resume}</span>
          {valeurs.length > 1 && <span className="multi__compteur">{valeurs.length}</span>}
          <span aria-hidden="true" className="attenue">
            ▾
          </span>
        </button>

        {ouvert && (
          <div className="multi__panneau" role="listbox" aria-multiselectable="true">
            <input
              className="champ"
              style={{ width: '100%', marginBottom: 6 }}
              placeholder="Rechercher…"
              value={recherche}
              onChange={(evenement) => setRecherche(evenement.target.value)}
              autoFocus
            />
            <div className="rang" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
              <button
                type="button"
                className="bouton bouton--discret"
                style={{ height: 22 }}
                onClick={() => onChangement(filtrees)}
                disabled={filtrees.length === 0}
              >
                Tout cocher
              </button>
              <button
                type="button"
                className="bouton bouton--discret"
                style={{ height: 22 }}
                onClick={() => onChangement([])}
                disabled={valeurs.length === 0}
              >
                Effacer
              </button>
            </div>
            {filtrees.length === 0 && <div className="attenue" style={{ padding: 6 }}>Aucun résultat</div>}
            {filtrees.map((option) => (
              <label key={option} className="multi__option" role="option" aria-selected={valeurs.includes(option)}>
                <input
                  type="checkbox"
                  checked={valeurs.includes(option)}
                  onChange={() => basculer(option)}
                />
                <span className="multi__valeur">{option}</span>
              </label>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
