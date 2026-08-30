"""
Cette fonctionnalité est utilisée pour formater les requêtes SQL en fonction du type de base de données utilisée, MySQL ou Microsoft SQL Server. Elle fournit des fonctions d'aide pour la génération de requêtes SQL standardisées.
"""
from __future__ import annotations
import hashlib
import json
import re
import threading

def _nettoyer_sql_commentaires(sql: str | None) -> str:
    """
Supprime les commentaires SQL multilineurs présents dans une chaîne de caractères SQL.
"""
    if not sql:
        return ''
    sql_clean = re.sub('^\\s*(?:--[^\\n]*\\n)+', '', sql, flags=re.MULTILINE)
    sql_clean = re.sub('^\\s*(?:/\\*.*?\\*/\\s*)+', '', sql_clean, flags=re.DOTALL)
    return sql_clean.strip()

def _fmt_mois(col_expr: str, mssql: bool) -> str:
    """
Cette fonction formate une expression de colonne en date sous un format spécifique, selon la plateforme (MSSQL ou pas).
"""
    if mssql:
        return f"FORMAT({col_expr}, 'yyyy-MM')"
    return f"STRFTIME('%Y-%m', {col_expr})"

def _premier_jour_mois_courant(mssql: bool) -> str:
    """
Renvoie la date du premier jour du mois en cours sous forme de code SQL.
"""
    if mssql:
        return 'DATEADD(month, DATEDIFF(month,0,GETDATE()), 0)'
    return "DATE('now','start of month')"

def _premier_jour_mois_dernier(mssql: bool) -> str:
    """
Cette fonction retourne la date du premier jour du mois qui précède le mois actuel dans une requête SQL.
"""
    if mssql:
        return 'DATEADD(month, DATEDIFF(month,0,GETDATE())-1, 0)'
    return "DATE('now','start of month','-1 month')"

def _premier_jour_semaine_courante(mssql: bool) -> str:
    """
Cette fonction retourne la date du premier jour de la semaine courante en fonction du système de bases de données utilisé.
"""
    if mssql:
        return 'DATEADD(day, -(DATEPART(weekday,GETDATE())+5)%7, CAST(GETDATE() AS DATE))'
    return "DATE('now','weekday 1','-7 days')"

def _fmt_annee(col_expr: str, mssql: bool) -> str:
    """
Cette fonction génère une expression SQL pour extraire l'année d'une date.
"""
    if mssql:
        return f"FORMAT({col_expr}, 'yyyy')"
    return f"STRFTIME('%Y', {col_expr})"

def _now_date(mssql: bool) -> str:
    """
Récupérer la date et l'heure actuelle sous forme de chaîne selon le système de base de données utilisé.
"""
    return 'GETDATE()' if mssql else "'now'"

def _diff_jours(date_fin: str, date_debut: str, mssql: bool) -> str:
    """
Calcule la différence entre deux dates en fonction du système de gestion de base de données utilisé.
"""
    if mssql:
        return f'DATEDIFF(day, {date_debut}, {date_fin})'
    return f'(JULIANDAY({date_fin}) - JULIANDAY({date_debut}))'

def _date_sub_days(n_days: int, mssql: bool) -> str:
    """
Cette fonction retourne une chaîne de caractères qui représente une expression SQL pour soustraire un nombre de jours à la date actuelle.
"""
    if mssql:
        return f'DATEADD(day, -{n_days}, GETDATE())'
    return f"DATE('now', '-{n_days} days')"

def _date_add_days(n_days: int, mssql: bool) -> str:
    """
Ajout d'une date en fonction d'un nombre de jours spécifique.
"""
    if mssql:
        return f'DATEADD(day, {n_days}, GETDATE())'
    return f"DATE('now', '+{n_days} days')"

def _limit_clause(n: int, mssql: bool) -> str:
    """
Limite le nombre de résultats retournés dans une requête SQL.
"""
    return '' if mssql else f'LIMIT {n}'

def _top_prefix(n: int, mssql: bool) -> str:
    """
Fonction qui génère la chaîne de caractères 'TOP <n>' si la base de données est Microsoft SQL Server, sinon retourne une chaîne vide.
"""
    return f'TOP {n} ' if mssql else ''

def _year_current(mssql: bool) -> str:
    """
Renvoie la fonction SQL pour obtenir la valeur de l'année courante en fonction du système de bases de données utilisé.
"""
    if mssql:
        return 'YEAR(GETDATE())'
    return "CAST(strftime('%Y', 'now') AS INTEGER)"

def construire_exemples_entrainement(mssql: bool, *, table, col) -> list[tuple[str, str]]:
    """
Créer des exemples d'entrainement pour une base de données.
"""
    te = table('doc_entete')
    ce_p = col('doc_entete', 'piece')
    ce_d = col('doc_entete', 'date')
    ce_ti = col('doc_entete', 'code_tiers')
    ce_ty = col('doc_entete', 'type')
    ce_do = col('doc_entete', 'domaine')
    tl = table('doc_ligne')
    cl_p = col('doc_ligne', 'piece')
    cl_q = col('doc_ligne', 'qte')
    cl_pu = col('doc_ligne', 'prix_unitaire')
    cl_a = col('doc_ligne', 'ref_article')
    tc = table('clients_fournisseurs')
    cc_id = col('clients_fournisseurs', 'code')
    cc_n = col('clients_fournisseurs', 'nom')
    cc_ty = col('clients_fournisseurs', 'type_tiers')
    cc_e = col('clients_fournisseurs', 'encours')
    cc_sommeil = col('clients_fournisseurs', 'sommeil')
    ta = table('articles')
    ca_r = col('articles', 'ref')
    ca_d = col('articles', 'designation')
    ca_pv = col('articles', 'prix_vente')
    ca_pa = col('articles', 'prix_achat')
    ts = table('stock')
    cs_r = col('stock', 'ref')
    cs_q = col('stock', 'qte_stock')
    cs_c = col('stock', 'qte_commande')
    tr = table('reglements')
    cr_p = col('reglements', 'piece')
    cr_dr = col('reglements', 'date_reglement')
    tls = table('lot_serie')
    cls_ref = col('lot_serie', 'ref')
    cls_num = col('lot_serie', 'numero')
    cls_qi = col('lot_serie', 'qte_initiale')
    cls_qr = col('lot_serie', 'qte_restante')
    cls_ep = col('lot_serie', 'epuise')
    cls_per = col('lot_serie', 'peremption')
    cls_fab = col('lot_serie', 'fabrication')
    cls_lin = col('lot_serie', 'ligne_entree')
    cls_lout = col('lot_serie', 'ligne_sortie')
    mois_expr = _fmt_mois(f'e.{ce_d}', mssql)
    now_expr = _now_date(mssql)
    limit12 = _limit_clause(12, mssql)
    top12_prefix = _top_prefix(12, mssql)
    impayes_join = f'AND r.{cr_p} IS NULL'
    exemples: list[tuple[str, str]] = [('clients avec un encours superieur a 5000', f'SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_du FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND r.{cr_p} IS NULL GROUP BY e.{ce_ti}, c.{cc_n} HAVING SUM(l.{cl_q}*l.{cl_pu}) > 5000 ORDER BY montant_du DESC'), ('liste tous les articles du catalogue', f'SELECT a.{ca_r}, a.{ca_d}, a.{ca_pv}, COALESCE(s.{cs_q},0) AS stock FROM {ta} a LEFT JOIN {ts} s ON a.{ca_r}=s.{cs_r} ORDER BY a.{ca_r}'), ('top 5 clients par chiffre d affaires', f'SELECT {top12_prefix}e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS ca_total FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 GROUP BY e.{ce_ti}, c.{cc_n} ORDER BY ca_total DESC, e.{ce_ti} ASC'), ('top 5 clients avec nombre de commandes et derniere date achat', f'SELECT {top12_prefix}c.{cc_id}, c.{cc_n}, cmd.nb_commandes, ach.derniere_date FROM {tc} c LEFT JOIN (SELECT {ce_ti}, COUNT(*) AS nb_commandes FROM {te}            WHERE {ce_do}=0 AND {ce_ty}=1 GROUP BY {ce_ti}) cmd ON cmd.{ce_ti}=c.{cc_id} LEFT JOIN (SELECT {ce_ti}, MAX({ce_d}) AS derniere_date FROM {te}            WHERE {ce_do}=0 AND {ce_ty}=6 GROUP BY {ce_ti}) ach ON ach.{ce_ti}=c.{cc_id} ORDER BY cmd.nb_commandes DESC, c.{cc_id} ASC'), ('factures impayees non reglees', f'SELECT e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND r.{cr_p} IS NULL GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC'), ('factures en souffrance', f'SELECT e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND r.{cr_p} IS NULL GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC'), ('clients avec plus de 6 factures impayees', f'SELECT e.{ce_ti}, c.{cc_n}, COUNT(DISTINCT e.{ce_p}) AS nb_factures, SUM(l.{cl_q}*l.{cl_pu}) AS total_impaye FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 {impayes_join} GROUP BY e.{ce_ti}, c.{cc_n} HAVING COUNT(DISTINCT e.{ce_p}) > 6 ORDER BY total_impaye DESC'), ('ca mensuel 12 derniers mois', f'SELECT {top12_prefix}{mois_expr} AS mois, COUNT(DISTINCT e.{ce_p}) AS nb_factures, SUM(l.{cl_q}*l.{cl_pu}) AS ca_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND e.{ce_d} >= {_date_sub_days(365, mssql)} GROUP BY {mois_expr} ORDER BY mois DESC{limit12}'), ('factures impayees par mois', f'SELECT {mois_expr} AS mois, COUNT(DISTINCT e.{ce_p}) AS nb_factures, SUM(l.{cl_q}*l.{cl_pu}) AS montant_impaye FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 {impayes_join} GROUP BY {mois_expr} ORDER BY mois DESC'), ('articles en rupture de stock', f'SELECT a.{ca_r}, a.{ca_d}, COALESCE(s.{cs_q},0) AS stock FROM {ta} a LEFT JOIN {ts} s ON a.{ca_r}=s.{cs_r} WHERE COALESCE(s.{cs_q},0)<=0 ORDER BY a.{ca_r}'), ('chiffre d affaires global total', f'SELECT COUNT(DISTINCT e.{ce_p}) AS nb_factures, COUNT(DISTINCT e.{ce_ti}) AS nb_clients, SUM(l.{cl_q}*l.{cl_pu}) AS ca_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0'), ('clients bloqués', f'SELECT {cc_id}, {cc_n}, {cc_e} FROM {tc} WHERE {cc_ty}=0 AND {cc_sommeil}=1 ORDER BY {cc_n}'), ('stock de l article ECRAN4K', f"SELECT a.{ca_r}, a.{ca_d}, COALESCE(s.{cs_q},0) AS stock, COALESCE(s.{cs_c},0) AS en_commande FROM {ta} a LEFT JOIN {ts} s ON a.{ca_r}=s.{cs_r} WHERE UPPER(a.{ca_r})='ECRAN4K'"), ('factures du client CLI001', f"SELECT e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht, CASE WHEN EXISTS (SELECT 1 FROM {tr} r WHERE r.{cr_p} = e.{ce_p}) THEN 'RÉGLÉE' ELSE 'EN ATTENTE' END AS statut FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_ti}='CLI001' GROUP BY e.{ce_p}, e.{ce_d} ORDER BY e.{ce_d} DESC"), ('marge brute par article rentabilite', f'SELECT l.{cl_a}, a.{ca_d}, SUM(l.{cl_q}*l.{cl_pu}) AS ca_vente, SUM(l.{cl_q}*a.{ca_pa}) AS cout_achat, SUM(l.{cl_q}*l.{cl_pu})-SUM(l.{cl_q}*a.{ca_pa}) AS marge_brute FROM {tl} l JOIN {te} e ON l.{cl_p}=e.{ce_p} LEFT JOIN {ta} a ON l.{cl_a}=a.{ca_r} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 GROUP BY l.{cl_a}, a.{ca_d} ORDER BY marge_brute DESC'), ('encours client CLI002', f"SELECT c.{cc_id}, c.{cc_n}, COALESCE(c.{cc_e},0) AS encours_autorise, COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS encours_utilise FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE c.{cc_id}='CLI002' AND r.{cr_p} IS NULL GROUP BY c.{cc_id}, c.{cc_n}, c.{cc_e}"), ('clients inactifs depuis 6 mois', f'SELECT c.{cc_id}, c.{cc_n}, MAX(e.{ce_d}) AS derniere_commande FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 WHERE c.{cc_ty}=0 GROUP BY c.{cc_id}, c.{cc_n} HAVING MAX(e.{ce_d}) IS NULL OR MAX(e.{ce_d}) < {_date_sub_days(180, mssql)} ORDER BY derniere_commande ASC'), ('liste des bons de livraison du client CLI001', f"SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=3 AND e.{ce_do}=0 AND e.{ce_ti}='CLI001' GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti} ORDER BY e.{ce_d} DESC"), ('clients ayant des factures superieures a 1000', f'SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS total_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 GROUP BY e.{ce_ti}, c.{cc_n} HAVING SUM(l.{cl_q}*l.{cl_pu}) > 1000 ORDER BY total_ht DESC'), ('tous les bons de livraison', f'SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=3 AND e.{ce_do}=0 GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n} ORDER BY e.{ce_d} DESC'), ('factures fournisseur', f'SELECT e.{ce_ti}, c.{cc_n}, e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=16 AND e.{ce_do}=1 AND c.{cc_ty}=1 GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC'), ('bons de reception fournisseur', f'SELECT e.{ce_ti}, c.{cc_n}, e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=13 AND e.{ce_do}=1 AND c.{cc_ty}=1 GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC'), ('clients qui ont passe plus de 3 commandes', f'SELECT c.{cc_id}, c.{cc_n}, COUNT(DISTINCT e.{ce_p}) AS nb_factures, COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS ca_total FROM {tc} c JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 AND e.{ce_do}=0 LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE c.{cc_ty}=0 GROUP BY c.{cc_id}, c.{cc_n} HAVING COUNT(DISTINCT e.{ce_p}) > 3 ORDER BY nb_factures DESC'), ('articles dont le prix de vente depasse 500', f'SELECT {ca_r}, {ca_d}, {ca_pv} AS prix_vente, {ca_pa} AS prix_achat, ROUND({ca_pv} - {ca_pa}, 2) AS marge FROM {ta} WHERE {ca_pv} > 500 ORDER BY {ca_pv} DESC'), ('factures du mois de juin', f'SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n}, COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS montant_ht FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 ' + (f'AND YEAR(e.{ce_d})=YEAR(GETDATE()) AND MONTH(e.{ce_d})=6 ' if mssql else f"AND strftime('%Y-%m', e.{ce_d})=strftime('%Y', 'now')||'-06' ") + f'GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n} ORDER BY e.{ce_d} DESC'), ('clients avec moins de 2 factures', f'SELECT c.{cc_id}, c.{cc_n}, COUNT(DISTINCT e.{ce_p}) AS nb_factures FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=6 AND e.{ce_do}=0 WHERE c.{cc_ty}=0 GROUP BY c.{cc_id}, c.{cc_n} HAVING COUNT(DISTINCT e.{ce_p}) < 2 ORDER BY nb_factures ASC'), ('liste des fournisseurs', f'SELECT {cc_id}, {cc_n} FROM {tc} WHERE {cc_ty} = 1 ORDER BY {cc_n}'), ('top 5 fournisseurs par montant d achat', f'SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS total_achat FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=16 AND e.{ce_do}=1 AND c.{cc_ty}=1 GROUP BY e.{ce_ti}, c.{cc_n} ORDER BY total_achat DESC'), ('fournisseurs bloques', f'SELECT {cc_id}, {cc_n}, {cc_e} FROM {tc} WHERE {cc_ty}=1 AND {cc_sommeil}=1 ORDER BY {cc_n}'), ('bons de commande fournisseur', f'SELECT e.{ce_ti}, c.{cc_n}, e.{ce_p}, e.{ce_d}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=11 AND e.{ce_do}=1 AND c.{cc_ty}=1 GROUP BY e.{ce_p}, e.{ce_ti}, c.{cc_n}, e.{ce_d} ORDER BY e.{ce_d} DESC'), ('fournisseurs inactifs depuis 6 mois', f'SELECT c.{cc_id}, c.{cc_n}, MAX(e.{ce_d}) AS derniere_commande FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=11 AND e.{ce_do}=1 WHERE c.{cc_ty}=1 GROUP BY c.{cc_id}, c.{cc_n} HAVING MAX(e.{ce_d}) IS NULL OR MAX(e.{ce_d}) < {_date_sub_days(180, mssql)} ORDER BY derniere_commande ASC'), ('encours fournisseur FOUR001', f"SELECT c.{cc_id}, c.{cc_n}, COALESCE(c.{cc_e},0) AS encours_autorise, COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS encours_utilise FROM {tc} c LEFT JOIN {te} e ON c.{cc_id}=e.{ce_ti} AND e.{ce_ty}=16 AND e.{ce_do}=1 LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE c.{cc_id}='FOUR001' AND r.{cr_p} IS NULL GROUP BY c.{cc_id}, c.{cc_n}, c.{cc_e}"), ('fournisseurs ayant des factures superieures a 5000', f'SELECT e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS total_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=16 AND e.{ce_do}=1 GROUP BY e.{ce_ti}, c.{cc_n} HAVING SUM(l.{cl_q}*l.{cl_pu}) > 5000 ORDER BY total_ht DESC'), ('liste des ordres de fabrication OF', f'SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, SUM(l.{cl_q}) AS qte_totale FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=25 AND e.{ce_do}=2 GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti} ORDER BY e.{ce_d} DESC'), ('liste des bons de fabrication BF', f'SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, SUM(l.{cl_q}) AS qte_totale FROM {te} e LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=26 AND e.{ce_do}=2 GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti} ORDER BY e.{ce_d} DESC'), ('liste des factures de vente', f'SELECT e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n}, SUM(l.{cl_q}*l.{cl_pu}) AS montant_ht FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 GROUP BY e.{ce_p}, e.{ce_d}, e.{ce_ti}, c.{cc_n} ORDER BY e.{ce_d} DESC'), ('delai moyen de paiement dso', f"SELECT e.{ce_ti}, c.{cc_n}, ROUND(AVG(CASE WHEN r.{cr_dr} IS NOT NULL THEN {_diff_jours(f'r.{cr_dr}', f'e.{ce_d}', mssql)} ELSE {_diff_jours(now_expr, f'e.{ce_d}', mssql)} END), 1) AS dso_jours FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} LEFT JOIN {tr} r ON e.{ce_p}=r.{cr_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 GROUP BY e.{ce_ti}, c.{cc_n} ORDER BY dso_jours DESC"), ('lots disponibles pour MOBWAC01', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls} WHERE UPPER({cls_ref})='MOBWAC01') SELECT {cls_num}, {cls_qr}, {cls_per} FROM DerniereLigne WHERE rn=1 AND COALESCE({cls_ep},0)=0 AND COALESCE({cls_qr},0)>0 ORDER BY {cls_per} ASC"), ('quantite restante du lot MOB0098-77644', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls} WHERE UPPER({cls_num})='MOB0098-77644') SELECT {cls_ref}, {cls_num}, {cls_qi}, {cls_qr} FROM DerniereLigne WHERE rn=1"), ('le lot LOT-BDF9411123 est il encore disponible', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls} WHERE UPPER({cls_num})='LOT-BDF9411123') SELECT {cls_num}, {cls_qr}, {cls_ep}, {cls_per} FROM DerniereLigne WHERE rn=1"), ('d ou vient le lot LOT-2026-00042', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls} WHERE UPPER({cls_num})='LOT-2026-00042') SELECT ls.{cls_num}, e.{ce_p} AS piece_origine, e.{ce_ty}, e.{ce_do}, e.{ce_d} FROM DerniereLigne ls LEFT JOIN {tl} l ON ls.{cls_lin}=l.{col('doc_ligne', 'id')} LEFT JOIN {te} e ON l.{cl_p}=e.{ce_p} WHERE ls.rn=1"), ('sur quel bl est parti le lot MOB0098-77644', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls} WHERE UPPER({cls_num})='MOB0098-77644') SELECT ls.{cls_num}, e.{ce_p} AS piece_sortie, e.{ce_ty}, e.{ce_d}, e.{ce_ti} FROM DerniereLigne ls LEFT JOIN {tl} l ON ls.{cls_lout}=l.{col('doc_ligne', 'id')} LEFT JOIN {te} e ON l.{cl_p}=e.{ce_p} WHERE ls.rn=1"), ('lots qui expirent bientot peremption proche 30 jours', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls}) SELECT {cls_ref}, {cls_num}, {cls_per}, {cls_qr} FROM DerniereLigne WHERE rn=1 AND {cls_per} IS NOT NULL AND {cls_per} <= {_date_add_days(30, mssql)} AND {cls_per} >= {_now_date(mssql)} AND COALESCE({cls_ep},0)=0 ORDER BY {cls_per} ASC"), ('lots epuises de MOBWOR01', f"WITH DerniereLigne AS (  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cls_ref}, {cls_num} ORDER BY {col('lot_serie', 'cbmarq')} DESC) AS rn   FROM {tls} WHERE UPPER({cls_ref})='MOBWOR01') SELECT {cls_num}, {cls_qi}, {cls_fab} FROM DerniereLigne WHERE rn=1 AND COALESCE({cls_ep},0)=1 ORDER BY {cls_fab} ASC")]
    tn = table('nomenclature')
    cn_pf = col('nomenclature', 'ref_pf')
    cn_mp = col('nomenclature', 'ref_mp')
    cn_q = col('nomenclature', 'qte')
    exemples += [('composants de la nomenclature de MONTRE01', f"SELECT n.{cn_pf}, n.{cn_mp}, a.{ca_d}, n.{cn_q} FROM {tn} n LEFT JOIN {ta} a ON n.{cn_mp}=a.{ca_r} WHERE n.{cn_pf}='MONTRE01'"), ('combien de composants a l article MONTRE01', f"SELECT COUNT(*) AS nb_composants FROM {tn} WHERE {cn_pf}='MONTRE01'"), ('articles utilises comme composant dans plusieurs nomenclatures', f'SELECT {cn_mp}, COUNT(DISTINCT {cn_pf}) AS nb_nomenclatures FROM {tn} GROUP BY {cn_mp} HAVING COUNT(DISTINCT {cn_pf}) > 1 ORDER BY nb_nomenclatures DESC'), ('cout matiere premiere de la nomenclature MONTRE01', f"SELECT n.{cn_pf}, SUM(n.{cn_q}*a.{ca_pa}) AS cout_total FROM {tn} n LEFT JOIN {ta} a ON n.{cn_mp}=a.{ca_r} WHERE n.{cn_pf}='MONTRE01' GROUP BY n.{cn_pf}")]
    exemples += [('liste des reglements du client CARAT', f"SELECT r.{cr_p}, r.{col('reglements', 'mode_paiement')}, r.{col('reglements', 'montant')}, r.{cr_dr} FROM {tr} r JOIN {te} e ON r.{cr_p}=e.{ce_p} WHERE e.{ce_ti}='CARAT' ORDER BY r.{cr_dr} DESC"), ('total encaisse par mode de paiement', f"SELECT {col('reglements', 'mode_paiement')}, SUM({col('reglements', 'montant')}) AS total FROM {tr} GROUP BY {col('reglements', 'mode_paiement')} ORDER BY total DESC"), ('montant total regle en 2026', f"SELECT SUM({col('reglements', 'montant')}) AS total_regle FROM {tr} WHERE YEAR({cr_dr})=2026" if mssql else f"SELECT SUM({col('reglements', 'montant')}) AS total_regle FROM {tr} WHERE strftime('%Y',{cr_dr})='2026'"), ('delai moyen entre facture et reglement', f"SELECT AVG({_diff_jours(f'r.{cr_dr}', f'e.{ce_d}', mssql)}) AS delai_moyen_jours FROM {te} e JOIN {tr} r ON e.{ce_p}=r.{cr_p} WHERE e.{ce_ty}=6")]
    mois_dernier_debut = _premier_jour_mois_dernier(mssql)
    mois_courant_debut = _premier_jour_mois_courant(mssql)
    annee_courante_expr = _year_current(mssql)
    exemples += [('chiffre d affaires du mois dernier', f'SELECT COUNT(DISTINCT e.{ce_p}) AS nb_factures, SUM(l.{cl_q}*l.{cl_pu}) AS ca_ht FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND e.{ce_d} >= {mois_dernier_debut} AND e.{ce_d} < {mois_courant_debut}'), ('total des factures du mois precedent', f'SELECT COUNT(DISTINCT e.{ce_p}) AS nb_factures, SUM(l.{cl_q}*l.{cl_pu}) AS montant_total FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND e.{ce_d} >= {mois_dernier_debut} AND e.{ce_d} < {mois_courant_debut}'), ('compare le chiffre d affaires de ce mois ci avec le meme mois l annee derniere', f'SELECT SUM(CASE WHEN e.{ce_d} >= {mois_courant_debut} THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_mois_courant, ' + (f'SUM(CASE WHEN YEAR(e.{ce_d})=YEAR(DATEADD(year,-1,GETDATE())) AND MONTH(e.{ce_d})=MONTH(GETDATE()) THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_meme_mois_annee_derniere ' if mssql else f"SUM(CASE WHEN strftime('%Y-%m',e.{ce_d})=strftime('%Y-%m',DATE('now','-1 year')) THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_meme_mois_annee_derniere ") + f'FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0'), ('chiffre d affaires de la semaine derniere compare a celui d il y a deux semaines', f'SELECT ' + (f'SUM(CASE WHEN e.{ce_d} >= DATEADD(day,-7,CAST(GETDATE() AS DATE)) AND e.{ce_d} < CAST(GETDATE() AS DATE) THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_semaine_derniere, SUM(CASE WHEN e.{ce_d} >= DATEADD(day,-14,CAST(GETDATE() AS DATE)) AND e.{ce_d} < DATEADD(day,-7,CAST(GETDATE() AS DATE)) THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_il_y_a_deux_semaines ' if mssql else f"SUM(CASE WHEN e.{ce_d} >= DATE('now','-7 days') AND e.{ce_d} < DATE('now') THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_semaine_derniere, SUM(CASE WHEN e.{ce_d} >= DATE('now','-14 days') AND e.{ce_d} < DATE('now','-7 days') THEN l.{cl_q}*l.{cl_pu} ELSE 0 END) AS ca_il_y_a_deux_semaines ") + f'FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0'), ('evolution du chiffre d affaires sur les 3 dernieres annees', f'SELECT ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + f' AS annee, COUNT(DISTINCT e.{ce_p}) AS nb_factures, SUM(l.{cl_q}*l.{cl_pu}) AS ca_annuel FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 AND ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + f' >= {annee_courante_expr} - 3 GROUP BY ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + ' ORDER BY annee'), ('taux de croissance du chiffre d affaires', f'SELECT a.annee, a.ca_annuel, b.ca_annuel AS ca_annee_prec, ROUND((a.ca_annuel - b.ca_annuel) / NULLIF(b.ca_annuel, 0) * 100, 1) AS taux_croissance_pct FROM (  SELECT ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + f' AS annee,   SUM(l.{cl_q}*l.{cl_pu}) AS ca_annuel   FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p}   WHERE e.{ce_ty}=6 AND e.{ce_do}=0   GROUP BY ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + f') a LEFT JOIN (  SELECT ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + f' AS annee,   SUM(l.{cl_q}*l.{cl_pu}) AS ca_annuel   FROM {te} e JOIN {tl} l ON e.{ce_p}=l.{cl_p}   WHERE e.{ce_ty}=6 AND e.{ce_do}=0   GROUP BY ' + (f'YEAR(e.{ce_d})' if mssql else f"CAST(strftime('%Y',e.{ce_d}) AS INTEGER)") + f') b ON a.annee = b.annee + 1 ORDER BY a.annee DESC'), ('chiffre d affaires par mois sur les 6 derniers mois', f"WITH Mois AS (  SELECT TOP 6 DATEADD(month, -n.number, {mois_courant_debut}) AS debut_mois   FROM (SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS number         FROM {tl}) n   GROUP BY DATEADD(month, -n.number, {mois_courant_debut})   ORDER BY debut_mois DESC) SELECT FORMAT(m.debut_mois,'yyyy-MM') AS mois, COUNT(DISTINCT e.{ce_p}) AS nb_factures, COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS ca_ht FROM Mois m LEFT JOIN {te} e ON e.{ce_d} >= m.debut_mois AND e.{ce_d} < DATEADD(month,1,m.debut_mois) AND e.{ce_ty}=6 AND e.{ce_do}=0 LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} GROUP BY m.debut_mois ORDER BY m.debut_mois DESC" if mssql else f"WITH RECURSIVE Mois(offset_mois) AS (  SELECT 0 UNION ALL SELECT offset_mois+1 FROM Mois WHERE offset_mois < 5) SELECT strftime('%Y-%m', DATE({mois_courant_debut}, '-' || offset_mois || ' months')) AS mois, COUNT(DISTINCT e.{ce_p}) AS nb_factures, COALESCE(SUM(l.{cl_q}*l.{cl_pu}),0) AS ca_ht FROM Mois LEFT JOIN {te} e ON strftime('%Y-%m',e.{ce_d}) = strftime('%Y-%m', DATE({mois_courant_debut}, '-' || offset_mois || ' months')) AND e.{ce_ty}=6 AND e.{ce_do}=0 LEFT JOIN {tl} l ON e.{ce_p}=l.{cl_p} GROUP BY offset_mois ORDER BY offset_mois ASC"), ('combien de clients ont commande pour la premiere fois ce mois ci', f'SELECT COUNT(*) AS nb_nouveaux_clients FROM (  SELECT e.{ce_ti} FROM {te} e   WHERE e.{ce_ty}=6 AND e.{ce_do}=0   GROUP BY e.{ce_ti}   HAVING MIN(' + (f'TRY_CAST(e.{ce_d} AS DATETIME)' if mssql else f'e.{ce_d}') + f') >= {mois_courant_debut}) AS premiers_clients'), ('liste des clients qui ont commande pour la premiere fois ce mois ci', f'SELECT e.{ce_ti}, c.{cc_n}, MIN(' + (f'TRY_CAST(e.{ce_d} AS DATETIME)' if mssql else f'e.{ce_d}') + f') AS premiere_commande FROM {te} e LEFT JOIN {tc} c ON e.{ce_ti}=c.{cc_id} WHERE e.{ce_ty}=6 AND e.{ce_do}=0 GROUP BY e.{ce_ti}, c.{cc_n} HAVING MIN(' + (f'TRY_CAST(e.{ce_d} AS DATETIME)' if mssql else f'e.{ce_d}') + f') >= {mois_courant_debut} ORDER BY premiere_commande DESC')]
    vus: set[str] = set()
    dedupliques: list[tuple[str, str]] = []
    for question, sql in exemples:
        cle = question.strip().lower()
        if cle in vus:
            continue
        vus.add(cle)
        dedupliques.append((question, sql))
    return dedupliques

def _valider_colonnes_connues(sql: str, colonnes_valides: set[str], mssql: bool=False) -> bool:
    """
Vérifie si les colonnes spécifiées sont présentes dans l'instruction SQL donnée en fonction du dialecte de base de données utilisé.
"""
    if not sql or not colonnes_valides:
        return True
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp
        sql_nettoye = _nettoyer_sql_commentaires(sql)
        dialect = 'tsql' if mssql else 'sqlite'
        tree = sqlglot.parse_one(sql_nettoye, dialect=dialect)
        colonnes_valides_upper = {c.upper() for c in colonnes_valides}
        alias_connus = set()
        for cte in tree.find_all(sqlglot_exp.CTE):
            if getattr(cte, 'alias', None):
                alias_connus.add(cte.alias.upper())
        for select_expr in tree.find_all(sqlglot_exp.Select):
            for projection in select_expr.expressions:
                if isinstance(projection, sqlglot_exp.Alias):
                    alias_connus.add(projection.alias.upper())
        alias_tables = set()
        for table_expr in tree.find_all(sqlglot_exp.Table):
            if getattr(table_expr, 'alias', None):
                alias_tables.add(table_expr.alias.upper())
        for col_node in tree.find_all(sqlglot_exp.Column):
            col_name = col_node.name
            table_ref = col_node.table.upper() if col_node.table else ''
            if not col_name:
                continue
            if col_name == '*' or col_name.upper() in ('NULL', 'TRUE', 'FALSE'):
                continue
            if col_name.upper() in alias_connus:
                continue
            if table_ref in alias_connus:
                continue
            if col_name.upper() not in colonnes_valides_upper:
                print(f"   ⚠️  [Vanna Phase 3] Colonne inconnue dans le SQL : '{col_name}' → score forcé à 0.0")
                return False
        return True
    except ImportError:
        return True
    except Exception:
        return True

def _construire_colonnes_valides(table_fn, col_fn) -> set[str]:
    """
Cette fonction génère les colonnes valides d'une table à partir d'une fonction de construction de colonnes.
"""
    tables_logiques = {'clients_fournisseurs': ['code', 'nom', 'type_tiers', 'sommeil', 'encours', 'encours_max', 'validite'], 'articles': ['ref', 'designation', 'prix_achat', 'prix_vente', 'type_article'], 'stock': ['ref', 'qte_stock', 'qte_commande'], 'doc_entete': ['piece', 'domaine', 'type', 'date', 'reference', 'code_tiers'], 'doc_ligne': ['ligne', 'piece', 'ref_article', 'qte', 'prix_unitaire'], 'reglements': ['piece', 'mode_paiement', 'montant', 'date_reglement'], 'nomenclature': ['ref_pf', 'ref_mp', 'qte']}
    colonnes: set[str] = {'cbMarq', 'cbDO_Piece', 'DE_No', 'cbCT_Num', 'cbAR_Ref', 'cbModification', 'cbCreateur', 'cbProt'}
    for table_logique, champs in tables_logiques.items():
        try:
            for champ in champs:
                colonnes.add(col_fn(table_logique, champ))
        except (KeyError, Exception):
            pass
    return colonnes
_vanna_generation_en_cours: 'threading.Event | None' = None

def _get_generation_event() -> 'threading.Event':
    """
Fournit un événement pour synchroniser les traitements de génération.
"""
    global _vanna_generation_en_cours
    if _vanna_generation_en_cours is None:
        _vanna_generation_en_cours = threading.Event()
    return _vanna_generation_en_cours

def generer_sql_thread_safe(vn, question: str, timeout_s: float, mssql: bool, *, table=None, col=None, _retry=False) -> tuple[str | None, float]:
    """
Générer une requête SQL thread-safe pour trouver les questions similaires en fonction d'une question donnée.
"""
    if vn is None:
        return (None, 0.0)
    gen_event = _get_generation_event()
    if gen_event.is_set():
        print('   ⚠️  [Vanna Phase 5] Génération précédente encore active → fallback patterns')
        return (None, 0.0)
    result_container: list = [None, None, 0]

    def _run():
        """
Remplacement temporaire de la fonction `get_similar_question_sql` pour limiter le nombre de résultats à 5.
"""
        original_get = getattr(vn, 'get_similar_question_sql', None)

        def _get_limited(q, **kw):
            """
Récupère des questions similaires pour une question donnée, en limitant la liste aux 5 premiers résultats.
"""
            try:
                results = original_get(q, **kw)
            except Exception as e:
                print(f'   ⚠️  [Vanna] get_similar_question_sql a échoué : {e}')
                return []
            if results is None:
                return []
            if isinstance(results, list):
                results = [r for r in results if isinstance(r, dict) and r.get('question') and r.get('sql')]
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
            if original_get:
                vn.get_similar_question_sql = original_get
            gen_event.clear()
    gen_event.set()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        print(f'   ⚠️  [Vanna] Timeout {timeout_s}s → fallback patterns')
        return (None, 0.0)
    if result_container[1] is not None:
        print(f'   ⚠️  [Vanna] {result_container[1]}')
        return (None, 0.0)
    sql = result_container[0]
    nb_similaires = result_container[2]
    sql_check = _nettoyer_sql_commentaires(sql)
    if not sql_check or not sql_check.upper().startswith('SELECT'):
        return (None, 0.0)
    if table is not None and col is not None:
        try:
            colonnes_valides = _construire_colonnes_valides(table, col)
            if colonnes_valides and (not _valider_colonnes_connues(sql_check, colonnes_valides, mssql=mssql)):
                if not _retry:
                    print(f'   🔄 [Vanna] Phase 3 échouée, tentative de correction automatique (retry=1)...')
                    question_corrigee = f"{question} (attention: n'utilise que les colonnes physiques du schéma, pas d'alias inventés dans une clause différente)"
                    return generer_sql_thread_safe(vn, question_corrigee, timeout_s, mssql, table=table, col=col, _retry=True)
                return (None, 0.0)
        except Exception as e:
            print(f'   ⚠️  [Vanna Phase 3] Validation colonnes échouée (ignorée) : {e}')
    ok, score = valider_sql_dialecte(sql_check, mssql, nb_similaires)
    return (sql_check.strip(), score)

def valider_sql_dialecte(sql: str, mssql: bool, nb_similaires: int=0) -> tuple[bool, float]:
    """
Vérifie la syntaxe d'une requête SQL et attribue un score en fonction de la validité de la requête et de la présence de requêtes similaires.
"""
    dialect = 'tsql' if mssql else 'sqlite'
    try:
        import sqlglot
        sql_nettoye = _nettoyer_sql_commentaires(sql)
        sqlglot.parse_one(sql_nettoye, dialect=dialect)
        score = 0.65 + min(0.3, nb_similaires * 0.1)
        return (True, score)
    except ImportError:
        return (True, 0.35)
    except Exception as e:
        print(f'   ❌ [Vanna] Erreur parsing SQL ({dialect}): {e}')
        return (False, 0.35)
_HASH_STORE_KEY = 'vanna_training_content_hash'

def calculer_hash_entrainement(ddl_statements: list[str], documentation: str, exemples: list[tuple[str, str]]) -> str:
    """
Crée un hash SHA-256 basé sur des données d'entraînement pour les models de langage.
"""
    payload = json.dumps({'ddl': ddl_statements, 'doc': documentation, 'exemples': exemples}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def doit_reentrainer(vn, hash_actuel: str, chemin_hash_file: str='./vanna_erp_db/.training_hash') -> bool:
    """
Fonction permettant de savoir si le système de hashing de données doit être re-entraîné en fonction du hash actuel et du hash stocké dans le fichier de configuration.
"""
    try:
        with open(chemin_hash_file, 'r', encoding='utf-8') as f:
            hash_stocke = f.read().strip()
        return hash_stocke != hash_actuel
    except FileNotFoundError:
        return True

def marquer_entrainement_fait(hash_actuel: str, chemin_hash_file: str='./vanna_erp_db/.training_hash') -> None:
    """
Enregistre le hash actuel dans un fichier local pour marquer qu'un entraînement est terminé.
"""
    import os
    os.makedirs(os.path.dirname(chemin_hash_file) or '.', exist_ok=True)
    with open(chemin_hash_file, 'w', encoding='utf-8') as f:
        f.write(hash_actuel)