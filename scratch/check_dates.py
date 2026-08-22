import sys
import json
sys.path.insert(0, '.')
from api.mcp_nl2sql import _connect

conn = _connect()

# Vérifier les dates max pour chaque client
query = """
SELECT 
    c.CT_Num, 
    c.CT_Intitule,
    MAX(e.DO_Date) as max_facture,
    COUNT(e.DO_Piece) as nb_factures
FROM F_COMPTET c
LEFT JOIN F_DOCENTETE e ON c.CT_Num = e.DO_Tiers AND e.DO_Type = 6 AND e.DO_Domaine = 0
WHERE c.CT_Type = 0
GROUP BY c.CT_Num, c.CT_Intitule
"""

print("--- FACTURES (Type=6) ---")
for r in conn.execute(query).fetchall():
    print(dict(r))

query_cmd = """
SELECT 
    c.CT_Num, 
    c.CT_Intitule,
    MAX(e.DO_Date) as max_commande,
    COUNT(e.DO_Piece) as nb_commandes
FROM F_COMPTET c
LEFT JOIN F_DOCENTETE e ON c.CT_Num = e.DO_Tiers AND e.DO_Type = 1 AND e.DO_Domaine = 0
WHERE c.CT_Type = 0
GROUP BY c.CT_Num, c.CT_Intitule
"""

print("\n--- COMMANDES (Type=1) ---")
for r in conn.execute(query_cmd).fetchall():
    print(dict(r))

conn.close()
