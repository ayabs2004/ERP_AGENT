import sys
import json
sys.path.insert(0, '.')
from api.mcp_nl2sql import _connect

conn = _connect()

query = """
SELECT
    c.CT_Num AS code,
    c.CT_Intitule AS nom,
    MAX(e.DO_Date) AS derniere_facture
FROM F_COMPTET c
LEFT JOIN F_DOCENTETE e
    ON  c.CT_Num  = e.DO_Tiers
    AND e.DO_Type = 6
    AND e.DO_Domaine = 0
LEFT JOIN F_DOCLIGNE l
    ON e.DO_Piece = l.DO_Piece
WHERE c.CT_Type = 0
GROUP BY c.CT_Num, c.CT_Intitule, c.CT_Encours, c.CT_Sommeil
HAVING
    MAX(e.DO_Date) IS NULL
    OR MAX(e.DO_Date) < DATEADD(day, -90, GETDATE())
"""

try:
    rows = conn.execute(query).fetchall()
    print(f"Trouvé {len(rows)} clients inactifs.")
    for r in rows:
        print(dict(r))
except Exception as e:
    print(f"Erreur SQL : {e}")

conn.close()
