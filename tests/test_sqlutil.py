"""Tests des outils de préparation SQL des jobs."""

from __future__ import annotations

import pytest

from src.jobs.sqlutil import (
    UnsafeIdentifierError,
    render_template,
    split_statements,
    validate_identifier,
    validate_iso_date,
    validate_number,
)


class TestValidateIdentifier:
    @pytest.mark.parametrize(
        "valeur", ["backflush", "emotors_data_champions", "_interne", "t1", "A" * 128]
    )
    def test_accepte_les_identifiants_surs(self, valeur: str) -> None:
        assert validate_identifier(valeur) == valeur

    @pytest.mark.parametrize(
        "valeur",
        [
            "a; DROP TABLE t",          # injection par point-virgule
            "cat.schema",               # nom qualifié : à valider partie par partie
            "1_commence_par_un_chiffre",
            "avec espace",
            'guillemet"',
            "",
            "A" * 129,
        ],
    )
    def test_refuse_tout_le_reste(self, valeur: str) -> None:
        with pytest.raises(UnsafeIdentifierError):
            validate_identifier(valeur)

    def test_refuse_les_types_non_textuels(self) -> None:
        with pytest.raises(UnsafeIdentifierError):
            validate_identifier(None)  # type: ignore[arg-type]


class TestValidateIsoDate:
    def test_accepte_une_date_iso(self) -> None:
        assert validate_iso_date("2026-03-30") == "2026-03-30"

    @pytest.mark.parametrize("valeur", ["30/03/2026", "2026-3-30", "2026-03-30' OR '1'='1", ""])
    def test_refuse_les_autres_formes(self, valeur: str) -> None:
        with pytest.raises(UnsafeIdentifierError):
            validate_iso_date(valeur)


class TestValidateNumber:
    def test_convertit_un_nombre(self) -> None:
        assert float(validate_number(0.5)) == 0.5
        assert float(validate_number("2")) == 2.0

    @pytest.mark.parametrize("valeur", ["0.5; DROP TABLE t", float("nan"), float("inf"), None])
    def test_refuse_les_valeurs_non_finies(self, valeur: object) -> None:
        with pytest.raises(UnsafeIdentifierError):
            validate_number(valeur)


class TestRenderTemplate:
    def test_substitue_les_placeholders(self) -> None:
        rendu = render_template(
            "SELECT * FROM {catalog}.{schema}.t", {"catalog": "c", "schema": "s"}
        )
        assert rendu == "SELECT * FROM c.s.t"

    def test_signale_un_parametre_manquant(self) -> None:
        with pytest.raises(KeyError, match="schema"):
            render_template("{catalog}.{schema}", {"catalog": "c"})

    def test_laisse_les_accolades_inconnues_intactes(self) -> None:
        # Une accolade dans un commentaire ne doit pas faire échouer le rendu.
        rendu = render_template("-- struct {Nom} \nSELECT {catalog}", {"catalog": "c"})
        assert "{Nom}" in rendu and rendu.endswith("c")


class TestSplitStatements:
    def test_decoupe_sur_les_points_virgules(self) -> None:
        assert split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_ignore_les_points_virgules_dans_les_chaines(self) -> None:
        sql = "SELECT 'a;b' AS x; SELECT 2"
        assert split_statements(sql) == ["SELECT 'a;b' AS x", "SELECT 2"]

    def test_gere_les_quotes_echappees(self) -> None:
        sql = "COMMENT ON TABLE t IS 'l''écart; mesuré'; SELECT 1"
        instructions = split_statements(sql)
        assert len(instructions) == 2
        assert "l''écart; mesuré" in instructions[0]

    def test_ignore_les_points_virgules_en_commentaire(self) -> None:
        sql = "-- ceci ; n'est pas une fin\nSELECT 1; SELECT 2"
        assert len(split_statements(sql)) == 2

    def test_ignore_les_commentaires_bloc(self) -> None:
        assert split_statements("/* a; b */ SELECT 1;") == ["/* a; b */ SELECT 1"]

    def test_ecarte_les_fragments_purement_commentaires(self) -> None:
        assert split_statements("SELECT 1;\n-- fin du fichier\n") == ["SELECT 1"]

    def test_gere_les_identifiants_quotes(self) -> None:
        sql = 'SELECT "colonne;bizarre" FROM t; SELECT 2'
        assert len(split_statements(sql)) == 2


class TestFichiersSqlReels:
    """Les scripts livrés doivent se rendre et se découper sans erreur."""

    def test_tous_les_scripts_gold_sont_exploitables(self) -> None:
        from src.jobs.build_gold import list_sql_files

        params = {
            "catalog": "c", "schema": "s",
            "bronze_catalog": "bc", "bronze_schema": "bs",
            "silver_catalog": "sc", "silver_schema": "ss",
            "date_from": "2026-03-30", "seuil_conformite": "0.5",
            "company_predicate": "1 = 1",
        }
        fichiers = list_sql_files()
        assert len(fichiers) >= 9, "Le modèle gold doit compter au moins 9 scripts."
        for fichier in fichiers:
            instructions = split_statements(
                render_template(fichier.read_text(encoding="utf-8"), params)
            )
            assert instructions, f"{fichier.name} ne produit aucune instruction."
            for instruction in instructions:
                assert "{" not in instruction.split("--")[0], (
                    f"{fichier.name} : placeholder non substitué."
                )
