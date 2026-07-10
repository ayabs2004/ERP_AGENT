"""
extraction/ — Module d'extraction d'entités et résolution de références.
Contient :
  - _ner_extraire_entites() : extraction NER via GLiNER
  - _extraire_code_ou_nom_depuis_texte() : extraction code/nom client
  - _nettoyer_nom_client() : nettoyage nom client
  - _est_nom_valide() : validation nom client
  - _charger_refs_articles() : cache références articles
  - _corriger_ref_article() : correction fuzzy référence article
"""

import re
import json
import logging
from typing import Optional

from common import (
    _safe_str, _db_path, _PREFIXES_PARASITES, _SUFFIXES_PARASITES,
    _MOTS_VIDES_NOM, _MOTS_METIER_INVALIDES, _PATTERN_NOM_CLIENT,
    _PREFIXES_PIECES, _articles_refs_cache, ENABLE_GLINER, _get_gliner_sync,
)

logger = logging.getLogger("sage.erp.extraction")

# ─────────────────────────────────────────────────────────────────────
# NER — Extraction d'entités via GLiNER
# ─────────────────────────────────────────────────────────────────────
async def _ner_extraire_entites(texte: str) -> dict[str, str]:
    """Extrait client, article, piece, type_doc, quantite, dates."""
    if not texte:
        return {}
    modele = _get_gliner_sync()
    if modele is None:
        return {}
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        predictions = await loop.run_in_executor(
            None,
            lambda: modele.predict_entities(
                texte,
                labels=["client", "article", "piece", "type_doc", "quantite", "date_debut", "date_fin"],
                threshold=0.35,
            )
        )
        return {p["label"]: p["text"] for p in predictions if p.get("label") in ("client", "article", "piece", "type_doc", "quantite", "date_debut", "date_fin")}
    except Exception as e:
        logger.warning("NER failed: %s", _safe_str(e))
        return {}


# ─────────────────────────────────────────────────────────────────────
# EXTRACTION CODE OU NOM CLIENT
# ─────────────────────────────────────────────────────────────────────
def _extraire_code_ou_nom_depuis_texte(texte: str) -> tuple[str, str]:
    """Retourne (code_client, nom_client) depuis un texte libre."""
    if not texte:
        return "", ""
    # Code CLIxxx ou FOURxxx
    m_code = re.search(r"\b((?:CLI|FOUR)\d{3,})\b", texte, re.IGNORECASE)
    if m_code:
        return m_code.group(1).upper(), ""
    # Nom via pattern
    m_nom = _PATTERN_NOM_CLIENT.search(texte)
    if m_nom:
        nom = m_nom.group(1).strip()
        nom = _nettoyer_nom_client(nom)
        if _est_nom_valide(nom):
            return "", nom
    return "", ""


def _nettoyer_nom_client(nom: str) -> str:
    """Nettoie un nom de client (parasites, suffixes)."""
    if not nom:
        return ""
    nom = _PREFIXES_PARASITES.sub("", nom)
    nom = _SUFFIXES_PARASITES.sub("", nom)
    nom = re.sub(r"\s+", " ", nom).strip()
    return nom


def _est_nom_valide(nom: str) -> bool:
    """Valide un nom de client (pas un mot vide, pas de chiffre)."""
    if not nom or len(nom.strip()) < 2:
        return False
    mots = nom.lower().split()
    if any(m in _MOTS_VIDES_NOM for m in mots):
        return False
    if any(m in _MOTS_METIER_INVALIDES for m in mots):
        return False
    if re.search(r"\d", nom):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# ARTICLES — Cache et correction
# ─────────────────────────────────────────────────────────────────────
def _charger_refs_articles() -> list[str]:
    """Charge les références articles depuis la DB (une seule fois)."""
    global _articles_refs_cache
    if _articles_refs_cache is not None:
        return _articles_refs_cache
    try:
        import sqlite3
        conn = sqlite3.connect(str(_db_path))
        rows = conn.execute("SELECT AR_Ref FROM F_ARTICLE").fetchall()
        conn.close()
        _articles_refs_cache = [r[0] for r in rows if r[0]]
        return _articles_refs_cache or []
    except Exception as e:
        logger.warning("Failed to load article refs: %s", _safe_str(e))
        return []


def _corriger_ref_article(ref: str) -> str:
    """Correction fuzzy d'une référence article."""
    if not ref:
        return ""
    ref_upper = ref.upper()
    refs = _charger_refs_articles()
    if not refs:
        return ref_upper
    # Match exact
    if ref_upper in refs:
        return ref_upper
    # Match partiel
    candidats = [r for r in refs if ref_upper in r or r in ref_upper]
    if candidats:
        return sorted(candidats, key=len)[0]
    # Fuzzy match
    import difflib
    match = difflib.get_close_matches(ref_upper, refs, n=1, cutoff=0.8)
    return match[0] if match else ref_upper