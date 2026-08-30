"""
Création d'un modèle PDF pour la génération de documents commerciaux tels que des bons de livraison, des factures, etc.
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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfgen import canvas as _canvas
from api.mcp_actions_sage import _get_conn, _resolve_article
from database.schema_sage import CURRENCY_SYMBOL, CURRENCY_LABEL
OUTPUT_DIR = Path(os.getenv('PDF_OUTPUT_DIR', './documents_generes'))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TIMBRE_FISCAL = 1.0
CURRENCY_UNIT = CURRENCY_SYMBOL
_TITRES = {'BL': 'BON DE LIVRAISON', 'BL_ACHAT': 'BON DE RÉCEPTION', 'FACTURE': 'FACTURE', 'FA': 'FACTURE', 'FA_ACHAT': 'FACTURE FOURNISSEUR', 'BC': 'BON DE COMMANDE', 'OF': 'ORDRE DE FABRICATION', 'BF': 'BON DE FABRICATION', 'AV': 'AVOIR', 'AVOIR': 'AVOIR', 'OFFRE_PRIX': 'OFFRE DE PRIX'}
_DOCS_AVEC_TOTAUX = {'FACTURE', 'FA', 'FA_ACHAT', 'AVOIR', 'AV'}
_DOCS_AVEC_COLISAGE = {'BL', 'BL_ACHAT'}
_DOCS_AVEC_DOUBLE_QTE = {'BL', 'BL_ACHAT'}
_DOCS_AVEC_PRIX = {'BL', 'BL_ACHAT', 'BC', 'FACTURE', 'FA', 'FA_ACHAT', 'AVOIR', 'AV'}

@dataclass
class LigneDocument:
    """
La classe `LigneDocument` permet de représenter une ligne d'un document, avec des informations sur la quantité commandée et livrée, le prix unitaire et les observations associées.
"""
    reference: str
    description: str
    qte_commandee: float
    qte_livree: float | None = None
    prix_unitaire: float = 0.0
    observation: str = ''

@dataclass
class NomenclatureItem:
    """
Modèle pour un article de nomenclature.
"""
    ref: str
    designation: str
    qte: float
    prix_unitaire: float = 0.0
    total: float = 0.0

@dataclass
class EnteteDocument:
    """
La fonction EnteteDocument définit un modèle de données pour l'en-tête d'un document commercial. 

Cette classe contient les informations essentielles pour l'en-tête d'un document commercial tels que le type de document, le numéro de pièce, les dates, les informations sur l'emetteur et le destinataire, ainsi que les informations sur le paiement et le logo.
"""
    type_doc: str
    num_piece: str
    date_str: str
    date_commande_str: str = ''
    emetteur_nom: str = 'VOTRE NOM OU RAISON SOCIALE'
    emetteur_adresse: list[str] = field(default_factory=lambda: ['Adresse de votre entreprise', '', 'Téléphone : '])
    destinataire_nom: str = 'Nom du client'
    destinataire_adresse: list[str] = field(default_factory=list)
    lignes: list[LigneDocument] = field(default_factory=list)
    nomenclature: list[NomenclatureItem] = field(default_factory=list)
    mode_paiement: str = ''
    logo_path: str | None = None
    is_draft: bool = True

def _filigrane_draft(c: _canvas.Canvas, w: float, h: float):
    """
Définit une filigrane de brouillon sur une page, traduisant la phrase en rotation.
"""
    c.saveState()
    c.setFont('Helvetica-Bold', 90)
    c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.18))
    c.translate(w / 2, h / 2)
    c.rotate(40)
    c.drawCentredString(0, 0, 'BROUILLON')
    c.restoreState()

def _to_decimal(value: object) -> Decimal:
    """
Convertit un nombre en décimal et arrondit à 2 décimales.
"""
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def _bandeau_draft(c: _canvas.Canvas, w: float, h: float):
    """
Créer un bandeau de notification dans un document PDF.
"""
    c.saveState()
    c.setFillColor(colors.Color(0.85, 0.1, 0.1))
    c.rect(0, h - 14 * mm, w, 8 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 11)
    c.drawCentredString(w / 2, h - 11.5 * mm, '⚠ DOCUMENT NON VALIDÉ — APERÇU UNIQUEMENT — EN ATTENTE DE CONFIRMATION ⚠')
    c.restoreState()

def _page_decor_factory(entete: EnteteDocument):
    """
Fonction de fabrication de décor de page pour les documents.
"""

    def _decor(c: _canvas.Canvas, doc):
        """
Cette fonction dessine un cadre autour du contenu d'un document sur une feuille A4.
"""
        w, h = A4
        if entete.is_draft:
            _filigrane_draft(c, w, h)
            _bandeau_draft(c, w, h)
        c.setStrokeColor(colors.grey)
        c.setLineWidth(1)
        c.rect(10 * mm, 10 * mm, w - 20 * mm, h - 20 * mm)
    return _decor

def generer_pdf(entete: EnteteDocument) -> str:
    """
Crée un PDF à partir d'une entête de document.
"""
    titre = _TITRES.get(entete.type_doc.upper(), entete.type_doc.upper())
    suffixe = '_DRAFT' if entete.is_draft else ''
    nom_fichier = f'{entete.type_doc.upper()}_{entete.num_piece}{suffixe}.pdf'
    chemin = str(OUTPUT_DIR / nom_fichier)
    top_margin = 24 * mm if entete.is_draft else 16 * mm
    doc = SimpleDocTemplate(chemin, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=top_margin, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('normal9', parent=styles['Normal'], fontSize=9, leading=12)
    style_bold = ParagraphStyle('bold9', parent=style_normal, fontName='Helvetica-Bold')
    style_titre = ParagraphStyle('titre', parent=styles['Title'], fontSize=20, alignment=TA_CENTER, spaceAfter=2)
    style_logo = ParagraphStyle('logo', parent=style_normal, fontName='Helvetica-Oblique', fontSize=9)
    style_centre_gras = ParagraphStyle('centre_gras', parent=style_bold, alignment=TA_CENTER)
    story = []
    if entete.logo_path and Path(entete.logo_path).exists():
        from reportlab.platypus import Image as RLImage
        logo_cell = RLImage(entete.logo_path, width=28 * mm, height=18 * mm)
    else:
        logo_cell = Paragraph('votre logo', style_logo)
    titre_txt = f'{titre} N° {entete.num_piece}'
    titre_cell = Paragraph(titre_txt, style_titre)
    t_head = Table([[logo_cell, titre_cell]], colWidths=[35 * mm, 130 * mm])
    t_head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (1, 0), (1, 0), 'CENTER')]))
    story.append(t_head)
    story.append(Spacer(1, 6))
    date_para = Paragraph(f'<b>Date :</b> &nbsp;&nbsp; {entete.date_str}', style_normal)
    t_date = Table([['', date_para]], colWidths=[90 * mm, 75 * mm])
    t_date.setStyle(TableStyle([('ALIGN', (1, 0), (1, 0), 'RIGHT')]))
    story.append(t_date)
    story.append(Spacer(1, 8))
    bloc_emetteur = [Paragraph(f'<b>{entete.emetteur_nom}</b>', style_bold)]
    for ligne in entete.emetteur_adresse:
        bloc_emetteur.append(Paragraph(ligne, style_normal))
    label_dest = 'Fournisseur' if entete.type_doc.upper() in ('BL_ACHAT', 'FA_ACHAT') else 'Client'
    bloc_dest = [Paragraph(f'<b>A :</b>  {entete.destinataire_nom}', style_bold)]
    for ligne in entete.destinataire_adresse:
        bloc_dest.append(Paragraph(f"<b>{''}</b>{ligne}", style_normal))
    if entete.mode_paiement:
        bloc_dest.append(Paragraph(f'<b>Règlement :</b> {entete.mode_paiement}', style_normal))
    t_parties = Table([[bloc_emetteur, bloc_dest]], colWidths=[90 * mm, 75 * mm])
    t_parties.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, colors.grey), ('LINEAFTER', (0, 0), (0, 0), 0.5, colors.grey), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    story.append(t_parties)
    story.append(Spacer(1, 10))
    if entete.date_commande_str:
        label_ref = 'Votre commande du' if entete.type_doc.upper() != 'OF' else 'Lancement fabrication du'
        story.append(Paragraph(f'<b>{label_ref} : {entete.date_commande_str}</b>', style_centre_gras))
        story.append(Spacer(1, 6))
    avec_double_qte = entete.type_doc.upper() in _DOCS_AVEC_DOUBLE_QTE
    avec_totaux = entete.type_doc.upper() in _DOCS_AVEC_TOTAUX
    avec_prix = entete.type_doc.upper() in _DOCS_AVEC_PRIX
    if avec_double_qte:
        if avec_prix:
            header = ['Référence', 'Description', 'Qté\ncommandée', 'Qté\nlivrée', f'PU ({CURRENCY_UNIT})', 'Observations']
            col_widths = [20 * mm, 55 * mm, 18 * mm, 18 * mm, 22 * mm, 24 * mm]
        else:
            header = ['Référence', 'Description', 'Qté\ncommandée', 'Qté\nlivrée', 'Observations']
            col_widths = [22 * mm, 70 * mm, 22 * mm, 22 * mm, 33 * mm]
    elif avec_totaux:
        header = ['Référence', 'Description', 'Qté', f'PU ({CURRENCY_UNIT})', 'Total HT']
        col_widths = [22 * mm, 65 * mm, 18 * mm, 28 * mm, 36 * mm]
    elif avec_prix:
        header = ['Référence', 'Description', 'Quantité', 'PU (TND)', 'Observations']
        col_widths = [24 * mm, 70 * mm, 22 * mm, 28 * mm, 30 * mm]
    else:
        header = ['Référence', 'Description', 'Quantité', 'Observations']
        col_widths = [24 * mm, 85 * mm, 25 * mm, 35 * mm]
    data = [header]
    total_ht = Decimal('0.00')
    for l in entete.lignes:
        qte_l = l.qte_livree if l.qte_livree is not None else l.qte_commandee
        if avec_double_qte:
            if avec_prix:
                total_ht += _to_decimal(Decimal(str(l.qte_commandee)) * Decimal(str(l.prix_unitaire)))
                data.append([l.reference, l.description, f'{l.qte_commandee:g}', f'{qte_l:g}', f'{l.prix_unitaire:.3f}', l.observation])
            else:
                data.append([l.reference, l.description, f'{l.qte_commandee:g}', f'{qte_l:g}', l.observation])
        elif avec_totaux:
            tot = _to_decimal(Decimal(str(l.qte_commandee)) * Decimal(str(l.prix_unitaire)))
            total_ht += tot
            data.append([l.reference, l.description, f'{l.qte_commandee:g}', f'{l.prix_unitaire:.3f}', f'{tot:.3f}'])
        elif avec_prix:
            tot = _to_decimal(Decimal(str(l.qte_commandee)) * Decimal(str(l.prix_unitaire)))
            total_ht += tot
            data.append([l.reference, l.description, f'{l.qte_commandee:g}', f'{l.prix_unitaire:.3f}', l.observation])
        else:
            data.append([l.reference, l.description, f'{l.qte_commandee:g}', l.observation])
    if entete.nomenclature and entete.type_doc.upper() in {'OF', 'BF'}:
        story.append(Spacer(1, 10))
        story.append(Paragraph('<b>Nomenclature / Matières premières</b>', style_bold))
        story.append(Spacer(1, 4))
        data_n = [['Réf. composant', 'Désignation', 'Qté', f'PU ({CURRENCY_UNIT})', f'Total {CURRENCY_UNIT}']]
        for item in entete.nomenclature:
            data_n.append([item.ref, item.designation, f'{item.qte:g}', f'{item.prix_unitaire:.3f}' if item.prix_unitaire else '', f'{item.total:.3f}' if item.total else ''])
        while len(data_n) < 6:
            data_n.append([''] * len(data_n[0]))
        t_nomenclature = Table(data_n, colWidths=[30 * mm, 70 * mm, 24 * mm, 25 * mm, 25 * mm])
        t_nomenclature.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#BFBFBF')), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('ALIGN', (2, 1), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('GRID', (0, 0), (-1, -1), 0.4, colors.grey), ('BOX', (0, 0), (-1, -1), 1, colors.black), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        story.append(t_nomenclature)
    while len(data) < 11:
        data.append([''] * len(header))
    t_lignes = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#BFBFBF')), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('ALIGN', (2, 0), (-1, 0), 'CENTER'), ('ALIGN', (2, 1), (-1, -1), 'CENTER') if not avec_totaux else ('ALIGN', (2, 1), (-1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('GRID', (0, 0), (-1, -1), 0.4, colors.grey), ('BOX', (0, 0), (-1, -1), 1, colors.black), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4), ('ROWHEIGHT', (0, 1), (-1, -1), 7 * mm)]
    t_lignes.setStyle(TableStyle(style_cmds))
    story.append(t_lignes)
    if avec_totaux:
        story.append(Spacer(1, 6))
        timbre = _to_decimal(TIMBRE_FISCAL)
        total_ttc = total_ht + timbre
        data_tot = [['', 'Total HT (TND)', f'{total_ht:.3f}'], ['', 'Timbre fiscal (TND)', f'{timbre:.3f}'], ['', 'Total TTC (TND)', f'{total_ttc:.3f}']]
        t_tot = Table(data_tot, colWidths=[97 * mm, 40 * mm, 32 * mm])
        t_tot.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 9.5), ('ALIGN', (1, 0), (-1, -1), 'RIGHT'), ('FONTNAME', (1, 2), (-1, 2), 'Helvetica-Bold'), ('LINEABOVE', (1, 2), (-1, 2), 0.8, colors.black), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        story.append(t_tot)
    elif avec_prix and total_ht > 0:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f'<b>Montant total HT : {total_ht:.3f} TND</b>', ParagraphStyle('total_simple', parent=style_bold, fontSize=11, alignment=TA_RIGHT)))
    story.append(Spacer(1, 14))
    if entete.type_doc.upper() in _DOCS_AVEC_COLISAGE:
        bloc_sign = [Paragraph('<b>Date de réception et visa :</b>', style_normal), Spacer(1, 30)]
        colis_data = [['Colis', 'Poids', 'Dimensions']]
        for i in range(1, 6):
            colis_data.append([f'Colis {i}', '', ''])
        t_colis = Table(colis_data, colWidths=[25 * mm, 25 * mm, 45 * mm])
        t_colis.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#BFBFBF')), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8), ('GRID', (0, 0), (-1, -1), 0.4, colors.grey), ('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
        bloc_colis = [Paragraph('<b>Colisage :</b>', style_normal), Spacer(1, 4), t_colis]
        t_bas = Table([[bloc_sign, bloc_colis]], colWidths=[85 * mm, 80 * mm])
        t_bas.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story.append(t_bas)
    elif entete.type_doc.upper() == 'OF':
        story.append(Paragraph('<b>Visa atelier / responsable production :</b>', style_normal))
        story.append(Spacer(1, 20))
        story.append(Paragraph('⚠️ Document interne — non destiné à la facturation.', style_normal))
    elif entete.type_doc.upper() == 'BF':
        story.append(Paragraph('<b>Contrôle qualité / réception magasin :</b>', style_normal))
        story.append(Spacer(1, 20))
    else:
        story.append(Paragraph('<b>Signature :</b>', style_normal))
        story.append(Spacer(1, 24))
    decor = _page_decor_factory(entete)
    doc.build(story, onFirstPage=decor, onLaterPages=decor)
    return chemin

def entete_depuis_draft(draft: dict, is_draft: bool=True) -> EnteteDocument:
    """
Crée une entête de document à partir d'un ébauche.
"""
    from datetime import datetime
    type_doc = (draft.get('type_doc') or 'BL').upper()
    num_piece = draft.get('num_piece') or 'EN ATTENTE'
    date_str = draft.get('date_str') or datetime.now().strftime('%d/%m/%Y')
    nom_dest = draft.get('intitule_fournisseur') or draft.get('intitule_client') or draft.get('nom_fournisseur') or draft.get('nom_client') or draft.get('code_fournisseur') or draft.get('code_client') or '—'
    ligne = LigneDocument(reference=draft.get('ref_article', '—'), description=draft.get('designation_article') or draft.get('ref_article', ''), qte_commandee=float(draft.get('quantite', 0) or 0), qte_livree=float(draft.get('quantite', 0) or 0), prix_unitaire=float(draft.get('prix_unitaire', 0) or 0), observation=draft.get('observation', ''))
    nomenclature_items = []
    for item in draft.get('nomenclature', []) or []:
        if not isinstance(item, dict):
            continue
        nomenclature_items.append(NomenclatureItem(ref=str(item.get('ref', '')), designation=str(item.get('designation', '')), qte=float(item.get('qte', 0) or 0), prix_unitaire=float(item.get('prix_unitaire', 0) or 0), total=float(item.get('total', 0) or 0)))
    prix_unitaire = float(draft.get('prix_unitaire', 0) or 0)
    if prix_unitaire <= 0 and type_doc in _DOCS_AVEC_PRIX and draft.get('ref_article'):
        conn = _get_conn()
        try:
            article = _resolve_article(conn, draft['ref_article'])
            if article:
                prix_unitaire = float(article.get('AR_PrixVen', 0) or 0)
                draft['prix_unitaire'] = prix_unitaire
        except Exception:
            prix_unitaire = 0.0
        finally:
            conn.close()
    ligne = LigneDocument(reference=draft.get('ref_article', '—'), description=draft.get('designation_article') or draft.get('ref_article', ''), qte_commandee=float(draft.get('quantite', 0) or 0), qte_livree=float(draft.get('quantite', 0) or 0), prix_unitaire=prix_unitaire, observation=draft.get('observation', ''))
    nomenclature_items = []
    for item in draft.get('nomenclature', []) or []:
        if not isinstance(item, dict):
            continue
        nomenclature_items.append(NomenclatureItem(ref=str(item.get('ref', '')), designation=str(item.get('designation', '')), qte=float(item.get('qte', 0) or 0), prix_unitaire=float(item.get('prix_unitaire', 0) or 0), total=float(item.get('total', 0) or 0)))
    return EnteteDocument(type_doc=type_doc, num_piece=num_piece, date_str=date_str, date_commande_str=draft.get('date_commande_str', date_str), destinataire_nom=nom_dest, destinataire_adresse=draft.get('adresse_dest', []), lignes=[ligne], nomenclature=nomenclature_items, mode_paiement=draft.get('mode_paiement', ''), logo_path=draft.get('logo_path'), is_draft=is_draft)

async def generer_pdf_async(draft: dict, is_draft: bool=True) -> str:
    """
Génère un PDF à partir d'un fichier de draft.
"""
    import asyncio
    entete = entete_depuis_draft(draft, is_draft=is_draft)
    return await asyncio.to_thread(generer_pdf, entete)

def generer_pdf_offre_prix_doc(draft: dict) -> str:
    """
Créer un PDF contenant l'offre de prix d'un document de vente.
"""
    from datetime import datetime as _dt
    from decimal import Decimal
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    articles = draft.get('articles', [])
    remise_pct = float(draft.get('remise_pct') or 0.0)
    date_str = draft.get('date_str') or _dt.now().strftime('%d/%m/%Y')
    num_offre = 'OP-' + _dt.now().strftime('%y%m%d%H%M%S')
    nom_fichier = f'OFFRE_PRIX_{num_offre}.pdf'
    chemin = str(OUTPUT_DIR / nom_fichier)
    doc = SimpleDocTemplate(chemin, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=20 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('op_normal', parent=styles['Normal'], fontSize=9, leading=12)
    style_bold = ParagraphStyle('op_bold', parent=style_normal, fontName='Helvetica-Bold')
    style_titre = ParagraphStyle('op_titre', parent=styles['Title'], fontSize=18, alignment=TA_CENTER, spaceAfter=4)
    style_right = ParagraphStyle('op_right', parent=style_normal, alignment=TA_RIGHT)
    style_kb = ParagraphStyle('op_kb', parent=style_normal, fontSize=8, textColor=colors.HexColor('#555555'), leading=11, leftIndent=4)
    story = []
    logo_cell = Paragraph('votre logo', ParagraphStyle('op_logo', parent=style_normal, fontName='Helvetica-Oblique', fontSize=9))
    titre_cell = Paragraph(f'OFFRE DE PRIX N° {num_offre}', style_titre)
    date_cell = Paragraph(f'<b>Date :</b>&nbsp;&nbsp;{date_str}', style_right)
    t_head = Table([[logo_cell, titre_cell, date_cell]], colWidths=[35 * mm, 105 * mm, 45 * mm])
    t_head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (1, 0), (1, 0), 'CENTER'), ('ALIGN', (2, 0), (2, 0), 'RIGHT')]))
    story.append(t_head)
    story.append(Spacer(1, 10))
    from reportlab.platypus import HRFlowable
    story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#2C3E50')))
    story.append(Spacer(1, 8))
    avec_remise = remise_pct > 0.0
    if avec_remise:
        header_row = [Paragraph('<b>Référence</b>', style_bold), Paragraph('<b>Désignation / Informations</b>', style_bold), Paragraph('<b>Prix Unitaire</b>', style_bold), Paragraph('<b>Remise %</b>', style_bold), Paragraph('<b>Prix Net</b>', style_bold)]
        col_widths_main = [28 * mm, 75 * mm, 28 * mm, 22 * mm, 24 * mm]
    else:
        header_row = [Paragraph('<b>Référence</b>', style_bold), Paragraph('<b>Désignation / Informations</b>', style_bold), Paragraph('<b>Prix Unitaire</b>', style_bold), Paragraph('<b>Total HT</b>', style_bold)]
        col_widths_main = [28 * mm, 87 * mm, 28 * mm, 27 * mm]
    data_main = [header_row]
    total_ht = Decimal('0.00')
    for art in articles:
        ref = art.get('ref', '—')
        desig = art.get('designation', ref)
        infos_kb = (art.get('infos_kb') or '').strip()
        prix_u = float(art.get('prix_final') or art.get('prix_suggere') or 0.0)
        remise_val = round(prix_u * remise_pct / 100, 3)
        prix_net = round(prix_u - remise_val, 3)
        total_ht += _to_decimal(prix_net)
        desc_parts = [f'<b>{desig}</b>']
        if infos_kb:
            desc_parts.append(f"<i><font size='7.5'>{infos_kb}</font></i>")
        desc_para = Paragraph('<br/>'.join(desc_parts), style_normal)
        if avec_remise:
            data_main.append([ref, desc_para, f'{prix_u:.3f}', f'{remise_pct:.0f}%', f'{prix_net:.3f}'])
        else:
            data_main.append([ref, desc_para, f'{prix_u:.3f}', f'{prix_net:.3f}'])
    while len(data_main) < 6:
        data_main.append([''] * len(header_row))
    t_main = Table(data_main, colWidths=col_widths_main, repeatRows=1)
    style_cmds_main = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2C3E50')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('ALIGN', (2, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#BDC3C7')), ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#2C3E50')), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('LEFTPADDING', (0, 0), (-1, -1), 4), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F2F3F4')])]
    t_main.setStyle(TableStyle(style_cmds_main))
    story.append(t_main)
    story.append(Spacer(1, 10))
    data_totaux = []
    if avec_remise:
        total_brut = sum((float(art.get('prix_final') or art.get('prix_suggere') or 0) for art in articles))
        data_totaux.append(['', 'Total HT brut (TND)', f'{total_brut:.3f}'])
        data_totaux.append(['', f'Remise {remise_pct:.0f}%', f'- {total_brut - float(total_ht):.3f}'])
        data_totaux.append(['', Paragraph('<b>Total HT net (TND)</b>', style_bold), Paragraph(f'<b>{float(total_ht):.3f}</b>', style_bold)])
    else:
        data_totaux.append(['', Paragraph('<b>Total HT (TND)</b>', style_bold), Paragraph(f'<b>{float(total_ht):.3f}</b>', style_bold)])
    t_tot = Table(data_totaux, colWidths=[100 * mm, 50 * mm, 37 * mm])
    t_tot.setStyle(TableStyle([('ALIGN', (1, 0), (-1, -1), 'RIGHT'), ('FONTSIZE', (0, 0), (-1, -1), 9.5), ('LINEABOVE', (1, -1), (-1, -1), 0.8, colors.HexColor('#2C3E50')), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    story.append(t_tot)
    story.append(Spacer(1, 20))
    story.append(Paragraph("Cette offre est valable 30 jours à compter de la date d'émission.", ParagraphStyle('op_mention', parent=style_normal, textColor=colors.HexColor('#7F8C8D'), fontSize=8)))
    story.append(Spacer(1, 16))
    sign_data = [['Signature client :', 'Cachet et signature :']]
    sign_table = Table(sign_data, colWidths=[85 * mm, 85 * mm])
    sign_table.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 9), ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'), ('BOTTOMPADDING', (0, 0), (-1, -1), 30), ('BOX', (0, 0), (0, 0), 0.5, colors.grey), ('BOX', (1, 0), (1, 0), 0.5, colors.grey), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 6)]))
    story.append(sign_table)

    def _decor_offre(c, doc_obj):
        """
Définition d'une fonction qui crée un carré en arrière-plan avec une couleur spécifique pour les offres.
"""
        w, h = A4
        c.setStrokeColor(colors.HexColor('#2C3E50'))
        c.setLineWidth(1.5)
        c.rect(10 * mm, 10 * mm, w - 20 * mm, h - 20 * mm)
    doc.build(story, onFirstPage=_decor_offre, onLaterPages=_decor_offre)
    return chemin