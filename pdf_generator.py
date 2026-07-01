"""
pdf_generator.py — Génération PDF Sage 100 (BL / FACTURE / OF / BF)
====================================================================
Reproduit le template fourni (BON DE LIVRAISON N° BLxxxx) :
  - logo (placeholder texte si aucun logo fourni)
  - titre + numéro de pièce
  - date
  - bloc émetteur / bloc destinataire
  - ligne "Votre commande du : ..."
  - tableau Référence / Description / Qté commandée / Qté livrée / Observations
  - pour FACTURE : ligne supplémentaire "Timbre fiscal : 1.000 DT" + total TTC
  - zone signature / colisage (BL, BL_ACHAT) en bas
  - mode DRAFT : filigrane diagonal "BROUILLON" + bandeau rouge

Utilisable pour : BL, BL_ACHAT (bon de réception), FACTURE, FA_ACHAT,
                  BC, OF, AVOIR — chacun avec un intitulé de titre adapté.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.pdfgen import canvas as _canvas

from mcp_actions_sage import _get_conn, _resolve_article
from schema_sage import CURRENCY_SYMBOL, CURRENCY_LABEL

OUTPUT_DIR = Path(os.getenv("PDF_OUTPUT_DIR", "./documents_generes"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TIMBRE_FISCAL = 1.000  # 1 DT — timbre fiscal facture (Tunisie)
CURRENCY_UNIT = CURRENCY_SYMBOL

# Titres affichés selon le type de document
_TITRES = {
    "BL":        "BON DE LIVRAISON",
    "BL_ACHAT":  "BON DE RÉCEPTION",
    "FACTURE":   "FACTURE",
    "FA":        "FACTURE",
    "FA_ACHAT":  "FACTURE FOURNISSEUR",
    "BC":        "BON DE COMMANDE",
    "OF":        "ORDRE DE FABRICATION",
    "BF":        "BON DE FABRICATION",
    "AV":        "AVOIR",
    "AVOIR":     "AVOIR",
}

# Documents qui affichent timbre fiscal + total
_DOCS_AVEC_TOTAUX = {"FACTURE", "FA", "FA_ACHAT", "AVOIR", "AV"}
# Documents qui affichent colis/transport (BL physiques)
_DOCS_AVEC_COLISAGE = {"BL", "BL_ACHAT"}
# Documents qui affichent "qté commandée / qté livrée" (sinon une seule colonne qté)
_DOCS_AVEC_DOUBLE_QTE = {"BL", "BL_ACHAT"}
# Documents qui affichent un prix unitaire à afficher (BL inclus)
_DOCS_AVEC_PRIX = {"BL", "BL_ACHAT", "BC", "FACTURE", "FA", "FA_ACHAT", "AVOIR", "AV"}

@dataclass
class LigneDocument:
    reference: str
    description: str
    qte_commandee: float
    qte_livree: float | None = None   # None → on reprend qte_commandee
    prix_unitaire: float = 0.0
    observation: str = ""


@dataclass
class NomenclatureItem:
    ref: str
    designation: str
    qte: float
    prix_unitaire: float = 0.0
    total: float = 0.0


@dataclass
class EnteteDocument:
    type_doc: str               # BL | BL_ACHAT | FACTURE | FA_ACHAT | BC | OF | BF | AV
    num_piece: str
    date_str: str
    date_commande_str: str = ""
    emetteur_nom: str = "VOTRE NOM OU RAISON SOCIALE"
    emetteur_adresse: list[str] = field(default_factory=lambda: [
        "Adresse de votre entreprise", "",
        "Téléphone : ",
    ])
    destinataire_nom: str = "Nom du client"
    destinataire_adresse: list[str] = field(default_factory=list)
    lignes: list[LigneDocument] = field(default_factory=list)
    nomenclature: list[NomenclatureItem] = field(default_factory=list)
    mode_paiement: str = ""
    logo_path: str | None = None
    is_draft: bool = True       # True = brouillon (filigrane), False = définitif


# ─────────────────────────────────────────────────────────────────────
def _filigrane_draft(c: _canvas.Canvas, w: float, h: float):
    c.saveState()
    c.setFont("Helvetica-Bold", 90)
    c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.18))
    c.translate(w / 2, h / 2)
    c.rotate(40)
    c.drawCentredString(0, 0, "BROUILLON")
    c.restoreState()


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bandeau_draft(c: _canvas.Canvas, w: float, h: float):
    c.saveState()
    c.setFillColor(colors.Color(0.85, 0.1, 0.1))
    c.rect(0, h - 14 * mm, w, 8 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(
        w / 2, h - 11.5 * mm,
        "⚠ DOCUMENT NON VALIDÉ — APERÇU UNIQUEMENT — EN ATTENTE DE CONFIRMATION ⚠"
    )
    c.restoreState()


def _page_decor_factory(entete: EnteteDocument):
    def _decor(c: _canvas.Canvas, doc):
        w, h = A4
        if entete.is_draft:
            _filigrane_draft(c, w, h)
            _bandeau_draft(c, w, h)
        # Cadre extérieur
        c.setStrokeColor(colors.grey)
        c.setLineWidth(1)
        c.rect(10 * mm, 10 * mm, w - 20 * mm, h - 20 * mm)
    return _decor


# ─────────────────────────────────────────────────────────────────────
def generer_pdf(entete: EnteteDocument) -> str:
    """
    Génère le PDF et retourne le chemin du fichier créé.
    """
    titre = _TITRES.get(entete.type_doc.upper(), entete.type_doc.upper())
    suffixe = "_DRAFT" if entete.is_draft else ""
    nom_fichier = f"{entete.type_doc.upper()}_{entete.num_piece}{suffixe}.pdf"
    chemin = str(OUTPUT_DIR / nom_fichier)

    top_margin = 24 * mm if entete.is_draft else 16 * mm
    doc = SimpleDocTemplate(
        chemin, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=top_margin, bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle(
        "normal9", parent=styles["Normal"], fontSize=9, leading=12,
    )
    style_bold = ParagraphStyle(
        "bold9", parent=style_normal, fontName="Helvetica-Bold",
    )
    style_titre = ParagraphStyle(
        "titre", parent=styles["Title"], fontSize=20, alignment=TA_CENTER,
        spaceAfter=2,
    )
    style_logo = ParagraphStyle(
        "logo", parent=style_normal, fontName="Helvetica-Oblique", fontSize=9,
    )
    style_centre_gras = ParagraphStyle(
        "centre_gras", parent=style_bold, alignment=TA_CENTER,
    )

    story = []

    # ── En-tête : logo (gauche) / Titre + N° (centre) ──────────────────
    if entete.logo_path and Path(entete.logo_path).exists():
        from reportlab.platypus import Image as RLImage
        logo_cell = RLImage(entete.logo_path, width=28 * mm, height=18 * mm)
    else:
        logo_cell = Paragraph("votre logo", style_logo)

    titre_txt = f"{titre} N° {entete.num_piece}"
    titre_cell = Paragraph(titre_txt, style_titre)

    t_head = Table([[logo_cell, titre_cell]], colWidths=[35 * mm, 130 * mm])
    t_head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
    ]))
    story.append(t_head)
    story.append(Spacer(1, 6))

    # ── Date ──────────────────────────────────────────────────────────
    date_para = Paragraph(f"<b>Date :</b> &nbsp;&nbsp; {entete.date_str}", style_normal)
    t_date = Table([["", date_para]], colWidths=[90 * mm, 75 * mm])
    t_date.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story.append(t_date)
    story.append(Spacer(1, 8))

    # ── Bloc émetteur / destinataire ────────────────────────────────────
    bloc_emetteur = [Paragraph(f"<b>{entete.emetteur_nom}</b>", style_bold)]
    for ligne in entete.emetteur_adresse:
        bloc_emetteur.append(Paragraph(ligne, style_normal))

    label_dest = "Fournisseur" if entete.type_doc.upper() in ("BL_ACHAT", "FA_ACHAT") else "Client"
    bloc_dest = [
        Paragraph(f"<b>A :</b>  {entete.destinataire_nom}", style_bold),
    ]
    for ligne in entete.destinataire_adresse:
        bloc_dest.append(Paragraph(f"<b>{'' }</b>{ligne}", style_normal))
    if entete.mode_paiement:
        bloc_dest.append(Paragraph(f"<b>Règlement :</b> {entete.mode_paiement}", style_normal))

    t_parties = Table(
        [[bloc_emetteur, bloc_dest]],
        colWidths=[90 * mm, 75 * mm],
    )
    t_parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.grey),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t_parties)
    story.append(Spacer(1, 10))

    if entete.date_commande_str:
        label_ref = "Votre commande du" if entete.type_doc.upper() != "OF" else "Lancement fabrication du"
        story.append(Paragraph(
            f"<b>{label_ref} : {entete.date_commande_str}</b>", style_centre_gras
        ))
        story.append(Spacer(1, 6))

    # ── Tableau des lignes ───────────────────────────────────────────
    avec_double_qte = entete.type_doc.upper() in _DOCS_AVEC_DOUBLE_QTE
    avec_totaux = entete.type_doc.upper() in _DOCS_AVEC_TOTAUX
    avec_prix = entete.type_doc.upper() in _DOCS_AVEC_PRIX

    if avec_double_qte:
        if avec_prix:
            header = ["Référence", "Description", "Qté\ncommandée", "Qté\nlivrée", f"PU ({CURRENCY_UNIT})", "Observations"]
            col_widths = [20 * mm, 55 * mm, 18 * mm, 18 * mm, 22 * mm, 24 * mm]
        else:
            header = ["Référence", "Description", "Qté\ncommandée", "Qté\nlivrée", "Observations"]
            col_widths = [22 * mm, 70 * mm, 22 * mm, 22 * mm, 33 * mm]
    elif avec_totaux:
        header = ["Référence", "Description", "Qté", f"PU ({CURRENCY_UNIT})", "Total HT"]
        col_widths = [22 * mm, 65 * mm, 18 * mm, 28 * mm, 36 * mm]
    elif avec_prix:
        header = ["Référence", "Description", "Quantité", "PU (TND)", "Observations"]
        col_widths = [24 * mm, 70 * mm, 22 * mm, 28 * mm, 30 * mm]
    else:
        header = ["Référence", "Description", "Quantité", "Observations"]
        col_widths = [24 * mm, 85 * mm, 25 * mm, 35 * mm]

    data = [header]
    total_ht = Decimal("0.00")
    for l in entete.lignes:
        qte_l = l.qte_livree if l.qte_livree is not None else l.qte_commandee
        if avec_double_qte:
            if avec_prix:
                total_ht += _to_decimal(Decimal(str(l.qte_commandee)) * Decimal(str(l.prix_unitaire)))
                data.append([
                    l.reference, l.description, f"{l.qte_commandee:g}", f"{qte_l:g}",
                    f"{l.prix_unitaire:.3f}", l.observation,
                ])
            else:
                data.append([l.reference, l.description, f"{l.qte_commandee:g}", f"{qte_l:g}", l.observation])
        elif avec_totaux:
            tot = l.qte_commandee * l.prix_unitaire
            total_ht += tot
            data.append([l.reference, l.description, f"{l.qte_commandee:g}",
                         f"{l.prix_unitaire:.3f}", f"{tot:.3f}"])
        elif avec_prix:
            tot = l.qte_commandee * l.prix_unitaire
            total_ht += tot
            data.append([l.reference, l.description, f"{l.qte_commandee:g}",
                         f"{l.prix_unitaire:.3f}", l.observation])
        else:
            data.append([l.reference, l.description, f"{l.qte_commandee:g}", l.observation])

    # Nomenclature et matières premières pour OF/BF
    if entete.nomenclature and entete.type_doc.upper() in {"OF", "BF"}:
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>Nomenclature / Matières premières</b>", style_bold))
        story.append(Spacer(1, 4))
        data_n = [[
            "Réf. composant", "Désignation", "Qté", f"PU ({CURRENCY_UNIT})", f"Total {CURRENCY_UNIT}"
        ]]
        for item in entete.nomenclature:
            data_n.append([
                item.ref,
                item.designation,
                f"{item.qte:g}",
                f"{item.prix_unitaire:.3f}" if item.prix_unitaire else "",
                f"{item.total:.3f}" if item.total else "",
            ])
        while len(data_n) < 6:
            data_n.append([""] * len(data_n[0]))
        t_nomenclature = Table(data_n, colWidths=[30 * mm, 70 * mm, 24 * mm, 25 * mm, 25 * mm])
        t_nomenclature.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#BFBFBF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (2, 1), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_nomenclature)

    # Remplissage visuel jusqu'à un minimum de lignes (esthétique template)
    while len(data) < 11:
        data.append([""] * len(header))

    t_lignes = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#BFBFBF")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (2, 0), (-1, 0), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "CENTER") if not avec_totaux else
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWHEIGHT", (0, 1), (-1, -1), 7 * mm),
    ]
    t_lignes.setStyle(TableStyle(style_cmds))
    story.append(t_lignes)

    # ── Totaux ─────────────────────────────────────────────────────
    if avec_totaux:
        story.append(Spacer(1, 6))
        timbre = TIMBRE_FISCAL
        total_ttc = total_ht + timbre
        data_tot = [
            ["", "Total HT (TND)", f"{total_ht:.3f}"],
            ["", "Timbre fiscal (TND)", f"{timbre:.3f}"],
            ["", "Total TTC (TND)", f"{total_ttc:.3f}"],
        ]
        t_tot = Table(data_tot, colWidths=[97 * mm, 40 * mm, 32 * mm])
        t_tot.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (1, 2), (-1, 2), "Helvetica-Bold"),
            ("LINEABOVE", (1, 2), (-1, 2), 0.8, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_tot)
    elif avec_prix and total_ht > 0:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"<b>Montant total HT : {total_ht:.3f} TND</b>",
            ParagraphStyle("total_simple", parent=style_bold, fontSize=11, alignment=TA_RIGHT),
        ))

    story.append(Spacer(1, 14))

    # ── Zone basse : signature + colisage (BL) OU mention libre ─────
    if entete.type_doc.upper() in _DOCS_AVEC_COLISAGE:
        bloc_sign = [Paragraph("<b>Date de réception et visa :</b>", style_normal),
                     Spacer(1, 30)]
        colis_data = [["Colis", "Poids", "Dimensions"]]
        for i in range(1, 6):
            colis_data.append([f"Colis {i}", "", ""])
        t_colis = Table(colis_data, colWidths=[25 * mm, 25 * mm, 45 * mm])
        t_colis.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#BFBFBF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        bloc_colis = [Paragraph("<b>Colisage :</b>", style_normal), Spacer(1, 4), t_colis]
        t_bas = Table([[bloc_sign, bloc_colis]], colWidths=[85 * mm, 80 * mm])
        t_bas.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t_bas)
    elif entete.type_doc.upper() == "OF":
        story.append(Paragraph(
            "<b>Visa atelier / responsable production :</b>", style_normal))
        story.append(Spacer(1, 20))
        story.append(Paragraph(
            "⚠️ Document interne — non destiné à la facturation.", style_normal))
    elif entete.type_doc.upper() == "BF":
        story.append(Paragraph(
            "<b>Contrôle qualité / réception magasin :</b>", style_normal))
        story.append(Spacer(1, 20))
    else:
        story.append(Paragraph("<b>Signature :</b>", style_normal))
        story.append(Spacer(1, 24))

    decor = _page_decor_factory(entete)
    doc.build(story, onFirstPage=decor, onLaterPages=decor)
    return chemin


# ─────────────────────────────────────────────────────────────────────
# Adaptateur : construit un EnteteDocument depuis le "draft" de
# l'orchestrateur (dict simple) + éventuellement les infos résolues
# (intitulé client/fournisseur, désignation article) côté MCP.
# ─────────────────────────────────────────────────────────────────────
def entete_depuis_draft(draft: dict, is_draft: bool = True) -> EnteteDocument:
    from datetime import datetime

    type_doc = (draft.get("type_doc") or "BL").upper()
    num_piece = draft.get("num_piece") or "EN ATTENTE"
    date_str = draft.get("date_str") or datetime.now().strftime("%d/%m/%Y")

    nom_dest = (
        draft.get("intitule_fournisseur") or draft.get("intitule_client")
        or draft.get("nom_fournisseur") or draft.get("nom_client")
        or draft.get("code_fournisseur") or draft.get("code_client") or "—"
    )

    ligne = LigneDocument(
        reference=draft.get("ref_article", "—"),
        # FIX : utiliser la désignation réelle si dispo, sinon la ref en dernier recours
        description=draft.get("designation_article") or draft.get("ref_article", ""),
        qte_commandee=float(draft.get("quantite", 0) or 0),
        qte_livree=float(draft.get("quantite", 0) or 0),
        prix_unitaire=float(draft.get("prix_unitaire", 0) or 0),
        observation=draft.get("observation", ""),
    )

    nomenclature_items = []
    for item in draft.get("nomenclature", []) or []:
        if not isinstance(item, dict):
            continue
        nomenclature_items.append(NomenclatureItem(
            ref=str(item.get("ref", "")),
            designation=str(item.get("designation", "")),
            qte=float(item.get("qte", 0) or 0),
            prix_unitaire=float(item.get("prix_unitaire", 0) or 0),
            total=float(item.get("total", 0) or 0),
        ))

    prix_unitaire = float(draft.get("prix_unitaire", 0) or 0)
    if prix_unitaire <= 0 and type_doc in _DOCS_AVEC_PRIX and draft.get("ref_article"):
        conn = _get_conn()
        try:
            article = _resolve_article(conn, draft["ref_article"])
            if article:
                prix_unitaire = float(article.get("AR_PrixVen", 0) or 0)
                draft["prix_unitaire"] = prix_unitaire
        except Exception:
            prix_unitaire = 0.0
        finally:
            conn.close()

    ligne = LigneDocument(
        reference=draft.get("ref_article", "—"),
        # FIX : utiliser la désignation réelle si dispo, sinon la ref en dernier recours
        description=draft.get("designation_article") or draft.get("ref_article", ""),
        qte_commandee=float(draft.get("quantite", 0) or 0),
        qte_livree=float(draft.get("quantite", 0) or 0),
        prix_unitaire=prix_unitaire,
        observation=draft.get("observation", ""),
    )

    nomenclature_items = []
    for item in draft.get("nomenclature", []) or []:
        if not isinstance(item, dict):
            continue
        nomenclature_items.append(NomenclatureItem(
            ref=str(item.get("ref", "")),
            designation=str(item.get("designation", "")),
            qte=float(item.get("qte", 0) or 0),
            prix_unitaire=float(item.get("prix_unitaire", 0) or 0),
            total=float(item.get("total", 0) or 0),
        ))

    return EnteteDocument(
        type_doc=type_doc,
        num_piece=num_piece,
        date_str=date_str,
        date_commande_str=draft.get("date_commande_str", date_str),
        destinataire_nom=nom_dest,
        destinataire_adresse=draft.get("adresse_dest", []),
        lignes=[ligne],
        nomenclature=nomenclature_items,
        mode_paiement=draft.get("mode_paiement", ""),
        logo_path=draft.get("logo_path"),
        is_draft=is_draft,
    )


async def generer_pdf_async(draft: dict, is_draft: bool = True) -> str:
    """Wrapper asynchrone pour usage dans l'orchestrateur (asyncio.to_thread)."""
    import asyncio
    entete = entete_depuis_draft(draft, is_draft=is_draft)
    return await asyncio.to_thread(generer_pdf, entete)