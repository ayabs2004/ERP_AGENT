"""Test script: simulate _workflow_of sequence to check for 3988."""
import sys
sys.path.insert(0, ".")
import adaptation.db_adapter as sch
from adaptation.db_adapter import get_connection

T_NOMENCLAT = sch.T_NOMENCLAT
C_NO_REF_PF = sch.C_NO_REF_PF
C_NO_REF_MP = sch.C_NO_REF_MP
C_NO_QTE = sch.C_NO_QTE
T_STOCK = sch.T_STOCK
C_AS_REF = sch.C_AS_REF
C_AS_QTESTO = sch.C_AS_QTESTO

conn = get_connection()
try:
    ref_art = "BAAR01"

    # Step 1: get composants
    nomenc = conn.execute(
        f"SELECT n.{C_NO_REF_MP} AS ref_composant, n.{C_NO_QTE} AS qte FROM {T_NOMENCLAT} n WHERE UPPER(n.{C_NO_REF_PF}) = UPPER(?)",
        (ref_art,)
    ).fetchall()
    print("nomenc rows:", len(nomenc), "=>", [(r["ref_composant"], r["qte"]) for r in nomenc])

    # Step 2: for each composant check stock
    for comp in nomenc:
        ref_comp = comp["ref_composant"]
        stock = conn.execute(
            f"SELECT COALESCE(SUM({C_AS_QTESTO}),0) AS q FROM {T_STOCK} WHERE {C_AS_REF}=?",
            (ref_comp,)
        ).fetchone()
        print(f"  comp {ref_comp} stock:", stock["q"])

    # Step 3: INSERT (the critical step)
    print("About to INSERT F_DOCENTETE...")
    conn.execute(
        "INSERT INTO F_DOCENTETE (DO_Domaine, DO_Type, DO_Piece, DO_Date, DO_Ref, DO_Tiers, DO_Statut, DO_Attente) "
        "VALUES (2, 1, 'TEST_OF_9999', GETDATE(), 'TESTREF', 'PROD-INT', 0, 1)"
    )
    print("INSERT OK -> rolling back")
    conn.rollback()
    print("rollback OK - no 3988!")
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
    try:
        conn.rollback()
    except Exception as re:
        print("rollback also failed:", re)
finally:
    conn.close()
