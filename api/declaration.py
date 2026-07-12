"""
declaration.py — Génération de la déclaration mensuelle Achat/Vente (Excel)
============================================================================
v2 — CORRECTIF DE NEUTRALITÉ DB (règle d'or db_adapter.py)
     La requête SQL de _lignes_periode() codait en dur F_DOCENTETE,
     F_COMPTET, F_DOCLIGNE, CT_Num, CT_Intitule, DO_Piece, DO_Date,
     DO_Type, DO_Domaine, DL_Qte, DL_PrixUnitaire, et _connect()
     ouvrait sa propre connexion sqlite3 sur un chemin recalculé
     localement. Désormais, tout passe par adaptation.db_adapter
     (table()/col()/get_connection()), comme dans mcp_actions_sage.py
     et nl2sql_server.py.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
import os

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Permet d'importer adaptation.db_adapter quel que soit le cwd d'exécution
sys.path.insert(0, str(Path(__file__).parent.parent))
from adaptation.db_adapter import table, col, get_connection

OUTPUT_DIR = Path("./declarations_generes")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TVA_TAUX = 0.19

_MOIS_FR = {
    "janvier": 1, "jan": 1, "février": 2, "fevrier": 2, "fev": 2,
    "mars": 3, "avril": 4, "avr": 4, "mai": 5,
    "juin": 6, "juillet": 7, "juil": 7, "août": 8, "aout": 8,
    "septembre": 9, "sept": 9, "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11, "décembre": 12, "decembre": 12, "dec": 12,
}
_MOIS_NOMS = [
    "", "JANVIER", "FÉVRIER", "MARS", "AVRIL", "MAI", "JUIN",
    "JUILLET", "AOÛT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DÉCEMBRE",
]


def _parse_mois_annee(texte: str) -> tuple[int, int]:
    """Extrait (mois, annee) depuis un texte libre. Année par défaut = année en cours."""
    texte = (texte or "").lower()
    mois = None
    for nom, num in _MOIS_FR.items():
        if re.search(rf"\b{nom}\b", texte):
            mois = num
            break
    if mois is None:
        m = re.search(r"\b(0?[1-9]|1[0-2])[/\-](\d{4})\b", texte)
        if m:
            return int(m.group(1)), int(m.group(2))
        raise ValueError("Mois introuvable dans la demande.")
    m_annee = re.search(r"\b(20\d{2})\b", texte)
    annee = int(m_annee.group(1)) if m_annee else datetime.now().year
    return mois, annee


def _connect():
    """Connexion DB via db_adapter (sqlite ou mssql selon db_config.json)."""
    return get_connection()


def _lignes_periode(conn, domaine: int, mois: int, annee: int, label_tiers: str):
    """domaine=0 → ventes (clients) | domaine=1 → achats (fournisseurs)"""
    mois_str = f"{annee:04d}-{mois:02d}"

    doc_entete_table = table("doc_entete")
    clients_table    = table("clients_fournisseurs")
    doc_ligne_table  = table("doc_ligne")

    piece_col   = col("doc_entete", "piece")
    date_col    = col("doc_entete", "date")
    type_col    = col("doc_entete", "type")
    domaine_col = col("doc_entete", "domaine")
    tiers_col   = col("doc_entete", "code_tiers")

    code_col = col("clients_fournisseurs", "code")
    nom_col  = col("clients_fournisseurs", "nom")

    piece_col_ligne = col("doc_ligne", "piece")
    qte_col         = col("doc_ligne", "qte")
    prix_col        = col("doc_ligne", "prix_unitaire")

    rows = conn.execute(f"""
        SELECT e.{piece_col} AS DO_Piece, e.{date_col} AS DO_Date, e.{tiers_col} AS CT_Num,
               COALESCE(c.{nom_col}, e.{tiers_col}) AS tiers,
               COALESCE(SUM(l.{qte_col} * l.{prix_col}), 0) AS ht
        FROM {doc_entete_table} e
        LEFT JOIN {clients_table}  c ON e.{tiers_col} = c.{code_col}
        LEFT JOIN {doc_ligne_table} l ON e.{piece_col} = l.{piece_col_ligne}
        WHERE e.{type_col} = 3 AND e.{domaine_col} = {domaine}
          AND STRFTIME('%Y-%m', e.{date_col}) = ?
        GROUP BY e.{piece_col}
        ORDER BY e.{date_col}
    """, (mois_str,)).fetchall()

    lignes = []
    for r in rows:
        ht = round(r["ht"] or 0.0, 2)
        tva = round(ht * TVA_TAUX, 2)
        ttc = round(ht + tva, 2)
        lignes.append({
            "date": datetime.strptime(r["DO_Date"], "%Y-%m-%d").strftime("%d/%m/%Y"),
            "ref": r["DO_Piece"],
            "tiers": r["tiers"],
            "ht": ht, "tva": tva, "ttc": ttc,
        })
    return lignes


# ── Styles Excel (repris du gabarit fourni) ─────────────────────────
FILL_TITRE = PatternFill("solid", fgColor="4BACC6")
FILL_ENTETE_SECTION = PatternFill("solid", fgColor="D9D9D9")
FILL_HEADER_COL = PatternFill("solid", fgColor="D9D9D9")
FILL_TOTAL = PatternFill("solid", fgColor="FBD4B4")
FONT_TITRE = Font(bold=True, color="FFFFFF", size=13)
FONT_ENTETE = Font(bold=True)
FONT_TOTAL = Font(bold=True)
BORDURE = Border(*(Side(style="thin", color="808080"),) * 4)
CENTRE = Alignment(horizontal="center", vertical="center")


def _bloc(ws, col_debut: int, titre: str, lignes: list[dict], label_tiers: str):
    """Écrit un bloc ACHAT ou VENTE à partir de la colonne col_debut (1-based)."""
    c0, c1, c2, c3, c4, c5 = range(col_debut, col_debut + 6)

    # Bandeau section (ligne 3)
    ws.merge_cells(start_row=3, start_column=c0, end_row=3, end_column=c5)
    cell = ws.cell(row=3, column=c0, value=titre)
    cell.font = FONT_ENTETE
    cell.alignment = CENTRE
    cell.fill = FILL_ENTETE_SECTION

    # En-têtes colonnes (ligne 4)
    entetes = ["Date", "Référence", label_tiers, "H.T", "TVA", "TTC"]
    for i, txt in enumerate(entetes):
        cell = ws.cell(row=4, column=col_debut + i, value=txt)
        cell.font = FONT_ENTETE
        cell.fill = FILL_HEADER_COL
        cell.alignment = CENTRE
        cell.border = BORDURE

    # Lignes de données
    r = 5
    total_ht = total_tva = total_ttc = 0.0
    for ligne in lignes:
        ws.cell(row=r, column=c0, value=ligne["date"]).border = BORDURE
        ws.cell(row=r, column=c1, value=ligne["ref"]).border = BORDURE
        ws.cell(row=r, column=c2, value=ligne["tiers"]).border = BORDURE
        ws.cell(row=r, column=c3, value=ligne["ht"]).border = BORDURE
        ws.cell(row=r, column=c4, value=ligne["tva"]).border = BORDURE
        ws.cell(row=r, column=c5, value=ligne["ttc"]).border = BORDURE
        for cc in (c3, c4, c5):
            ws.cell(row=r, column=cc).number_format = "0.00"
        total_ht  += ligne["ht"]
        total_tva += ligne["tva"]
        total_ttc += ligne["ttc"]
        r += 1

    # Ligne TOTAL
    ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c2)
    cell = ws.cell(row=r, column=c0, value="TOTAL")
    cell.font = FONT_TOTAL
    cell.fill = FILL_TOTAL
    cell.alignment = CENTRE
    for cc, val in ((c3, total_ht), (c4, total_tva), (c5, total_ttc)):
        cell = ws.cell(row=r, column=cc, value=round(val, 2))
        cell.font = FONT_TOTAL
        cell.fill = FILL_TOTAL
        cell.number_format = "0.00"
    for cc in range(c0, c5 + 1):
        ws.cell(row=r, column=cc).border = BORDURE

    for i in range(6):
        ws.column_dimensions[get_column_letter(col_debut + i)].width = 14

    return total_ht, total_tva, total_ttc


def generer_declaration_mensuelle_excel(texte_periode: str) -> str:
    mois, annee = _parse_mois_annee(texte_periode)
    conn = _connect()
    try:
        achats = _lignes_periode(conn, 1, mois, annee, "Fournisseur")
        ventes = _lignes_periode(conn, 0, mois, annee, "Client")
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Déclaration"
    ws.sheet_view.showGridLines = False

    # Bandeau titre (ligne 1) sur toute la largeur (achat: A-F, gap G, vente: H-M)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=13)
    titre_cell = ws.cell(row=1, column=1, value=f"DECLARATION {_MOIS_NOMS[mois]} {annee}")
    titre_cell.font = FONT_TITRE
    titre_cell.fill = FILL_TITRE
    titre_cell.alignment = CENTRE
    ws.row_dimensions[1].height = 24

    tot_achat = _bloc(ws, 1, "ACHAT", achats, "Fournisseur")
    tot_vente = _bloc(ws, 8, "VENTE", ventes, "Client")
    ws.column_dimensions["G"].width = 3  # colonne d'écart entre les 2 tableaux

    nom_fichier = f"DECLARATION_{_MOIS_NOMS[mois]}_{annee}.xlsx"
    chemin = str(OUTPUT_DIR / nom_fichier)
    wb.save(chemin)

    return json.dumps({
        "statut": "OK",
        "mois": _MOIS_NOMS[mois],
        "annee": annee,
        "fichier": chemin,
        "achat":  {"nb": len(achats), "total_ht": round(tot_achat[0], 2), "total_tva": round(tot_achat[1], 2), "total_ttc": round(tot_achat[2], 2)},
        "vente":  {"nb": len(ventes), "total_ht": round(tot_vente[0], 2), "total_tva": round(tot_vente[1], 2), "total_ttc": round(tot_vente[2], 2)},
    }, ensure_ascii=False)


if __name__ == "__main__":
    print(generer_declaration_mensuelle_excel("crée une déclaration du mois de juin 2026"))