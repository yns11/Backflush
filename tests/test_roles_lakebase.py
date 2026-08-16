"""Tests des rôles Postgres réattribués après la bascule.

La bascule transit → production crée une table NEUVE : les privilèges de
l'ancienne sont perdus, d'où le re-GRANT. Ce GRANT est construit à partir d'une
variable de bundle (``app_service_principal``) dont la valeur par défaut est la
chaîne vide.

En production, cette chaîne vide a produit ``GRANT SELECT ON "backflush"."…"
TO ""`` — rejeté par Postgres (« zero-length delimited identifier »), ce qui a
fait échouer la publication entière pour un paramètre simplement non renseigné.
Ces tests verrouillent le nettoyage des rôles et le fait qu'un échec de
journalisation ne masque jamais l'erreur d'origine.
"""

from __future__ import annotations

import argparse

import pytest
from psycopg import sql as pgsql

from src.jobs import sync_to_lakebase as job
from src.jobs.lakebase_schema import META_INGESTION, TABLES


def rendre(instruction: object) -> str:
    """Rend une instruction psycopg (ou une chaîne) sous forme de SQL lisible."""
    if isinstance(instruction, (pgsql.Composable,)):
        return instruction.as_string()
    return str(instruction)


class CurseurFactice:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal

    def __enter__(self) -> CurseurFactice:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def execute(self, instruction: object, params: object = None) -> None:
        self.journal.append(rendre(instruction))


class TransactionFactice:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> bool:
        return False


class ConnexionFactice:
    """Connexion minimale : n'enregistre que le SQL réellement exécuté."""

    def __init__(self) -> None:
        self.journal: list[str] = []

    def cursor(self) -> CurseurFactice:
        return CurseurFactice(self.journal)

    def transaction(self) -> TransactionFactice:
        return TransactionFactice()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    @property
    def grants(self) -> list[str]:
        return [sql for sql in self.journal if "GRANT" in sql]


class TestNormalisationDesRoles:
    def test_une_valeur_vide_ne_designe_aucun_role(self) -> None:
        """Cas exact rencontré en production : `--app-role=` sans valeur."""
        assert job.normaliser_roles([""]) == []

    def test_les_espaces_seuls_sont_ecartes(self) -> None:
        assert job.normaliser_roles(["   ", "\t", ""]) == []

    def test_les_valeurs_sont_deballees_de_leurs_espaces(self) -> None:
        assert job.normaliser_roles(["  backflush-analytics  "]) == ["backflush-analytics"]

    def test_les_doublons_sont_elimines_dans_l_ordre(self) -> None:
        roles = job.normaliser_roles(["b", "a", "b", " a "])
        assert roles == ["b", "a"]

    def test_les_roles_reels_sont_conserves_tels_quels(self) -> None:
        """Un client_id de principal de service n'est pas un identifiant SQL nu."""
        client_id = "1a2b3c4d-5e6f-7890-abcd-ef1234567890"
        assert job.normaliser_roles([client_id]) == [client_id]

    def test_l_analyseur_d_arguments_produit_bien_le_cas_vide(self) -> None:
        """Le bundle passe `--app-role=${var.app_service_principal}`, vide par défaut."""
        args = job.build_arg_parser().parse_args(["--app-role="])
        assert args.app_roles == [""]
        assert job.normaliser_roles(args.app_roles) == []


class TestGrantApresBascule:
    def test_aucun_grant_n_est_emis_sans_role(self) -> None:
        conn = ConnexionFactice()
        job._swap(conn, TABLES[0], f"{TABLES[0].name}__stg", "backflush", [], ())

        assert conn.grants == []
        assert conn.journal, "La bascule elle-même doit avoir eu lieu."

    def test_le_grant_est_emis_pour_un_role_renseigne(self) -> None:
        conn = ConnexionFactice()
        job._swap(conn, TABLES[0], f"{TABLES[0].name}__stg", "backflush", ["sp-appli"], ())

        assert conn.grants == [
            f'GRANT SELECT ON "backflush"."{TABLES[0].name}" TO "sp-appli"'
        ]

    def test_le_journal_d_ingestion_n_emet_pas_de_grant_vide(self) -> None:
        conn = ConnexionFactice()
        job.record_ingestion(
            conn, "backflush",
            table_name="dim_article", source_table="cat.sch.dim_article", row_count=10,
            started_at=job.datetime.now(job.UTC), ended_at=job.datetime.now(job.UTC),
            status="SUCCES", error_message=None, run_id="test", roles=[],
        )

        assert conn.grants == []
        assert any("INSERT INTO" in sql for sql in conn.journal)

    def test_le_journal_d_ingestion_reattribue_le_role_renseigne(self) -> None:
        conn = ConnexionFactice()
        job.record_ingestion(
            conn, "backflush",
            table_name="dim_article", source_table="cat.sch.dim_article", row_count=10,
            started_at=job.datetime.now(job.UTC), ended_at=job.datetime.now(job.UTC),
            status="SUCCES", error_message=None, run_id="test", roles=["sp-appli"],
        )

        assert conn.grants == [
            f'GRANT SELECT ON "backflush"."{META_INGESTION.name}" TO "sp-appli"'
        ]


class TestRemonteeDesErreurs:
    """Une erreur de publication doit remonter telle quelle, jamais remplacée."""

    def _preparer(self, monkeypatch: pytest.MonkeyPatch, conn: ConnexionFactice) -> argparse.Namespace:
        from contextlib import contextmanager

        @contextmanager
        def connexion_factice(_args):
            yield conn

        monkeypatch.setattr(job, "connect", connexion_factice)
        monkeypatch.setattr(job, "ensure_meta_table", lambda *a, **k: None)
        monkeypatch.setattr(job, "ensure_extensions", lambda *a: False)
        return job.build_arg_parser().parse_args(
            ["--pg-host=hote", f"--tables={TABLES[0].name}"]
        )

    def test_l_echec_du_journal_ne_masque_pas_la_cause_reelle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = ConnexionFactice()
        args = self._preparer(monkeypatch, conn)

        def publication_en_echec(*_a, **_k):
            raise RuntimeError("COPY interrompu : colonne manquante")

        def journal_en_echec(*_a, **_k):
            raise RuntimeError("connexion perdue pendant la journalisation")

        monkeypatch.setattr(job, "publish_table", publication_en_echec)
        monkeypatch.setattr(job, "record_ingestion", journal_en_echec)

        with pytest.raises(RuntimeError, match="COPY interrompu"):
            job.run(spark=None, args=args)

    def test_l_echec_est_journalise_quand_c_est_possible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = ConnexionFactice()
        args = self._preparer(monkeypatch, conn)
        journalises: list[str] = []

        monkeypatch.setattr(job, "publish_table", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("échec de publication")
        ))
        monkeypatch.setattr(
            job, "record_ingestion",
            lambda *a, **k: journalises.append(k["status"]),
        )

        with pytest.raises(RuntimeError, match="échec de publication"):
            job.run(spark=None, args=args)

        assert journalises == ["ECHEC"]
