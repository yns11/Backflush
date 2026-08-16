/**
 * Barre de filtres transverse.
 *
 * Elle pilote l'unique objet de filtres partagé par tous les écrans. La
 * recherche est temporisée : sans cela, chaque frappe déclencherait une requête
 * de trigramme sur la table de détail.
 */

import { useEffect, useState } from 'react'

import type { OptionsFiltres, StatutLigne, TypeEcart } from '@/api/types'
import { useFiltres } from '@/state/filtres'
import { SelecteurMultiple } from './SelecteurMultiple'

const DELAI_RECHERCHE_MS = 300

export function BarreFiltres({ options }: { options: OptionsFiltres | undefined }) {
  const { filtres, modifier, reinitialiser, nbCriteresActifs } = useFiltres()
  const [rechercheLocale, setRechercheLocale] = useState(filtres.recherche ?? '')
  const [avance, setAvance] = useState(false)

  // Synchronise le champ lorsque les filtres changent ailleurs (lien partagé,
  // réinitialisation, drill-through).
  useEffect(() => {
    setRechercheLocale(filtres.recherche ?? '')
  }, [filtres.recherche])

  useEffect(() => {
    const attendu = filtres.recherche ?? ''
    if (rechercheLocale === attendu) return
    const minuteur = setTimeout(
      () => modifier({ recherche: rechercheLocale.trim() || null }),
      DELAI_RECHERCHE_MS,
    )
    return () => clearTimeout(minuteur)
  }, [rechercheLocale, filtres.recherche, modifier])

  return (
    <div className="filtres">
      <div className="filtres__groupe" style={{ width: 240 }}>
        <label className="etiquette" htmlFor="recherche">
          Recherche
        </label>
        <input
          id="recherche"
          className="champ"
          type="search"
          placeholder="Référence ou désignation…"
          value={rechercheLocale}
          onChange={(evenement) => setRechercheLocale(evenement.target.value)}
        />
      </div>

      <SelecteurMultiple
        libelle="Programme"
        options={options?.programmes ?? []}
        valeurs={filtres.programmes}
        onChangement={(valeurs) => modifier({ programmes: valeurs })}
      />
      <SelecteurMultiple
        libelle="Catégorie composant"
        options={options?.categories ?? []}
        valeurs={filtres.categories}
        onChangement={(valeurs) => modifier({ categories: valeurs })}
      />
      <SelecteurMultiple
        libelle="Type d'écart"
        options={options?.types_ecart ?? []}
        valeurs={filtres.types_ecart}
        onChangement={(valeurs) => modifier({ types_ecart: valeurs as TypeEcart[] })}
      />
      <SelecteurMultiple
        libelle="Statut de ligne"
        options={options?.statuts_ligne ?? []}
        valeurs={filtres.statuts_ligne}
        onChangement={(valeurs) => modifier({ statuts_ligne: valeurs as StatutLigne[] })}
      />

      <div className="filtres__actions">
        <button
          type="button"
          className="bouton"
          onClick={() => setAvance((precedent) => !precedent)}
          aria-expanded={avance}
        >
          Réglages avancés {avance ? '▴' : '▾'}
        </button>
        <button
          type="button"
          className="bouton"
          onClick={reinitialiser}
          disabled={nbCriteresActifs === 0}
          title="Revenir aux filtres par défaut"
        >
          Réinitialiser{nbCriteresActifs > 0 ? ` (${nbCriteresActifs})` : ''}
        </button>
      </div>

      {avance && (
        <div
          className="filtres"
          style={{
            width: '100%',
            margin: 0,
            border: 0,
            borderTop: '1px solid var(--bordure)',
            borderRadius: 0,
            paddingLeft: 0,
            paddingRight: 0,
          }}
        >
          <div className="filtres__groupe" style={{ width: 190 }}>
            <label className="etiquette" htmlFor="seuil">
              Tolérance absolue (unités)
            </label>
            <input
              id="seuil"
              className="champ"
              type="number"
              min={0}
              step={0.1}
              value={filtres.seuil_conformite}
              onChange={(evenement) =>
                modifier({ seuil_conformite: Math.max(0, Number(evenement.target.value) || 0) })
              }
              title="Une ligne dont l'écart absolu reste sous ce seuil est conforme."
            />
          </div>

          <div className="filtres__groupe" style={{ width: 210 }}>
            <label className="etiquette" htmlFor="seuil-pct">
              Tolérance relative (%)
            </label>
            <input
              id="seuil-pct"
              className="champ"
              type="number"
              min={0}
              max={100}
              step={0.5}
              placeholder="inactive"
              value={filtres.seuil_pct ?? ''}
              onChange={(evenement) =>
                modifier({
                  seuil_pct: evenement.target.value === '' ? null : Number(evenement.target.value),
                })
              }
              title={
                "Combinée en OU avec la tolérance absolue. Indispensable quand les volumes " +
                "sont hétérogènes : 0,5 unité n'a pas le même sens sur une vis et sur un stator."
              }
            />
          </div>

          <div className="filtres__groupe" style={{ width: 190 }}>
            <label className="etiquette" htmlFor="impact">
              Impact minimum (€)
            </label>
            <input
              id="impact"
              className="champ"
              type="number"
              min={0}
              step={100}
              placeholder="aucun"
              value={filtres.impact_min ?? ''}
              onChange={(evenement) =>
                modifier({
                  impact_min: evenement.target.value === '' ? null : Number(evenement.target.value),
                })
              }
              title="Ne conserver que les lignes dont l'impact financier absolu atteint ce montant."
            />
          </div>

          <label className="rang" style={{ marginTop: 14 }}>
            <input
              type="checkbox"
              checked={filtres.exclure_conforme}
              onChange={(evenement) => modifier({ exclure_conforme: evenement.target.checked })}
            />
            Masquer les lignes conformes
          </label>

          <label className="rang" style={{ marginTop: 14 }}>
            <input
              type="checkbox"
              checked={filtres.coef_uniforme_uniquement}
              onChange={(evenement) =>
                modifier({ coef_uniforme_uniquement: evenement.target.checked })
              }
              title="Ne garder que les lignes dont l'écart en équivalent produit est calculable."
            />
            Coefficient uniforme uniquement
          </label>
        </div>
      )}
    </div>
  )
}
