#!/usr/bin/env python3
"""
convertir_md_vers_pdf.py — Migration kb_docs/*.md → kb_docs_pdf/<doc_type>/*.pdf
==================================================================================
Convertit tous les fichiers .md générés par generer_docs_fictifs.py (avec
frontmatter --- doc_type / ref_article / code_client / date ---) en PDF,
rangés selon l'arborescence recommandée pour indexer_kb.py (version PDF) :

    kb_docs_pdf/
      fiche_article/AR001.pdf
      commande_email/CLI003_0.pdf
      note_crm/CLI007.pdf
      reclamation_sav/AR012_0.pdf

Le dossier de destination = doc_type du frontmatter (détection automatique
par indexer_kb.py via le nom du dossier parent → pas besoin de deviner par
regex/LLM pour ces fichiers migrés).

Les métadonnées (code_client, ref_article, date) sont réinjectées en clair
en tête du PDF, pour que la détection par regex dans indexer_kb.py fonctionne
aussi si jamais les PDF sont un jour déplacés hors de leur dossier doc_type.

Dépendance :
    pip install reportlab

Usage :
    python convertir_md_vers_pdf.py
    python convertir_md_vers_pdf.py --source kb_docs --dest kb_docs_pdf
    python convertir_md_vers_pdf.py --overwrite
"""

import re
import argparse
import textwrap
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────
SOURCE_DIR_DEFAUT = Path(__file__).parent / "kb_docs"
DEST_DIR_DEFAUT   = Path(__file__).parent / "kb_docs_pdf"

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGE_G   = 20 * mm
MARGE_D   = 20 * mm
MARGE_HAUT = 20 * mm
MARGE_BAS  = 20 * mm
LARGEUR_UTILE = PAGE_WIDTH - MARGE_G - MARGE_D

FONT_TITRE = "Helvetica-Bold"
FONT_TEXTE = "Helvetica"
TAILLE_TITRE = 13
TAILLE_META  = 9
TAILLE_TEXTE = 10.5
INTERLIGNE   = 14

# Nb de caractères par ligne, calibré empiriquement pour Helvetica 10.5pt
# sur la largeur utile (évite de dépendre de stringWidth pour rester simple).
CAR_PAR_LIGNE = 95

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n\n?(.*)$", re.DOTALL)

_DOC_TYPE_FALLBACK = "inconnu"


# ─────────────────────────────────────────────────────────────────────
# PARSING DU MARKDOWN SOURCE (même logique que l'ancien indexer_kb.py)
# ─────────────────────────────────────────────────────────────────────
def parser_document(texte: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(texte)
    if not m:
        return {}, texte
    bloc_meta, corps = m.group(1), m.group(2)
    metadata = {}
    for ligne in bloc_meta.splitlines():
        if ":" in ligne:
            cle, val = ligne.split(":", 1)
            metadata[cle.strip()] = val.strip()
    return metadata, corps.strip()


# ─────────────────────────────────────────────────────────────────────
# RENDU PDF
# ─────────────────────────────────────────────────────────────────────
def _wrap_paragraphe(paragraphe: str, largeur_car: int = CAR_PAR_LIGNE) -> list[str]:
    """Découpe un paragraphe en lignes, en respectant les lignes déjà
    présentes (ex: listes à puces markdown) plutôt que de tout refondre."""
    lignes_sortie = []
    for ligne_brute in paragraphe.split("\n"):
        if not ligne_brute.strip():
            lignes_sortie.append("")
            continue
        lignes_sortie.extend(
            textwrap.wrap(ligne_brute, width=largeur_car) or [""]
        )
    return lignes_sortie


def _nouvelle_page(c: canvas.Canvas, titre: str) -> float:
    c.showPage()
    y = PAGE_HEIGHT - MARGE_HAUT
    c.setFont(FONT_TITRE, TAILLE_TITRE)
    c.drawString(MARGE_G, y, f"{titre} (suite)")
    return y - (INTERLIGNE * 1.8)


def generer_pdf(nom_titre: str, metadata: dict, corps_md: str, chemin_sortie: Path):
    chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(chemin_sortie), pagesize=A4)

    y = PAGE_HEIGHT - MARGE_HAUT

    # ── Titre ──────────────────────────────────────────────────────
    c.setFont(FONT_TITRE, TAILLE_TITRE)
    c.drawString(MARGE_G, y, nom_titre)
    y -= INTERLIGNE * 1.5

    # ── Bloc métadonnées (texte brut, lisible par regex indexer_kb.py) ──
    c.setFont(FONT_TEXTE, TAILLE_META)
    for cle in ("doc_type", "ref_article", "code_client", "date"):
        val = metadata.get(cle, "")
        if val:
            c.drawString(MARGE_G, y, f"{cle} : {val}")
            y -= INTERLIGNE * 0.85
    y -= INTERLIGNE * 0.5

    # Ligne de séparation
    c.setLineWidth(0.5)
    c.line(MARGE_G, y, PAGE_WIDTH - MARGE_D, y)
    y -= INTERLIGNE * 1.2

    # ── Corps du document ────────────────────────────────────────────
    c.setFont(FONT_TEXTE, TAILLE_TEXTE)
    paragraphes = corps_md.split("\n\n")

    for paragraphe in paragraphes:
        lignes = _wrap_paragraphe(paragraphe)
        for ligne in lignes:
            if y < MARGE_BAS:
                y = _nouvelle_page(c, nom_titre)
                c.setFont(FONT_TEXTE, TAILLE_TEXTE)
            c.drawString(MARGE_G, y, ligne)
            y -= INTERLIGNE
        # espace entre paragraphes
        y -= INTERLIGNE * 0.4
        if y < MARGE_BAS:
            y = _nouvelle_page(c, nom_titre)
            c.setFont(FONT_TEXTE, TAILLE_TEXTE)

    c.save()


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def convertir_tout(source_dir: Path, dest_dir: Path, overwrite: bool = False):
    fichiers_md = sorted(source_dir.glob("*.md"))
    if not fichiers_md:
        print(f"❌ Aucun fichier .md trouvé dans {source_dir}")
        return

    print(f"📂 Source : {source_dir}  ({len(fichiers_md)} fichier(s) .md)")
    print(f"📁 Destination : {dest_dir}\n")

    stats: dict[str, int] = {}
    ignores = 0

    for fichier_md in fichiers_md:
        texte_brut = fichier_md.read_text(encoding="utf-8")
        metadata, corps = parser_document(texte_brut)

        doc_type = metadata.get("doc_type", "").strip() or _DOC_TYPE_FALLBACK
        nom_pdf = fichier_md.stem + ".pdf"
        chemin_sortie = dest_dir / doc_type / nom_pdf

        if chemin_sortie.exists() and not overwrite:
            ignores += 1
            continue

        # Titre = première ligne markdown "# ..." si présente, sinon le nom de fichier
        m_titre = re.match(r"^#\s+(.+)$", corps.splitlines()[0]) if corps.splitlines() else None
        titre = m_titre.group(1).strip() if m_titre else fichier_md.stem

        generer_pdf(titre, metadata, corps, chemin_sortie)

        stats[doc_type] = stats.get(doc_type, 0) + 1
        print(f"   ✅ {fichier_md.name}  →  {chemin_sortie.relative_to(dest_dir)}")

    print("\n✅ Conversion terminée :")
    for doc_type, nb in stats.items():
        print(f"   {doc_type:<20} : {nb} PDF généré(s)")
    if ignores:
        print(f"   (⏭️  {ignores} fichier(s) déjà présent(s), ignoré(s) — utilisez --overwrite pour forcer)")
    print(f"\n📁 PDF écrits dans : {dest_dir}/")
    print("➡️  Lance maintenant : python indexer_kb.py --reset")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, default=str(SOURCE_DIR_DEFAUT),
                         help="Dossier contenant les .md source (défaut: kb_docs/)")
    parser.add_argument("--dest", type=str, default=str(DEST_DIR_DEFAUT),
                         help="Dossier de destination des .pdf (défaut: kb_docs_pdf/)")
    parser.add_argument("--overwrite", action="store_true",
                         help="Régénère les PDF même s'ils existent déjà")
    args = parser.parse_args()

    convertir_tout(Path(args.source), Path(args.dest), overwrite=args.overwrite)


if __name__ == "__main__":
    main()