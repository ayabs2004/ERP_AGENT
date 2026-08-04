"""
extraction/ — Module d'extraction d'entités et résolution de références.
Contient :
  - _ner_extraire_entites() : extraction NER via GLiNER
  - _extraire_code_ou_nom_depuis_texte() : extraction code/nom client
  - _nettoyer_nom_client() : nettoyage nom client
  - _est_nom_valide() : validation nom client
  - _charger_refs_articles() : cache références articles (via MCP)
  - _corriger_ref_article() : correction fuzzy référence article (via MCP)
"""

import re
import json
import asyncio
import difflib
import logging
from typing import Optional

import api.common as common
from api.common import (
    _safe_str, _PREFIXES_PARASITES, _SUFFIXES_PARASITES,
    _MOTS_VIDES_NOM, _MOTS_METIER_INVALIDES, _PATTERN_NOM_CLIENT,
    _PREFIXES_PIECES, ENABLE_GLINER, _get_gliner_sync,
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
# ARTICLES — Cache et correction (via MCP, plus de SQLite direct)
# ─────────────────────────────────────────────────────────────────────
def _parse_mcp_response(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {"statut": "ERREUR", "message": "Réponse vide"}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"statut": "ERREUR", "message": str(raw)}


_articles_refs_lock: asyncio.Lock | None = None


async def _charger_refs_articles(mcp_pool) -> list[str]:
    """Charge (une seule fois) les références articles via le serveur MCP 'nl2sql'.
    Mute directement common._articles_refs_cache (via `import api.common as common`)
    pour que le cache soit réellement partagé entre modules — un `from api.common
    import _articles_refs_cache` ne créerait qu'une copie locale jamais synchronisée."""
    global _articles_refs_lock
    if common._articles_refs_cache is not None:
        return common._articles_refs_cache
    if _articles_refs_lock is None:
        _articles_refs_lock = asyncio.Lock()
    async with _articles_refs_lock:
        if common._articles_refs_cache is not None:
            return common._articles_refs_cache
        try:
            raw = await mcp_pool.call("nl2sql", "lister_references_articles", {})
            data = _parse_mcp_response(raw)
            common._articles_refs_cache = data.get("references", []) if data.get("statut") == "OK" else []
        except Exception as e:
            logger.warning("Failed to load article refs via MCP: %s", _safe_str(e))
            common._articles_refs_cache = []
    return common._articles_refs_cache


async def _corriger_ref_article(ref: str, mcp_pool) -> str:
    """Correction fuzzy d'une référence article — nécessite désormais mcp_pool
    puisque la liste des refs vient du serveur MCP, plus de SQLite local."""
    if not ref:
        return ""
    ref_upper = ref.upper()
    refs = await _charger_refs_articles(mcp_pool)
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
    match = difflib.get_close_matches(ref_upper, refs, n=1, cutoff=0.8)
    return match[0] if match else ref_upper