#!/usr/bin/env python3
"""
generer_pdf_test_rag.py — Génère des PDF de test pour la base de connaissance RAG
====================================================================================
Objectif : produire, à partir des VRAIES données de la base (clients,
fournisseurs, articles — lues via adaptation/db_adapter, donc compatible
sqlite ET mssql sans rien changer), des fichiers PDF organisés dans
kb_docs_pdf/<doc_type>/... comme attendu par indexer_kb.py, en y injectant
des informations FICTIVES (garanties, tolérances, réclamations, conditions
commerciales négociées, relances impayés...) clairement signalées comme
telles, pour tester la logique de recherche/retrieval du RAG.

Arborescence produite (compatible avec KB_DOCS_DIR de indexer_kb.py) :
    kb_docs_pdf/
        fiche_article/AR0001.pdf          (réel + specs fictives)
        reclamation_sav/AR0001_reclam.pdf (réclamation 100% fictive)
        note_crm/CLI0001.pdf              (réel + conditions négociées fictives)
        relance_commerciale/CLI0001_relance.pdf   (fictif)
        recouvrement/CLI0001_recouvrement.pdf     (fictif)
        procedure/procedure_XX.pdf        (procédures génériques fictives)

Un fichier test_manifest.json est produit à côté, listant pour chaque fait
fictif injecté : le PDF concerné, le code de l'entité, le fait exact, et une
question de test suggérée — pour vérifier ensuite que rechercher_procedure()
/ generer_recommandation_commerciale() retrouvent bien la bonne source.

Usage :
    python generer_pdf_test_rag.py                     # génère avec valeurs par défaut
    python generer_pdf_test_rag.py --reset              # supprime kb_docs_pdf/ avant
    python generer_pdf_test_rag.py --nb-clients 30 --nb-articles 50 --seed 42
    python generer_pdf_test_rag.py --nb-clients 0       # 0 = tous les clients de la base

Après génération, ré-indexez :
    python indexer_kb.py --reset
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from adaptation.db_adapter import get_connection, table, col  # noqa: E402

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

# ─────────────────────────────────────────────────────────────────────
# STYLES
# ─────────────────────────────────────────────────────────────────────
_STYLES = getSampleStyleSheet()
ST_TITLE = ParagraphStyle("TitrePerso", parent=_STYLES["Title"], fontSize=16, spaceAfter=8)
ST_H2 = ParagraphStyle("H2Perso", parent=_STYLES["Heading2"], fontSize=12,
                        spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#1f4e79"))
ST_BODY = ParagraphStyle("BodyPerso", parent=_STYLES["Normal"], fontSize=10, leading=14)
ST_FICTIF = ParagraphStyle("Fictif", parent=_STYLES["Normal"], fontSize=9, leading=13,
                            textColor=colors.HexColor("#8a1f11"))
ST_BANDEAU = ParagraphStyle("Bandeau", parent=_STYLES["Normal"], fontSize=8,
                             textColor=colors.white, alignment=1)

# ─────────────────────────────────────────────────────────────────────
# BANQUES DE DONNÉES FICTIVES ("irréelles" mais plausibles)
# ─────────────────────────────────────────────────────────────────────
_MATIERES = [
    "acier inoxydable 316L", "aluminium anodisé 6061", "PVC rigide alimentaire",
    "laiton chromé", "polypropylène renforcé fibre de verre", "fonte GS",
    "titane grade 5", "PEHD alimentaire", "bronze fritté auto-lubrifiant",
]
_TOLERANCES = [
    "± 0,02 mm (classe IT6)", "± 0,05 mm", "± 0,1 mm (usinage standard)",
    "± 0,15 mm (moulage)", "± 0,3 mm (découpe laser)", "± 0,5° angulaire",
]
_GARANTIES = [
    "12 mois pièces et main d'œuvre", "24 mois pièces uniquement",
    "36 mois constructeur (hors usure normale)", "6 mois (article reconditionné)",
    "5 ans sur la structure, 1 an sur les composants électroniques",
]
_TEMPERATURES = [
    "stockage entre 5°C et 25°C, à l'abri de l'humidité",
    "stockage entre -10°C et 40°C, ventilation obligatoire",
    "température ambiante, éviter toute exposition directe au soleil",
    "chaîne du froid requise en continu : 2°C à 8°C",
]
_PROCESS = [
    "usinage CNC 5 axes puis traitement de surface par anodisation dure",
    "moulage par injection suivi d'un contrôle dimensionnel à 100%",
    "découpe laser, pliage puis soudure TIG sur banc automatisé",
    "extrusion continue avec refroidissement par bain d'eau régulé",
]
_PRECAUTIONS = [
    "porter des gants de protection lors de la manipulation (arêtes vives)",
    "produit sensible à l'électricité statique : manipuler en zone ESD",
    "ne pas empiler plus de 3 palettes, risque d'écrasement",
    "tenir hors de portée des enfants, présence de petites pièces détachables",
]
_MOTIFS_RECLAMATION = [
    "livraison partielle : {qte1} unités reçues sur {qte2} commandées",
    "défaut d'aspect constaté sur environ {pct}% du lot (rayures de surface)",
    "non-conformité dimensionnelle relevée par le contrôle qualité à réception",
    "emballage endommagé pendant le transport, {qte1} unités inutilisables",
    "délai de livraison dépassé de {jours} jours ouvrés sans notification préalable",
]
_CONDITIONS_NEGOCIEES = [
    "remise fidélité de {pct}% appliquée au-delà de {seuil} DT de CA annuel",
    "délai de paiement négocié à 60 jours fin de mois (au lieu de 30 jours standard)",
    "franco de port à partir de {seuil} DT d'achat",
    "remise quantitative de {pct}% par palier de 100 unités",
    "prix bloqué pendant 12 mois sur les références principales de ce client",
]
_HISTORIQUE_RELANCE = [
    "3 relances envoyées en {mois}, aucune réponse du client à ce jour",
    "client recontacté par téléphone le {mois}, promesse de règlement non tenue",
    "baisse de commande de {pct}% sur le dernier trimestre par rapport à l'historique",
    "aucune commande enregistrée depuis {mois}, client jugé à risque d'inactivité",
]
_MOTIFS_RECOUVREMENT = [
    "facture en souffrance depuis {jours} jours, mise en demeure envoyée le {date}",
    "échéancier de paiement proposé sur 3 mois, 1re échéance honorée le {date}",
    "dossier transmis au service contentieux le {date} après relances infructueuses",
    "règlement partiel de {pct}% du solde reçu le {date}, reliquat toujours dû",
]
_MOIS = [f"{m} 2025" for m in (
    "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre",
)]
_PROCEDURES_GENERIQUES = [
    ("Procédure retour marchandise",
     "Tout retour doit être signalé sous 14 jours calendaires à compter de la "
     "réception, accompagné du numéro de bon de livraison. Les articles "
     "personnalisés ou coupés sur mesure ne sont pas repris."),
    ("Procédure garantie SAV",
     "La garantie couvre les défauts de fabrication mais exclut l'usure normale, "
     "les mauvaises conditions de stockage et les modifications apportées par le "
     "client. Toute demande de prise en charge doit être accompagnée de photos."),
    ("Procédure de fabrication qualité",
     "Chaque lot de production fait l'objet d'un contrôle qualité par "
     "échantillonnage (norme interne QC-014) avant libération vers le stock."),
]


def _fmt(gabarit: str, rng: random.Random) -> str:
    return gabarit.format(
        qte1=rng.randint(2, 50), qte2=rng.randint(51, 200),
        pct=rng.choice([3, 5, 8, 10, 12, 15, 20, 35]),
        seuil=rng.choice([500, 1000, 2500, 5000, 10000]),
        jours=rng.randint(5, 45),
        mois=rng.choice(_MOIS),
        date=f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2025",
    )


# ─────────────────────────────────────────────────────────────────────
# CONSTRUCTION PDF
# ─────────────────────────────────────────────────────────────────────
def _bandeau(texte: str, couleur: str) -> Table:
    t = Table([[Paragraph(texte, ST_BANDEAU)]], colWidths=[17 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(couleur)),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _table_kv(paires: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", ST_BODY), Paragraph(str(v) if v not in (None, "") else "—", ST_BODY)]
            for k, v in paires]
    t = Table(data, colWidths=[5 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _ecrire_pdf(chemin: Path, titre: str, elements_reels: list, elements_fictifs: list):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(chemin), pagesize=A4,
                             topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                             leftMargin=2 * cm, rightMargin=2 * cm)
    story = [Paragraph(titre, ST_TITLE)]
    story += elements_reels
    if elements_fictifs:
        story.append(Spacer(1, 10))
        story.append(_bandeau(
            "⚠ SECTION SUIVANTE : DONNÉES DE TEST GÉNÉRÉES AUTOMATIQUEMENT — "
            "AUCUNE VALEUR RÉELLE", "#8a1f11"))
        story.append(Spacer(1, 6))
        story += elements_fictifs
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#cccccc")))
    story.append(Paragraph(
        f"Document généré le {datetime.now():%d/%m/%Y %H:%M} — "
        f"fichier de test pour la base RAG (generer_pdf_test_rag.py)",
        ParagraphStyle("Footer", parent=_STYLES["Normal"], fontSize=7,
                        textColor=colors.HexColor("#888888"))))
    doc.build(story)


# ─────────────────────────────────────────────────────────────────────
# ACCÈS DONNÉES (via db_adapter — indépendant sqlite/mssql)
# ─────────────────────────────────────────────────────────────────────
def _fetch_tiers(conn, type_tiers: int) -> list[dict]:
    t = table("clients_fournisseurs")
    c = lambda k: col("clients_fournisseurs", k)
    sql = (
        f"SELECT {c('code')} AS code, {c('nom')} AS nom, {c('type_tiers')} AS type_tiers, "
        f"{c('sommeil')} AS sommeil, {c('encours')} AS encours, {c('adresse')} AS adresse, "
        f"{c('code_postal')} AS code_postal, {c('ville')} AS ville, {c('pays')} AS pays, "
        f"{c('contact')} AS contact, {c('telephone')} AS telephone, {c('email')} AS email "
        f"FROM {t} WHERE {c('type_tiers')} = ?"
    )
    cur = conn.execute(sql, (type_tiers,))
    return [dict(r) for r in cur.fetchall()]


def _fetch_articles(conn) -> list[dict]:
    t = table("articles")
    c = lambda k: col("articles", k)
    sql = (
        f"SELECT {c('ref')} AS ref, {c('designation')} AS designation, "
        f"{c('prix_achat')} AS prix_achat, {c('prix_vente')} AS prix_vente "
        f"FROM {t}"
    )
    cur = conn.execute(sql)
    return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────
# GÉNÉRATION — ARTICLES
# ─────────────────────────────────────────────────────────────────────
def generer_fiche_article(article: dict, out_dir: Path, rng: random.Random, manifest: list) -> Path:
    ref = article["ref"]
    reel = [
        Paragraph("Données réelles (base)", ST_H2),
        _table_kv([
            ("Référence", ref),
            ("Désignation", article.get("designation")),
            ("Prix d'achat", article.get("prix_achat")),
            ("Prix de vente", article.get("prix_vente")),
        ]),
    ]
    matiere = rng.choice(_MATIERES)
    tolerance = rng.choice(_TOLERANCES)
    garantie = rng.choice(_GARANTIES)
    temperature = rng.choice(_TEMPERATURES)
    process = rng.choice(_PROCESS)
    precaution = rng.choice(_PRECAUTIONS)
    fictif = [
        Paragraph("Fiche technique (fictive — pour test RAG)", ST_H2),
        _table_kv([
            ("Matière", matiere),
            ("Tolérance dimensionnelle", tolerance),
            ("Garantie", garantie),
            ("Conditions de stockage", temperature),
            ("Procédé de fabrication", process),
            ("Précaution d'usage", precaution),
        ]),
    ]
    chemin = out_dir / "fiche_article" / f"{ref}.pdf"
    _ecrire_pdf(chemin, f"Fiche article — {ref}", reel, fictif)

    for champ, valeur, question in [
        ("matière", matiere, f"Quelle est la matière de l'article {ref} ?"),
        ("tolérance", tolerance, f"Quelle est la tolérance dimensionnelle de l'article {ref} ?"),
        ("garantie", garantie, f"Quelle est la garantie de l'article {ref} ?"),
        ("température de stockage", temperature, f"À quelle température stocker l'article {ref} ?"),
        ("procédé de fabrication", process, f"Comment est fabriqué l'article {ref} ?"),
        ("précaution d'usage", precaution, f"Quelles précautions prendre avec l'article {ref} ?"),
    ]:
        manifest.append({
            "fichier": str(chemin.relative_to(out_dir)),
            "doc_type": "fiche_article",
            "code": ref,
            "champ_fictif": champ,
            "valeur_fictive": valeur,
            "question_test_suggeree": question,
        })
    return chemin


def generer_reclamation(article: dict, out_dir: Path, rng: random.Random, manifest: list) -> Path:
    ref = article["ref"]
    motif = _fmt(rng.choice(_MOTIFS_RECLAMATION), rng)
    fictif = [
        Paragraph("Réclamation SAV (100% fictive — pour test RAG)", ST_H2),
        _table_kv([
            ("Article concerné", ref),
            ("Date de la réclamation", f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2025"),
            ("Motif", motif),
            ("Statut", rng.choice(["ouverte", "en cours de traitement", "clôturée — avoir émis"])),
        ]),
    ]
    chemin = out_dir / "reclamation_sav" / f"{ref}_reclamation_{rng.randint(1,999)}.pdf"
    _ecrire_pdf(chemin, f"Réclamation SAV — {ref}", [], fictif)
    manifest.append({
        "fichier": str(chemin.relative_to(out_dir)),
        "doc_type": "reclamation_sav",
        "code": ref,
        "champ_fictif": "motif de réclamation",
        "valeur_fictive": motif,
        "question_test_suggeree": f"Quelles réclamations a-t-on eu sur l'article {ref} ?",
    })
    return chemin


# ─────────────────────────────────────────────────────────────────────
# GÉNÉRATION — CLIENTS / FOURNISSEURS
# ─────────────────────────────────────────────────────────────────────
def generer_note_crm(tiers: dict, out_dir: Path, rng: random.Random, manifest: list, est_fournisseur: bool) -> Path:
    code = tiers["code"]
    reel = [
        Paragraph("Données réelles (base)", ST_H2),
        _table_kv([
            ("Code", code),
            ("Intitulé", tiers.get("nom")),
            ("Statut", "BLOQUÉ" if str(tiers.get("sommeil")) in ("1", "True", "true") else "ACTIF"),
            ("Encours", tiers.get("encours")),
            ("Adresse", tiers.get("adresse")),
            ("Ville", f"{tiers.get('code_postal') or ''} {tiers.get('ville') or ''}".strip()),
            ("Pays", tiers.get("pays")),
            ("Contact", tiers.get("contact")),
            ("Téléphone", tiers.get("telephone")),
            ("Email", tiers.get("email")),
        ]),
    ]
    condition = _fmt(rng.choice(_CONDITIONS_NEGOCIEES), rng)
    fictif = [
        Paragraph("Conditions commerciales négociées (fictives — pour test RAG)", ST_H2),
        Paragraph(condition, ST_FICTIF),
    ]
    label = "Fournisseur" if est_fournisseur else "Client"
    chemin = out_dir / "note_crm" / f"{code}.pdf"
    _ecrire_pdf(chemin, f"Fiche {label.lower()} — {code}", reel, fictif)
    manifest.append({
        "fichier": str(chemin.relative_to(out_dir)),
        "doc_type": "note_crm",
        "code": code,
        "champ_fictif": "conditions commerciales négociées",
        "valeur_fictive": condition,
        "question_test_suggeree": f"Quelles sont les conditions commerciales négociées avec le {label.lower()} {code} ?",
    })
    return chemin


def generer_relance(tiers: dict, out_dir: Path, rng: random.Random, manifest: list) -> Path:
    code = tiers["code"]
    historique = _fmt(rng.choice(_HISTORIQUE_RELANCE), rng)
    fictif = [
        Paragraph("Historique de relance commerciale (fictif — pour test RAG)", ST_H2),
        Paragraph(historique, ST_FICTIF),
    ]
    chemin = out_dir / "relance_commerciale" / f"{code}_relance.pdf"
    _ecrire_pdf(chemin, f"Relance commerciale — {code}", [], fictif)
    manifest.append({
        "fichier": str(chemin.relative_to(out_dir)),
        "doc_type": "relance_commerciale",
        "code": code,
        "champ_fictif": "historique de relance",
        "valeur_fictive": historique,
        "question_test_suggeree": f"Quel est l'historique de relance du client {code} ?",
    })
    return chemin


def generer_recouvrement(tiers: dict, out_dir: Path, rng: random.Random, manifest: list) -> Path:
    code = tiers["code"]
    motif = _fmt(rng.choice(_MOTIFS_RECOUVREMENT), rng)
    fictif = [
        Paragraph("Dossier recouvrement (fictif — pour test RAG)", ST_H2),
        Paragraph(motif, ST_FICTIF),
    ]
    chemin = out_dir / "recouvrement" / f"{code}_recouvrement.pdf"
    _ecrire_pdf(chemin, f"Dossier recouvrement — {code}", [], fictif)
    manifest.append({
        "fichier": str(chemin.relative_to(out_dir)),
        "doc_type": "recouvrement",
        "code": code,
        "champ_fictif": "statut recouvrement",
        "valeur_fictive": motif,
        "question_test_suggeree": f"Où en est le recouvrement du client {code} ?",
    })
    return chemin


def generer_procedures_generiques(out_dir: Path, manifest: list):
    for i, (titre, texte) in enumerate(_PROCEDURES_GENERIQUES, start=1):
        chemin = out_dir / "procedure" / f"procedure_{i:02d}.pdf"
        _ecrire_pdf(chemin, titre, [Paragraph(texte, ST_BODY)], [])
        manifest.append({
            "fichier": str(chemin.relative_to(out_dir)),
            "doc_type": "procedure",
            "code": "",
            "champ_fictif": titre,
            "valeur_fictive": texte,
            "question_test_suggeree": f"{titre} : que dit la procédure ?",
        })


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dossier", default=str(_ROOT / "kb_docs_pdf"), help="Dossier de sortie des PDF")
    p.add_argument("--nb-clients", type=int, default=20, help="0 = tous les clients de la base")
    p.add_argument("--nb-fournisseurs", type=int, default=10, help="0 = tous les fournisseurs")
    p.add_argument("--nb-articles", type=int, default=40, help="0 = tous les articles")
    p.add_argument("--pct-reclamation", type=float, default=0.3, help="Fraction d'articles avec réclamation fictive")
    p.add_argument("--pct-relance", type=float, default=0.25, help="Fraction de clients avec relance fictive")
    p.add_argument("--pct-recouvrement", type=float, default=0.15, help="Fraction de clients avec dossier recouvrement fictif")
    p.add_argument("--seed", type=int, default=None, help="Graine aléatoire (reproductibilité)")
    p.add_argument("--reset", action="store_true", help="Supprime le dossier de sortie avant de régénérer")
    args = p.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.dossier)

    if args.reset and out_dir.exists():
        print(f"🗑️  Suppression de {out_dir} ...")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("🔌 Connexion à la base (via adaptation.db_adapter)...")
    conn = get_connection()

    print("📥 Lecture des clients...")
    clients = _fetch_tiers(conn, 0)
    print("📥 Lecture des fournisseurs...")
    fournisseurs = _fetch_tiers(conn, 1)
    print("📥 Lecture des articles...")
    articles = _fetch_articles(conn)
    conn.close()

    if args.nb_clients > 0:
        clients = rng.sample(clients, min(args.nb_clients, len(clients)))
    if args.nb_fournisseurs > 0:
        fournisseurs = rng.sample(fournisseurs, min(args.nb_fournisseurs, len(fournisseurs)))
    if args.nb_articles > 0:
        articles = rng.sample(articles, min(args.nb_articles, len(articles)))

    print(f"   {len(clients)} client(s), {len(fournisseurs)} fournisseur(s), {len(articles)} article(s) retenus.")

    manifest: list[dict] = []

    print("📝 Génération des fiches articles + réclamations...")
    for art in articles:
        generer_fiche_article(art, out_dir, rng, manifest)
        if rng.random() < args.pct_reclamation:
            generer_reclamation(art, out_dir, rng, manifest)

    print("📝 Génération des fiches clients + relances/recouvrements...")
    for cli in clients:
        generer_note_crm(cli, out_dir, rng, manifest, est_fournisseur=False)
        if rng.random() < args.pct_relance:
            generer_relance(cli, out_dir, rng, manifest)
        if rng.random() < args.pct_recouvrement:
            generer_recouvrement(cli, out_dir, rng, manifest)

    print("📝 Génération des fiches fournisseurs...")
    for four in fournisseurs:
        generer_note_crm(four, out_dir, rng, manifest, est_fournisseur=True)

    print("📝 Génération des procédures génériques...")
    generer_procedures_generiques(out_dir, manifest)

    manifest_path = out_dir.parent / "test_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    nb_total = sum(1 for _ in out_dir.rglob("*.pdf"))
    print(f"\n✅ {nb_total} PDF générés dans {out_dir}")
    print(f"✅ Manifeste de test ({len(manifest)} faits fictifs + questions suggérées) : {manifest_path}")
    print("\n➡️  Prochaine étape : python indexer_kb.py --reset")


if __name__ == "__main__":
    main()