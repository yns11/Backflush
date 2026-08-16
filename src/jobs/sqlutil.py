"""Outils de préparation SQL — sans dépendance Spark, donc testables en local.

Trois responsabilités :

* :func:`validate_identifier` — refuser tout identifiant SQL non sûr avant de
  l'injecter dans un template (les identifiants ne peuvent pas être passés en
  paramètre lié : la validation est la seule protection).
* :func:`render_template` — substituer les ``{placeholders}`` d'un fichier SQL.
* :func:`split_statements` — découper un fichier multi-instructions en respectant
  les chaînes littérales et les commentaires.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping

# Un identifiant Unity Catalog / Postgres sûr : lettres, chiffres, underscore.
# Volontairement plus strict que la grammaire réelle (pas de quotes, pas de
# points) : chaque partie du nom qualifié est validée séparément.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

# Une date ISO simple, seule forme acceptée pour les bornes d'historique.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UnsafeIdentifierError(ValueError):
    """Levée lorsqu'un identifiant destiné à être interpolé est refusé."""


def validate_identifier(value: str, *, label: str = "identifiant") -> str:
    """Retourne ``value`` s'il s'agit d'un identifiant SQL sûr, sinon lève.

    >>> validate_identifier("emotors_data_champions")
    'emotors_data_champions'
    >>> validate_identifier("a; DROP TABLE t")
    Traceback (most recent call last):
        ...
    src.jobs.sqlutil.UnsafeIdentifierError: ...
    """
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise UnsafeIdentifierError(
            f"{label} invalide : {value!r}. Attendu : [A-Za-z_][A-Za-z0-9_]* "
            f"(128 caractères maximum)."
        )
    return value


def validate_iso_date(value: str, *, label: str = "date") -> str:
    """Retourne ``value`` s'il s'agit d'une date ``AAAA-MM-JJ``, sinon lève."""
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise UnsafeIdentifierError(f"{label} invalide : {value!r}. Attendu : AAAA-MM-JJ.")
    return value


def validate_number(value: object, *, label: str = "nombre") -> str:
    """Retourne la représentation décimale de ``value`` si c'est un nombre fini."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:  # pragma: no cover - garde défensive
        raise UnsafeIdentifierError(f"{label} invalide : {value!r}.") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise UnsafeIdentifierError(f"{label} invalide : {value!r}.")
    return repr(number)


def render_template(sql: str, params: Mapping[str, str]) -> str:
    """Substitue les ``{clé}`` de ``sql`` par ``params[clé]``.

    Contrairement à :meth:`str.format`, les accolades non reconnues sont laissées
    telles quelles : un ``{`` isolé dans un commentaire ne fait pas échouer le
    rendu.

    :raises KeyError: si un placeholder connu du fichier n'est pas fourni.
    """
    placeholders = set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", sql))
    missing = placeholders - set(params)
    if missing:
        raise KeyError(
            "Paramètres manquants pour le rendu SQL : " + ", ".join(sorted(missing))
        )
    rendered = sql
    for key in placeholders:
        rendered = rendered.replace("{" + key + "}", str(params[key]))
    return rendered


def split_statements(sql: str) -> list[str]:
    """Découpe un script SQL en instructions.

    Gère les chaînes littérales ``'...'`` (avec échappement ``''``), les
    identifiants entre guillemets ``"..."``, les commentaires ``--`` et ``/* */``.
    Les commentaires sont conservés dans l'instruction produite (ils documentent
    la requête dans l'historique Databricks) mais n'influencent pas le découpage.
    Les fragments ne contenant que des commentaires sont écartés.
    """
    return [stmt for stmt in _iter_statements(sql) if _has_executable_content(stmt)]


def _has_executable_content(statement: str) -> bool:
    """Vrai si l'instruction contient autre chose que des commentaires."""
    without_block = re.sub(r"/\*.*?\*/", " ", statement, flags=re.DOTALL)
    without_line = re.sub(r"--[^\n]*", " ", without_block)
    return bool(without_line.strip())


def _iter_statements(sql: str) -> Iterator[str]:
    buffer: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        # --- commentaire de fin de ligne ---
        if char == "-" and sql.startswith("--", index):
            end = sql.find("\n", index)
            end = length if end == -1 else end
            buffer.append(sql[index:end])
            index = end
            continue

        # --- commentaire bloc ---
        if char == "/" and sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = length if end == -1 else end + 2
            buffer.append(sql[index:end])
            index = end
            continue

        # --- chaîne littérale ou identifiant quoté ---
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            while index < length:
                if sql[index] == quote:
                    # Doublement du quote = quote échappé, la chaîne continue.
                    if index + 1 < length and sql[index + 1] == quote:
                        buffer.append(quote * 2)
                        index += 2
                        continue
                    buffer.append(quote)
                    index += 1
                    break
                buffer.append(sql[index])
                index += 1
            continue

        # --- fin d'instruction ---
        if char == ";":
            yield "".join(buffer).strip()
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    yield "".join(buffer).strip()
