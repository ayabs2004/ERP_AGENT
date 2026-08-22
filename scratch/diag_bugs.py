"""
Test diagnostics for all reported problems.
"""
import sys
sys.path.insert(0, '.')
import sqlite3
import json

# Test direct queries to see what the DB contains
conn = sqlite3.connect('entreprise_mock.db')
conn.row_factory = sqlite3.Row

print("=== TEST 1: Clients en baisse de CA ===")
from datetime import datetime, timedelta
today = datetime.now()
d_m6  = (today - timedelta(days=180)).strftime("%Y-%m-%d")
d_m12 = (today - timedelta(days=360)).strftime("%Y-%m-%d")
print(f"Période récente: {d_m6} à {today.strftime('%Y-%m-%d')}")
print(f"Période ancienne: {d_m12} à {d_m6}")

recents = conn.execute(f"""
    SELECT e.DO_Tiers AS code, c.CT_Intitule AS nom,
           COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS ca
    FROM F_DOCENTETE e
    JOIN F_DOCLIGNE l ON e.DO_Piece = l.DO_Piece
    LEFT JOIN F_COMPTET c ON e.DO_Tiers = c.CT_Num
    WHERE e.DO_Type=6 AND e.DO_Domaine=0 AND e.DO_Date>=?
    GROUP BY e.DO_Tiers
""", (d_m6,)).fetchall()
print("Factures récentes (6 derniers mois):", [dict(r) for r in recents])

anciens = conn.execute(f"""
    SELECT e.DO_Tiers AS code,
           COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS ca
    FROM F_DOCENTETE e
    JOIN F_DOCLIGNE l ON e.DO_Piece = l.DO_Piece
    WHERE e.DO_Type=6 AND e.DO_Domaine=0
      AND e.DO_Date>=? AND e.DO_Date<?
    GROUP BY e.DO_Tiers
""", (d_m12, d_m6)).fetchall()
print("Factures anciennes (6-12 mois avant):", [dict(r) for r in anciens])

print()
print("=== TEST 2: CA par mois (SAISONNALITE) ===")
rows = conn.execute("""
    SELECT STRFTIME('%Y-%m', e.DO_Date) AS mois,
           COUNT(DISTINCT e.DO_Piece) AS nb_factures,
           COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS ca_mensuel
    FROM F_DOCENTETE e
    JOIN F_DOCLIGNE l ON e.DO_Piece = l.DO_Piece
    WHERE e.DO_Type=6 AND e.DO_Domaine=0 AND e.DO_Date IS NOT NULL
    GROUP BY STRFTIME('%Y-%m', e.DO_Date)
    ORDER BY mois DESC
""").fetchall()
print("CA mensuel:", [dict(r) for r in rows])

print()
print("=== TEST 3: Liste fournisseurs ===")
rows = conn.execute("SELECT CT_Num, CT_Intitule, CT_Type FROM F_COMPTET WHERE CT_Type=1").fetchall()
print("Fournisseurs:", [dict(r) for r in rows])

print()
print("=== TEST 5: Clients inactifs depuis 3 mois ===")
rows = conn.execute("""
    SELECT c.CT_Num AS code, c.CT_Intitule AS nom,
           MAX(e.DO_Date) AS derniere_facture,
           COUNT(DISTINCT e.DO_Piece) AS nb_factures
    FROM F_COMPTET c
    LEFT JOIN F_DOCENTETE e
        ON c.CT_Num = e.DO_Tiers
        AND e.DO_Type = 6 AND e.DO_Domaine = 0
    WHERE c.CT_Type = 0
    GROUP BY c.CT_Num, c.CT_Intitule
    HAVING MAX(e.DO_Date) IS NULL OR MAX(e.DO_Date) < DATE('now', '-90 days')
""").fetchall()
print("Clients inactifs depuis 90j:", [dict(r) for r in rows])

print()
print("=== TEST 6: Top fournisseurs par volume d'achat ===")
print("--- Avec type=6 domaine=1 (actuel, INCORRECT) ---")
rows = conn.execute("""
    SELECT c.CT_Num AS code, c.CT_Intitule AS nom,
           COUNT(DISTINCT e.DO_Piece) AS nb_commandes,
           COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire), 0) AS volume_achat
    FROM F_COMPTET c
    LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.DO_Tiers AND e.DO_Type=6 AND e.DO_Domaine=1
    LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece
    WHERE c.CT_Type=1
    GROUP BY c.CT_Num, c.CT_Intitule
    ORDER BY volume_achat DESC
""").fetchall()
print([dict(r) for r in rows])

print()
print("--- BC Achat type=11 domaine=1 + Réception type=13 + Facture type=16 (CORRECT) ---")
rows = conn.execute("""
    SELECT c.CT_Num AS code, c.CT_Intitule AS nom,
           COUNT(DISTINCT e.DO_Piece) AS nb_commandes,
           COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire), 0) AS volume_achat
    FROM F_COMPTET c
    LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.DO_Tiers AND e.DO_Domaine=1
    LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece
    WHERE c.CT_Type=1
    GROUP BY c.CT_Num, c.CT_Intitule
    ORDER BY volume_achat DESC
""").fetchall()
print([dict(r) for r in rows])

print()
print("--- Factures fournisseur only type=16 domaine=1 ---")
rows = conn.execute("""
    SELECT c.CT_Num AS code, c.CT_Intitule AS nom,
           COUNT(DISTINCT e.DO_Piece) AS nb_commandes,
           COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire), 0) AS volume_achat
    FROM F_COMPTET c
    LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.DO_Tiers AND e.DO_Type=16 AND e.DO_Domaine=1
    LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece
    WHERE c.CT_Type=1
    GROUP BY c.CT_Num, c.CT_Intitule
    ORDER BY volume_achat DESC
""").fetchall()
print([dict(r) for r in rows])

conn.close()
print("\n=== All tests done ===")
