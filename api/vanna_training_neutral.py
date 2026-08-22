"""
vanna_training_neutral.py
================================================================================
Drop-in replacement pieces for the Vanna training logic in
orchestrateur_general.py. Fixes:

  1. Few-shot SQL examples are now dialect-aware (SQLite vs MSSQL), using the
     same STRFTIME/FORMAT, DATEDIFF/JULIANDAY, DATE('now')/GETDATE() switching
     that nl2sql_server.py already applies to the DDL and pattern-matcher SQL.
     Previously the ~150 few-shot examples were hardcoded SQLite syntax even
     when DB_DRIVER=mssql, silently teaching Vanna to emit broken SQL on MSSQL.

  2. The (question, sql) example list is deduplicated (order-preserving).
     The original list repeated the same handful of examples 20-40x, which
     bloats the Chroma vector store and skews retrieval without adding any
     signal.

  3. `_vanna_generer_sql`'s monkeypatch of `get_similar_question_sql` is now
     restored in a `finally` block, so a raised exception during
     `generate_sql()` can no longer leave the patch applied permanently
     (which would stack wrappers on every subsequent call).

  4. `_valider_sql` and the generated-SQL parse check are dialect-aware
     (dialect="tsql" under MSSQL, "sqlite" otherwise) instead of always
     assuming sqlite.

  5. A content hash lets you auto-retrain Vanna whenever the DDL / doc string
     / example set actually changes, instead of only training once when the
     Chroma store is empty (today, editing db_config.json or the examples
     silently has zero effect until someone manually types 'vanna_retrain').

HOW TO INTEGRATE
----------------
In orchestrateur_general.py:

  - Replace the body of `_vanna_entrainer_schema(vn)` (the non-string branch)
    with a call to `construire_exemples_entrainement(_is_mssql())` for the
    `exemples` list, keep your existing DDL/documentation train() calls, and
    add the hash check from `doit_reentrainer(...)` before deciding whether
    to call `_vanna_entrainer_schema` at all.

  - Replace `_vanna_generer_sql`'s inner `_run()` with `generer_sql_thread_safe`
    below (same signature/behavior, just the try/finally fix + dialect-aware
    sqlglot validation).

  - Replace `_valider_sql` with `valider_sql_dialecte` below.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading


def _nettoyer_sql_commentaires(sql: str | None) -> str:
    """Retire les commentaires en tête et les blocks commentés pour
    ne pas faire échouer les checks de validation sur `-- comment`.
    """
    if not sql:
        return ""
    sql_clean = re.sub(r"^\s*(?:--[^\n]*\n)+", "", sql, flags=re.MULTILINE)
    sql_clean = re.sub(r"^\s*(?:/\*.*?\*/\s*)+", "", sql_clean, flags=re.DOTALL)
    return sql_clean.strip()


# ─────────────────────────────────────────────────────────────────────
# 1. HELPERS SQL NEUTRES — miroir de nl2sql_server.py, paramétrés par
#    un booléen plutôt que par une lecture d'env, pour rester testables
#    et découplés de l'import de nl2sql_server.
# ─────────────────────────────────────────────────────────────────────
def _fmt_mois(col_expr: str, mssql: bool) -> str:
    if mssql:
        return f"FORMAT({col_expr}, 'yyyy-MM')"
    return f"STRFTIME('%Y-%m', {col_expr})"


def _fmt_annee(col_expr: str, mssql: bool) -> str:
    if mssql:
        return f"FORMAT({col_expr}, 'yyyy')"
    return f"STRFTIME('%Y', {col_expr})"


def _now_date(mssql: bool) -> str:
    return "GETDATE()" if mssql else "'now'"


def _diff_jours(date_fin: str, date_debut: str, mssql: bool) -> str:
    if mssql:
        return f"DATEDIFF(day, {date_debut}, {date_fin})"
    return f"(JULIANDAY({date_fin}) - JULIANDAY({date_debut}))"


def _date_sub_days(n_days: int, mssql: bool) -> str:
    if mssql:
        return f"DATEADD(day, -{n_days}, GETDATE())"
    return f"DATE('now', '-{n_days} days')"


def _limit_clause(n: int, mssql: bool) -> str:
    """Trailing clause. Empty under MSSQL (use TOP instead)."""
    return "" if mssql else f"LIMIT {n}"


def _top_prefix(n: int, mssql: bool) -> str:
    return f"TOP {n} " if mssql else ""


def _year_current(mssql: bool) -> str:
    """Returns SQL expression for the current year as a 4-digit integer."""
    if mssql:
        return "YEAR(GETDATE())"
    return "CAST(strftime('%Y', 'now') AS INTEGER)"


# ─────────────────────────────────────────────────────────────────────
# 2. CONSTRUCTION DES EXEMPLES — dialect-aware, dédupliqués
# ─────────────────────────────────────────────────────────────────────
def construire_exemples_entrainement(
    mssql: bool,
    *,
    table,   # callable: table('logical_name') -> physical name
    col,     # callable: col('logical_name', 'logical_col') -> physical name
) -> list[tuple[str, str]]:
    """
    Retourne une liste dédupliquée de (question, sql) pour l'entraînement
    Vanna, générée avec les mêmes conventions neutres (table()/col()) et
    les mêmes helpers de dialecte que le reste du projet.

    `table` et `col` doivent être les fonctions de adaptation.db_adapter,
    identiques à celles déjà utilisées ailleurs dans orchestrateur_general.py
    et nl2sql_server.py.
    """
    te   = table('doc_entete');            ce_p  = col('doc_entete', 'piece')
    ce_d = col('doc_entete', 'date');      ce_ti = col('doc_entete', 'code_tiers')
    ce_ty = col('doc_entete', 'type');     ce_do = col('doc_entete', 'domaine')
    tl   = table('doc_ligne');             cl_p  = col('doc_ligne', 'piece')
    cl_q = col('doc_ligne', 'qte');        cl_pu = col('doc_ligne', 'prix_unitaire')
    cl_a = col('doc_ligne', 'ref_article')
    tc   = table('clients_fournisseurs');  cc_id = col('clients_fournisseurs', 'code')
    cc_n = col('clients_fournisseurs', 'nom')
    cc_ty = col('clients_fournisseurs', 'type_tiers')
    cc_e  = col('clients_fournisseurs', 'encours')
    cc_sommeil = col('clients_fournisseurs', 'sommeil')
    ta   = table('articles');              ca_r  = col('articles', 'ref')
    ca_d = col('articles', 'designation')
    ca_pv = col('articles', 'prix_vente'); ca_pa = col('articles', 'prix_achat')
    ts   = table('stock');                 cs_r  = col('stock', 'ref')
    cs_q = col('stock', 'qte_stock');      cs_c  = col('stock', 'qte_commande')
    tr   = table('reglements');            cr_p  = col('reglements', 'piece')
    cr_dr = col('reglements', 'date_reglement')

    mois_expr    = _fmt_mois(f"e.{ce_d}", mssql)
    now_expr     = _now_date(mssql)
    limit12      = _limit_clause(12, mssql)
    top12_prefix = _top_prefix(12, mssql)

    impayes_join = (
        f"AND r.{cr_p} IS NULL"
    )

    exemples: list[tuple[str, str]] = [
        ("clients avec un encours superieur a 5000",
         f"SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_du "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND r.{cr_p} IS NULL "
         f"GROUP BY e.{ce_ti}, c.{cc_n} HAVING SUM(l.{cl_q}*l.{cl_pu}) > 5000 ORDER BY montant_du DESC"),

        ("liste tous les articles du catalogue",
         f"SELECT a.{ca_r}, a.{ca_d}, a.{ca_pv}, COALESCE(s.{cs_q},0) AS stock "
         f"FROM {ta} a LEFT JOIN {ts} s ON a.{ca_r}=s.{cs_r} ORDER BY a.{ca_r}"),

        ("top 5 clients par chiffre d affaires",
         f"SELECT {top12_prefix}e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS ca_total "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"GROUP BY e.{ce_ti}, c.{cc_n} ORDER BY ca_total DESC, e.{ce_ti} ASC"),

        ("top 5 clients avec nombre de commandes et derniere date achat",
         f"SELECT {top12_prefix}c.{cc_id}, c.{cc_n}, cmd.nb_commandes, ach.derniere_date "
         f"FROM {tc} c "
         f"LEFT JOIN (SELECT {ce_ti}, COUNT(*) AS nb_commandes FROM {te} "
         f"           WHERE {ce_do}=0 AND {ce_ty}=1 GROUP BY {ce_ti}) cmd ON cmd.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN (SELECT {ce_ti}, MAX({ce_d}) AS derniere_date FROM {te} "
         f"           WHERE {ce_do}=0 AND {ce_ty}=6 GROUP BY {ce_ti}) ach ON ach.{ce_ti}=c.{cc_id} "
         f"ORDER BY cmd.nb_commandes DESC, c.{cc_id} ASC"),

        ("factures impayees non reglees",
         f"SELECT e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d}, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND r.{cr_p} IS NULL "
         f"GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC"),

        ("factures en souffrance",
         f"SELECT e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d}, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND r.{cr_p} IS NULL "
         f"GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC"),

        ("clients avec plus de 6 factures impayees",
         f"SELECT e.{ce_ti}, c.{cc_n}, COUNT(DISTINCT e.{ce_p}) AS nb_factures, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS total_impaye "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 {impayes_join} "
         f"GROUP BY e.{ce_ti}, c.{cc_n} HAVING COUNT(DISTINCT e.{ce_p}) > 6 ORDER BY total_impaye DESC"),

        ("ca mensuel 12 derniers mois",
         f"SELECT {top12_prefix}{mois_expr} AS mois, COUNT(DISTINCT e.{ce_p}) AS nb_factures, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS ca_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"AND e.{ce_d} >= {_date_sub_days(365, mssql)} "
         f"GROUP BY {mois_expr} ORDER BY mois DESC{limit12}"),

        ("factures impayees par mois",
         f"SELECT {mois_expr} AS mois, COUNT(DISTINCT e.{ce_p}) AS nb_factures, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS montant_impaye "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 {impayes_join} "
         f"GROUP BY {mois_expr} ORDER BY mois DESC"),

        ("articles en rupture de stock",
         f"SELECT a.{ca_r}, a.{ca_d}, COALESCE(s.{cs_q},0) AS stock "
         f"FROM {ta} a LEFT JOIN {ts} s ON a.{ca_r}=s.{cs_r} "
         f"WHERE COALESCE(s.{cs_q},0)<=0 ORDER BY a.{ca_r}"),

        ("chiffre d affaires global total",
         f"SELECT COUNT(DISTINCT e.{ce_p}) AS nb_factures, COUNT(DISTINCT e.{ce_ti}) AS nb_clients, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS ca_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0"),

        ("clients bloqués",
         f"SELECT {cc_id}, {cc_n}, {cc_e} FROM {tc} "
         f"WHERE {cc_ty}=0 AND {cc_sommeil}=1 ORDER BY {cc_n}"),

        ("stock de l article ECRAN4K",
         f"SELECT a.{ca_r}, a.{ca_d}, COALESCE(s.{cs_q},0) AS stock, COALESCE(s.{cs_c},0) AS en_commande "
         f"FROM {ta} a LEFT JOIN {ts} s ON a.{ca_r}=s.{cs_r} "
         f"WHERE UPPER(a.{ca_r})='ECRAN4K'"),

        ("factures du client CLI001",
         f"SELECT e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht, "
         f"CASE WHEN EXISTS (SELECT 1 FROM {tr} r WHERE r.{cr_p} = e.{ce_p}) "
         f"THEN 'RÉGLÉE' ELSE 'EN ATTENTE' END AS statut "
         f"FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_ti}='CLI001' "
         f"GROUP BY e.{ce_p}, e.{ce_d} ORDER BY e.{ce_d} DESC"),

        ("marge brute par article rentabilite",
         f"SELECT l.{cl_a}, a.{ca_d}, SUM(l.{cl_q}*l.{cl_pu}) AS ca_vente, "
         f"SUM(l.{cl_q}*a.{ca_pa}) AS cout_achat, "
         f"SUM(l.{cl_q}*l.{cl_pu})-SUM(l.{cl_q}*a.{ca_pa}) AS marge_brute "
         f"FROM {tl} l JOIN {te} e ON l.{cl_p}=e.{ce_p} "
         f"LEFT JOIN {ta} a ON l.{cl_a}=a.{ca_r} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"GROUP BY l.{cl_a}, a.{ca_d} ORDER BY marge_brute DESC"),

        ("encours client CLI002",
         f"SELECT c.{cc_id}, c.{cc_n}, COALESCE(c.{cc_e},0) AS encours_autorise, "
         f"COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS encours_utilise "
         f"FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 "
         f"LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE c.{cc_id}='CLI002' AND r.{cr_p} IS NULL "
         f"GROUP BY c.{cc_id}, c.{cc_n}, c.{cc_e}"),

        ("clients inactifs depuis 6 mois",
         f"SELECT c.{cc_id}, c.{cc_n}, MAX(e.{ce_d}) AS derniere_commande "
         f"FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 "
         f"WHERE c.{cc_ty}=0 GROUP BY c.{cc_id}, c.{cc_n} "
         f"HAVING MAX(e.{ce_d}) IS NULL OR MAX(e.{ce_d}) < {_date_sub_days(180, mssql)} "
         f"ORDER BY derniere_commande ASC"),

        ("liste des bons de livraison du client CLI001",
         f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=3 AND e.{ce_do}=0 AND e.{ce_ti}='CLI001' "
         f"GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti} ORDER BY e.{ce_d} DESC"),

        ("clients ayant des factures superieures a 1000",
         f"SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS total_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"GROUP BY e.{ce_ti}, c.{cc_n} HAVING SUM(l.{cl_q}*l.{cl_pu}) > 1000 ORDER BY total_ht DESC"),

        ("tous les bons de livraison",
         f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=3 AND e.{ce_do}=0 "
         f"GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n} ORDER BY e.{ce_d} DESC"),

        ("factures fournisseur",
         f"SELECT e.{ce_ti}, c.{cc_n}, e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=16 AND e.{ce_do}=1 AND c.{cc_ty}=1 "
         f"GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC"),

        ("bons de reception fournisseur",
         f"SELECT e.{ce_ti}, c.{cc_n}, e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=13 AND e.{ce_do}=1 AND c.{cc_ty}=1 "
         f"GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC"),

        ("clients qui ont passe plus de 3 commandes",
         f"SELECT c.{cc_id}, c.{cc_n}, COUNT(DISTINCT e.{ce_p}) AS nb_factures, "
         f"COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS ca_total "
         f"FROM {tc} c JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE c.{cc_ty}=0 GROUP BY c.{cc_id}, c.{cc_n} "
         f"HAVING COUNT(DISTINCT e.{ce_p}) > 3 ORDER BY nb_factures DESC"),

        ("articles dont le prix de vente depasse 500",
         f"SELECT {ca_r}, {ca_d}, {ca_pv} AS prix_vente, {ca_pa} AS prix_achat, "
         f"ROUND({ca_pv} - {ca_pa}, 2) AS marge "
         f"FROM {ta} WHERE {ca_pv} > 500 ORDER BY {ca_pv} DESC"),

        ("factures du mois de juin",
         f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n}, "
         f"COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS montant_ht "
         f"FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         + (
             f"AND YEAR(e.{ce_d})=YEAR(GETDATE()) AND MONTH(e.{ce_d})=6 "
             if mssql else
             f"AND strftime('%Y-%m', e.{ce_d})=strftime('%Y', 'now')||'-06' "
         ) +
         f"GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n} ORDER BY e.{ce_d} DESC"),

        ("clients avec moins de 2 factures",
         f"SELECT c.{cc_id}, c.{cc_n}, COUNT(DISTINCT e.{ce_p}) AS nb_factures "
         f"FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"WHERE c.{cc_ty}=0 GROUP BY c.{cc_id}, c.{cc_n} "
         f"HAVING COUNT(DISTINCT e.{ce_p}) < 2 ORDER BY nb_factures ASC"),

        ("liste des fournisseurs",
         f"SELECT {cc_id}, {cc_n} FROM {tc} WHERE {cc_ty} = 1 ORDER BY {cc_n}"),

        ("top 5 fournisseurs par montant d achat",
         f"SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS total_achat "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=16 AND e.{ce_do}=1 AND c.{cc_ty}=1 "
         f"GROUP BY e.{ce_ti}, c.{cc_n} ORDER BY total_achat DESC"),

        ("fournisseurs bloques",
         f"SELECT {cc_id}, {cc_n}, {cc_e} FROM {tc} "
         f"WHERE {cc_ty}=1 AND {cc_sommeil}=1 ORDER BY {cc_n}"),

        ("bons de commande fournisseur",
         f"SELECT e.{ce_ti}, c.{cc_n}, e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=11 AND e.{ce_do}=1 AND c.{cc_ty}=1 "
         f"GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC"),

        ("fournisseurs inactifs depuis 6 mois",
         f"SELECT c.{cc_id}, c.{cc_n}, MAX(e.{ce_d}) AS derniere_commande "
         f"FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=11 AND e.{ce_do}=1 "
         f"WHERE c.{cc_ty}=1 GROUP BY c.{cc_id}, c.{cc_n} "
         f"HAVING MAX(e.{ce_d}) IS NULL OR MAX(e.{ce_d}) < {_date_sub_days(180, mssql)} "
         f"ORDER BY derniere_commande ASC"),

        ("encours fournisseur FOUR001",
         f"SELECT c.{cc_id}, c.{cc_n}, COALESCE(c.{cc_e},0) AS encours_autorise, "
         f"COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS encours_utilise "
         f"FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=16 AND e.{ce_do}=1 "
         f"LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE c.{cc_id}='FOUR001' AND r.{cr_p} IS NULL "
         f"GROUP BY c.{cc_id}, c.{cc_n}, c.{cc_e}"),

        ("fournisseurs ayant des factures superieures a 5000",
         f"SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS total_ht "
         f"FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"WHERE e.{ce_ty}=16 AND e.{ce_do}=1 "
         f"GROUP BY e.{ce_ti}, c.{cc_n} HAVING SUM(l.{cl_q}*l.{cl_pu}) > 5000 ORDER BY total_ht DESC"),

        ("liste des ordres de fabrication OF",
         f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, SUM(l.{cl_q}) AS qte_totale "
         f"FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=25 AND e.{ce_do}=2 "
         f"GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti} ORDER BY e.{ce_d} DESC"),

        ("liste des bons de fabrication BF",
         f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, SUM(l.{cl_q}) AS qte_totale "
         f"FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=26 AND e.{ce_do}=2 "
         f"GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti} ORDER BY e.{ce_d} DESC"),

        ("liste des factures de vente",
         f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n}, "
         f"SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht "
         f"FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n} ORDER BY e.{ce_d} DESC"),

        ("delai moyen de paiement dso",
         f"SELECT e.{ce_ti}, c.{cc_n}, "
         f"ROUND(AVG(CASE WHEN r.{cr_dr} IS NOT NULL "
         f"THEN {_diff_jours(f'r.{cr_dr}', f'e.{ce_d}', mssql)} "
         f"ELSE {_diff_jours(now_expr, f'e.{ce_d}', mssql)} END), 1) AS dso_jours "
         f"FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} "
         f"LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} "
         f"WHERE e.{ce_ty}=6 AND e.{ce_do}=0 "
         f"GROUP BY e.{ce_ti}, c.{cc_n} ORDER BY dso_jours DESC"),
    ]

    # Déduplication order-preserving (clé = question normalisée)
    vus: set[str] = set()
    dedupliques: list[tuple[str, str]] = []
    for question, sql in exemples:
        cle = question.strip().lower()
        if cle in vus:
            continue
        vus.add(cle)
        dedupliques.append((question, sql))
    return dedupliques


# ─────────────────────────────────────────────────────────────────────
# 3. GÉNÉRATION SQL — patch restauré en finally, dialecte correct
# ─────────────────────────────────────────────────────────────────────
def _valider_colonnes_connues(sql: str, colonnes_valides: set[str], mssql: bool = False) -> bool:
    """Phase 3 : Vérifie que chaque colonne référencée dans le SQL existe
    dans l'ensemble des colonnes physiques connues (construit via col() sur
    toutes les tables logiques).

    Si une colonne inconnue est référencée, le SQL est probablement halluciné
    ou généré pour un schéma différent → score forcé à 0 par l'appelant.

    Retourne True si toutes les colonnes sont connues (ou si sqlglot n'est
    pas disponible, pour ne pas bloquer le fallback).
    """
    if not sql or not colonnes_valides:
        return True  # pas de base de validation → on laisse passer
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp
        sql_nettoye = _nettoyer_sql_commentaires(sql)
        dialect = "tsql" if mssql else "sqlite"
        tree = sqlglot.parse_one(sql_nettoye, dialect=dialect)
        colonnes_valides_upper = {c.upper() for c in colonnes_valides}
        
        # Collecter TOUS les alias produits par la requête : CTE + colonnes SELECT
        alias_connus = set()
        for cte in tree.find_all(sqlglot_exp.CTE):
            if getattr(cte, 'alias', None):
                alias_connus.add(cte.alias.upper())
        for select_expr in tree.find_all(sqlglot_exp.Select):
            for projection in select_expr.expressions:
                if isinstance(projection, sqlglot_exp.Alias):
                    alias_connus.add(projection.alias.upper())
        
        # Alias de table (ex: F_DOCENTETE AS e)
        alias_tables = set()
        for table_expr in tree.find_all(sqlglot_exp.Table):
            if getattr(table_expr, 'alias', None):
                alias_tables.add(table_expr.alias.upper())

        for col_node in tree.find_all(sqlglot_exp.Column):
            col_name = col_node.name
            table_ref = col_node.table.upper() if col_node.table else ""
            if not col_name:
                continue
            if col_name == "*" or col_name.upper() in ("NULL", "TRUE", "FALSE"):
                continue
            # Ignorer si c'est un alias de sortie (ORDER BY encours_ht, etc.)
            if col_name.upper() in alias_connus:
                continue
            # Ignorer si référencé via un alias de CTE (i.amount)
            if table_ref in alias_connus:
                continue
            if col_name.upper() not in colonnes_valides_upper:
                print(f"   ⚠️  [Vanna Phase 3] Colonne inconnue dans le SQL : '{col_name}' "
                      f"→ score forcé à 0.0")
                return False
        return True
    except ImportError:
        return True  # sqlglot non disponible → validation ignorée
    except Exception:
        return True  # erreur de parsing → on laisse la validation de score standard décider


def _construire_colonnes_valides(table_fn, col_fn) -> set[str]:
    """Construit l'ensemble des noms physiques de colonnes connus pour le schéma actuel.

    Utilise table()/col() de db_adapter pour rester neutre vis-à-vis du schéma.
    """
    tables_logiques = {
        "clients_fournisseurs": ["code", "nom", "type_tiers", "sommeil", "encours", "encours_max", "validite"],
        "articles": ["ref", "designation", "prix_achat", "prix_vente", "type_article"],
        "stock": ["ref", "qte_stock", "qte_commande"],
        "doc_entete": ["piece", "domaine", "type", "date", "reference", "code_tiers"],
        "doc_ligne": ["ligne", "piece", "ref_article", "qte", "prix_unitaire"],
        "reglements": ["piece", "mode_paiement", "montant", "date_reglement"],
        "nomenclature": ["ref_pf", "ref_mp", "qte"],
    }
    colonnes: set[str] = {
        "cbMarq", "cbDO_Piece", "DE_No", "cbCT_Num", "cbAR_Ref", 
        "cbModification", "cbCreateur", "cbProt"
    }
    for table_logique, champs in tables_logiques.items():
        try:
            for champ in champs:
                colonnes.add(col_fn(table_logique, champ))
        except (KeyError, Exception):
            pass
    return colonnes


# ── Phase 5 — Event de concurrence : une seule génération active à la fois ──
_vanna_generation_en_cours: "threading.Event | None" = None


def _get_generation_event() -> "threading.Event":
    """Lazy-init de l'Event threading (évite l'import circulaire au module load)."""
    global _vanna_generation_en_cours
    if _vanna_generation_en_cours is None:
        _vanna_generation_en_cours = threading.Event()
    return _vanna_generation_en_cours


def generer_sql_thread_safe(
    vn,
    question: str,
    timeout_s: float,
    mssql: bool,
    *,
    table=None,  # optionnel : callable table() de db_adapter pour Phase 3
    col=None,    # optionnel : callable col() de db_adapter pour Phase 3
    _retry=False,
) -> tuple[str | None, float]:
    """
    Remplace le corps de `_vanna_generer_sql`. Même contrat : retourne
    (sql | None, score). Corrige le bug où une exception dans
    `generate_sql()` laissait `get_similar_question_sql` monkeypatché
    en permanence.

    Phase 5 : si une génération précédente est encore active (timeout en cours),
    retourne immédiatement (None, 0.0) plutôt que de patcher get_similar_question_sql
    en concurrence.
    """
    if vn is None:
        return None, 0.0

    # ── Phase 5 : vérification concurrence ──
    gen_event = _get_generation_event()
    if gen_event.is_set():
        print("   ⚠️  [Vanna Phase 5] Génération précédente encore active → fallback patterns")
        return None, 0.0

    result_container: list = [None, None, 0]  # sql, exception, nb_similaires

    def _run():
        original_get = getattr(vn, "get_similar_question_sql", None)

        def _get_limited(q, **kw):
            try:
                results = original_get(q, **kw)
            except Exception as e:
                print(f"   ⚠️  [Vanna] get_similar_question_sql a échoué : {e}")
                return []
            if results is None:
                return []
            if isinstance(results, list):
                results = [r for r in results if isinstance(r, dict) and r.get("question") and r.get("sql")]
            nb = len(results) if results else 0
            result_container[2] = nb
            if nb == 0:
                print(f"   ⚠️  [Vanna] Aucun exemple similaire trouvé pour : '{q}'")
            return results[:5] if results else []

        try:
            if original_get:
                vn.get_similar_question_sql = _get_limited
            sql = vn.generate_sql(question)
            result_container[0] = sql
        except Exception as e:
            result_container[1] = e
        finally:
            # Restauré même si generate_sql() a levé une exception —
            # sinon le patch reste actif et s'empile au prochain appel.
            if original_get:
                vn.get_similar_question_sql = original_get
            # Phase 5 : signal que la génération est terminée
            gen_event.clear()

    gen_event.set()  # Phase 5 : marque la génération comme active
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        print(f"   ⚠️  [Vanna] Timeout {timeout_s}s → fallback patterns")
        # NOTE : le thread daemon continue en arrière-plan mais gen_event
        # sera cleared par son finally lorsqu'il se terminera naturellement.
        # Entre-temps, les nouveaux appels seront rejetés par la garde-fou.
        return None, 0.0

    if result_container[1] is not None:
        print(f"   ⚠️  [Vanna] {result_container[1]}")
        return None, 0.0

    sql = result_container[0]
    nb_similaires = result_container[2]
    sql_check = _nettoyer_sql_commentaires(sql)

    if not sql_check or not sql_check.upper().startswith("SELECT"):
        return None, 0.0

    # ── Phase 3 : validation colonnes connues ──
    if table is not None and col is not None:
        try:
            colonnes_valides = _construire_colonnes_valides(table, col)
            if colonnes_valides and not _valider_colonnes_connues(sql_check, colonnes_valides, mssql=mssql):
                if not _retry:
                    print(f"   🔄 [Vanna] Phase 3 échouée, tentative de correction automatique (retry=1)...")
                    question_corrigee = f"{question} (attention: n'utilise que les colonnes physiques du schéma, pas d'alias inventés dans une clause différente)"
                    return generer_sql_thread_safe(vn, question_corrigee, timeout_s, mssql, table=table, col=col, _retry=True)
                return None, 0.0
        except Exception as e:
            print(f"   ⚠️  [Vanna Phase 3] Validation colonnes échouée (ignorée) : {e}")

    ok, score = valider_sql_dialecte(sql_check, mssql, nb_similaires)
    return sql_check.strip(), score


def valider_sql_dialecte(sql: str, mssql: bool, nb_similaires: int = 0) -> tuple[bool, float]:
    """
    Remplace `_valider_sql`. Utilise le bon dialecte sqlglot selon la cible,
    et calcule un score composite. Retourne (parse_ok, score).

    Phase 3 : score plancher relevé de 0.55 → 0.65 pour réduire les faux
    positifs (SQL syntaxiquement valide mais sémantiquement incorrect).
    """
    dialect = "tsql" if mssql else "sqlite"
    try:
        import sqlglot
        sql_nettoye = _nettoyer_sql_commentaires(sql)
        sqlglot.parse_one(sql_nettoye, dialect=dialect)
        # Phase 3 : score plancher 0.65 (était 0.55) — plus conservateur,
        # à ajuster via SEUIL_CONFIANCE en fonction des logs vanna_candidates.jsonl
        score = 0.65 + min(0.30, nb_similaires * 0.10)
        return True, score
    except ImportError:
        return True, 0.35
    except Exception as e:
        print(f"   ❌ [Vanna] Erreur parsing SQL ({dialect}): {e}")
        return False, 0.35


# ─────────────────────────────────────────────────────────────────────
# 4. RETRAIN CONDITIONNEL — hash du contenu d'entraînement
# ─────────────────────────────────────────────────────────────────────
_HASH_STORE_KEY = "vanna_training_content_hash"


def calculer_hash_entrainement(
    ddl_statements: list[str],
    documentation: str,
    exemples: list[tuple[str, str]],
) -> str:
    """Hash stable du contenu qui doit déclencher un ré-entraînement s'il change."""
    payload = json.dumps(
        {"ddl": ddl_statements, "doc": documentation, "exemples": exemples},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def doit_reentrainer(
    vn,
    hash_actuel: str,
    chemin_hash_file: str = "./vanna_erp_db/.training_hash",
) -> bool:
    """
    True si le hash stocké diffère de hash_actuel (ou n'existe pas).
    Écrit le nouveau hash après appel si le caller décide de ré-entraîner
    (voir `marquer_entrainement_fait`).
    """
    try:
        with open(chemin_hash_file, "r", encoding="utf-8") as f:
            hash_stocke = f.read().strip()
        return hash_stocke != hash_actuel
    except FileNotFoundError:
        return True


def marquer_entrainement_fait(
    hash_actuel: str,
    chemin_hash_file: str = "./vanna_erp_db/.training_hash",
) -> None:
    import os
    os.makedirs(os.path.dirname(chemin_hash_file) or ".", exist_ok=True)
    with open(chemin_hash_file, "w", encoding="utf-8") as f:
        f.write(hash_actuel)
