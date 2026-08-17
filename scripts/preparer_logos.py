"""Prépare les logos web à partir des originaux de la racine du dépôt.

Les originaux (``logo.svg``, ``logo-sombre.png``) sont des tracés d'images :
815 ko et 3 500 chemins pour le premier, 220 ko pour le second. Servis tels
quels dans un en-tête de 56 px, ils coûteraient plus cher que le reste de
l'application réunie.

Ce script produit ``app/client/public/logo.png`` et ``logo-sombre.png``,
recadrés au contenu et dimensionnés pour un rendu de 40 px de haut en densité
double. Il est **reproductible** : sans lui, personne ne saurait d'où viennent
les fichiers du répertoire ``public``, ni comment les régénérer si la charte
change.

Usage ::

    pip install pillow playwright
    python scripts/preparer_logos.py
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

LOGGER = logging.getLogger("backflush.logos")

RACINE = Path(__file__).resolve().parents[1]
SORTIE = RACINE / "app" / "client" / "public"

#: Hauteur de rendu dans l'en-tête, doublée pour les écrans haute densité.
HAUTEUR = 80

#: Emplacements possibles de Chromium. Le runtime de développement distant en
#: fournit un ; en local, Playwright installe le sien.
CHROMIUM = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def rasteriser_svg(source: Path, destination: Path, largeur: int = 1057) -> None:
    """Rend un SVG en PNG via Chromium.

    Un tracé de 3 500 chemins ne se convertit pas correctement avec les
    bibliothèques légères ; le moteur de rendu du navigateur, lui, est
    exactement celui qui affichera la page.
    """
    from playwright.sync_api import sync_playwright

    executable = next((chemin for chemin in CHROMIUM if Path(chemin).exists()), None)
    with sync_playwright() as pilote:
        navigateur = pilote.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        page = navigateur.new_page(
            viewport={"width": largeur, "height": largeur}, device_scale_factor=2
        )
        page.set_content(f'<body style="margin:0">{source.read_text()}</body>')
        page.locator("svg").screenshot(path=str(destination))
        navigateur.close()


def recadrer(image: Image.Image, *, tolerance: int, marge_pct: float = 0.0) -> Image.Image:
    """Recadre sur le contenu, en prenant le pixel du coin comme couleur de fond.

    Le logo clair est sur blanc uni, le sombre sur un marine photographié donc
    légèrement dégradé : d'où la tolérance, réglée par appelant.
    """
    fond = Image.new("RGB", image.size, image.getpixel((2, 2)))
    masque = ImageChops.difference(image, fond).convert("L")
    boite = masque.point(lambda valeur: 255 if valeur > tolerance else 0).getbbox()
    if boite is None:
        LOGGER.warning("Aucun contenu détecté : l'image est laissée telle quelle.")
        return image
    if marge_pct:
        marge = round(image.height * marge_pct)
        boite = (
            max(0, boite[0] - marge), max(0, boite[1] - marge),
            min(image.width, boite[2] + marge), min(image.height, boite[3] + marge),
        )
    return image.crop(boite)


def redimensionner(image: Image.Image, destination: Path) -> None:
    ratio = HAUTEUR / image.height
    image.resize((round(image.width * ratio), HAUTEUR), Image.LANCZOS).save(
        destination, optimize=True
    )
    LOGGER.info(
        "%s : %s, %.1f ko",
        destination.name, Image.open(destination).size, destination.stat().st_size / 1024,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clair", type=Path, default=RACINE / "logo.svg")
    parser.add_argument("--sombre", type=Path, default=RACINE / "logo-sombre.png")
    args = parser.parse_args(argv)

    SORTIE.mkdir(parents=True, exist_ok=True)
    temporaire = Path(tempfile.mkdtemp(prefix="logos-"))
    try:
        brut = temporaire / "clair.png"
        rasteriser_svg(args.clair, brut)
        # Fond blanc uni : une tolérance basse suffit et conserve les bords
        # antialiasés.
        redimensionner(recadrer(Image.open(brut).convert("RGB"), tolerance=12), SORTIE / "logo.png")

        # Fond photographié : tolérance plus haute, et une marge pour ne pas
        # coller le logo au bord de sa plaque.
        redimensionner(
            recadrer(Image.open(args.sombre).convert("RGB"), tolerance=40, marge_pct=0.06),
            SORTIE / "logo-sombre.png",
        )
    finally:
        shutil.rmtree(temporaire, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    main()
