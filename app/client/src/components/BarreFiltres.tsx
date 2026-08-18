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
import { useNavigation } from '@/state/navigation'
import { SelecteurMultiple } from './SelecteurMultiple'

const DELAI_RECHERCHE_MS = 300

/** Infobulle des deux critères d'OF quand ils sont hors de leur seule vue utile. */
const AIDE_HORS_AXE_OF =
  "Ce critère n\u2019existe qu\u2019à la maille de l\u2019ordre de fabrication : " +
  "activez « Par OF » sur l\u2019écran Détail. Les autres vues agrègent plusieurs " +
  "ordres par ligne et n\u2019ont pas cette colonne."

export function BarreFiltres({ options }: { options: OptionsFiltres | undefined }) {
  const { filtres, modifier, reinitialiser, nbCriteresActifs } = useFiltres()
  const { page, detailParOf } = useNavigation()
  const [rechercheLocale, setRechercheLocale] = useState(filtres.recherche ?? '')
  const [avance, setAvance] = useState(false)

  // Les deux critères d'OF ne portent que sur la vue « Détail par OF ». Partout
  // ailleurs ils sont grisés — et VIDÉS, ce qui n'est pas la même chose : grisés
  // seuls, ils resteraient dans l'URL et dans le compteur de critères actifs,
  // et l'utilisateur croirait filtrer sur un OF qu'aucune requête n'applique.
  const axeOfActif = page === 'detail' && detailParOf

  useEffect(() => {
    if (axeOfActif) return
    if (filtres.ofs.length === 0 && filtres.statuts_of.length === 0) return
    modifier({ ofs: [], statuts_of: [] })
  }, [axeOfActif, filtres.ofs, filtres.statuts_of, modifier])

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
        libelle="Périmètre"
        options={options?.perimetres ?? []}
        valeurs={filtres.perimetres}
        onChangement={(valeurs) => modifier({ perimetres: valeurs })}
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

      <ChampListe
        libelle="Numéro OF"
        valeurs={filtres.ofs}
        onChangement={(valeurs) => modifier({ ofs: valeurs })}
        desactive={!axeOfActif}
        placeholder="OF-… ; OF-…"
        aide={
          axeOfActif
            ? 'Un ou plusieurs numéros d\u2019ordre, séparés par une virgule, un point-virgule ou un retour à la ligne. Correspondance exacte : le numéro d\u2019OF est un identifiant, pas un libellé.'
            : AIDE_HORS_AXE_OF
        }
      />
      <SelecteurMultiple
        libelle="Statut OF"
        options={options?.statuts_of ?? []}
        valeurs={filtres.statuts_of}
        onChangement={(valeurs) => modifier({ statuts_of: valeurs })}
        desactive={!axeOfActif}
        aide={
          axeOfActif
            ? "Où en est l\u2019ordre dans son cycle de vie D365. Un écart sur un ordre non terminé est attendu : il lui reste des mouvements à venir."
            : AIDE_HORS_AXE_OF
        }
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

/**
 * Saisie libre d'une liste d'identifiants — ici, des numéros d'ordre.
 *
 * Un `SelecteurMultiple` serait le réflexe, mais il suppose une liste d'options
 * énumérable : sur un parc réel, les ordres de fabrication se comptent en
 * dizaines de milliers. Les charger tous pour en cocher deux coûterait plus que
 * la requête filtrée elle-même, et la liste resterait illisible.
 *
 * La correspondance est EXACTE : un numéro d'OF est un identifiant, pas un
 * libellé. Une recherche partielle y renverrait des ordres sans rapport et
 * ferait passer un filtre d'investigation pour un filtre de tri.
 *
 * Le champ garde son propre texte tant qu'il a le focus : normaliser à chaque
 * frappe effacerait le séparateur que l'utilisateur vient de taper.
 */
function ChampListe({
  libelle,
  valeurs,
  onChangement,
  placeholder,
  desactive = false,
  aide,
}: {
  libelle: string
  valeurs: string[]
  onChangement: (valeurs: string[]) => void
  placeholder?: string
  desactive?: boolean
  aide?: string
}) {
  const identifiant = `champ-liste-${libelle.replace(/\s+/g, '-').toLowerCase()}`
  const [texte, setTexte] = useState(valeurs.join(' ; '))
  const [saisie, setSaisie] = useState(false)

  // Hors saisie, le champ reflète l'état : lien partagé, réinitialisation, ou
  // vidage automatique quand on quitte la vue « Détail par OF ».
  useEffect(() => {
    if (!saisie) setTexte(valeurs.join(' ; '))
  }, [valeurs, saisie])

  const appliquer = (brut: string) => {
    const liste = brut
      .split(/[\s,;]+/)
      .map((element) => element.trim())
      .filter(Boolean)
    // Comparaison sur le contenu : sans elle, chaque perte de focus réécrirait
    // un tableau identique et relancerait toutes les requêtes de l'écran.
    if (liste.join(' ') !== valeurs.join(' ')) onChangement(liste)
  }

  return (
    <div className="filtres__groupe" style={{ width: 200 }}>
      <label className="etiquette" htmlFor={identifiant}>
        {libelle}
        {valeurs.length > 1 && <span className="multi__compteur">{valeurs.length}</span>}
      </label>
      <input
        id={identifiant}
        className="champ"
        type="text"
        value={texte}
        placeholder={placeholder}
        disabled={desactive}
        title={aide}
        onFocus={() => setSaisie(true)}
        onChange={(evenement) => setTexte(evenement.target.value)}
        onBlur={(evenement) => {
          setSaisie(false)
          appliquer(evenement.target.value)
        }}
        onKeyDown={(evenement) => {
          if (evenement.key === 'Enter') appliquer(evenement.currentTarget.value)
        }}
      />
    </div>
  )
}
