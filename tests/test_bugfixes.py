import os
import re

from api.mcp_nl2sql import _corriger_group_by_mssql, _generer_sql_generique, _connect
from api.orchestrateur_general import _pre_classifier
from api.vanna_training_neutral import valider_sql_dialecte, generer_sql_thread_safe, construire_exemples_entrainement


def test_preclass_top_clients_and_qualifiers():
    assert _pre_classifier("Top 5 clients par CA") == "TOP_CLIENTS"
    assert _pre_classifier("clients avec des impayés") == "NL2SQL_LIBRE"
    assert _pre_classifier("liste des clients") == "LISTE_CLIENTS"
    assert _pre_classifier("encours du fournisseur FOU001") == "NL2SQL_LIBRE"
    assert _pre_classifier("encours fournisseur") == "NL2SQL_LIBRE"


def test_group_by_fix_adds_missing_mssql_columns():
    original = os.environ.get("DB_DRIVER")
    try:
        os.environ["DB_DRIVER"] = "mssql"
        sql = "SELECT c.nom, SUM(x.montant) AS total FROM clients c GROUP BY c.code"
        fixed = _corriger_group_by_mssql(sql)
        assert "GROUP BY" in fixed.upper()
        assert "C.CODE" in fixed.upper()
        assert "C.NOM" in fixed.upper()
    finally:
        if original is None:
            os.environ.pop("DB_DRIVER", None)
        else:
            os.environ["DB_DRIVER"] = original


def test_filtered_client_question_does_not_trigger_generic_client_list_and_wrapper_is_active():
    original = os.environ.get("DB_DRIVER")
    try:
        os.environ["DB_DRIVER"] = "mssql"
        conn = _connect()
        assert type(conn).__name__ == "_ConnexionCorrigee"
        assert _generer_sql_generique("clients avec moins de 2 factures", "", conn) is None
        assert _generer_sql_generique("Quel client a le plus gros encours ?", "", conn) is None
        assert re.search(r"clients?.{0,50}(?:moins\s+de\s+\d+\s+(?:factures?|achats?|commandes?))", "clients avec moins de 2 factures", re.IGNORECASE)
    finally:
        if original is None:
            os.environ.pop("DB_DRIVER", None)
        else:
            os.environ["DB_DRIVER"] = original


def test_sql_validator_ignores_inline_comments_for_mssql():
    sql = "SELECT * FROM t -- commentaire\n WHERE x = 1"
    ok, score = valider_sql_dialecte(sql, True)
    assert ok is True
    assert score >= 0.55


def test_generer_sql_thread_safe_ignores_leading_comments_and_example_anti_join_pattern():
    class DummyVanna:
        def get_similar_question_sql(self, q, **kw):
            return []

        def generate_sql(self, question):
            return "-- Fournisseurs inactifs depuis 1 mois\nSELECT 1 AS ok"

    sql, score = generer_sql_thread_safe(DummyVanna(), "question", 2.0, True)
    assert sql == "SELECT 1 AS ok"
    assert score >= 0.55

    examples = construire_exemples_entrainement(True, table=lambda name: name, col=lambda name, col_name: col_name)
    for _, qsql in examples:
        assert "NOT EXISTS (SELECT 1" not in qsql.upper()


def test_ca_avec_periode_classification():
    # 1. Chiffre d'affaires total du mois dernier -> NL2SQL_LIBRE
    assert _pre_classifier("chiffre d'affaires total du mois dernier") == "NL2SQL_LIBRE"
    # 2. Compare le CA de ce mois-ci vs l'an dernier -> NL2SQL_LIBRE
    assert _pre_classifier("Compare le CA de ce mois-ci vs l'année dernière") == "NL2SQL_LIBRE"
    # 3. La moyenne du montant des factures par trimestre cette année -> NL2SQL_LIBRE
    assert _pre_classifier("la moyenne du montant des factures par trimestre cette année") == "NL2SQL_LIBRE"
    # 4. CA global (sans période) -> CA_GLOBAL
    assert _pre_classifier("chiffre d'affaires total") == "CA_GLOBAL"
    assert _pre_classifier("chiffre d'affaires global") == "CA_GLOBAL"
