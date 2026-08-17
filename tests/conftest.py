"""Fixtures partagées.

Une seule pour l'instant : une connexion Postgres **dont la transaction est
toujours annulée**. Les tests de paramétrage écrivent réellement (c'est tout
leur objet) ; sans annulation, ils laisseraient des exclusions et des surcharges
derrière eux, et les tests d'analytique suivants verraient des chiffres
inexplicables — le pire genre de test instable, celui qui échoue ailleurs que là
où est le problème.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def connexion() -> Iterator:
    """Connexion Postgres directe, annulée en fin de test.

    Volontairement en marge du pool applicatif : celui-ci est en lecture seule,
    et les tests doivent pouvoir poser un paramétrage pour en observer l'effet.
    """
    url = os.getenv("LAKEBASE_PG_URL")
    if not url:
        pytest.skip("LAKEBASE_PG_URL non défini.")

    import psycopg

    with psycopg.connect(url) as conn:
        conn.execute("SET search_path = backflush, public")
        try:
            yield conn
        finally:
            conn.rollback()
