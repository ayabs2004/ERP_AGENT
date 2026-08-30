"""extraction.py – Module for extracting client and article information.

Provides utilities to parse text, extract entities using GLiNER, clean and validate client names,
detect client codes or names, load and correct article references, and search for client codes
by name via asynchronous MCP calls. Relies on constants and helpers defined in `api.common`."""
import re
import json
import asyncio
import difflib
import itertools

import api.common
from api.common import (
    _safe_str, _PREFIXES_PARASITES, _SUFFIXES_PARASITES,
    _MOTS_VIDES_NOM, _MOTS_METIER_INVALIDES, _PATTERN_NOM_CLIENT,
    _PREFIXES_PIECES, _get_gliner_sync,
)


def _ner_extraire_entites(texte: str) -> dict:
    """Extract named entities from the given text using the GLiNER model.

    Returns a dictionary mapping entity keys (client, article, piece, etc.) to their
    extracted text values. If the model is unavailable or an error occurs, returns an
    empty dictionary.
    """
    model = _get_gliner_sync()
    if model is None:
        return {}
    labels = [
        "code client", "référence article", "numéro de pièce",
        "type de document", "date de début", "date de fin", "quantité",
    ]
    try:
        entities = model.predict_entities(texte, labels, threshold=0.4)
        label_map = {
            "code client":       "client",
            "référence article": "article",
            "numéro de pièce":   "piece",
            "type de document":  "type_doc",
            "date de début":     "date_debut",
            "date de fin":       "date_fin",
            "quantité":          "quantite",
        }
        result = {}
        for ent in entities:
            key = label_map.get(ent["label"])
            if key and key not in result:
                result[key] = ent["text"].strip()
        return result
    except Exception as e:
        print(f"   ⚠️  [GLiNER] {_safe_str(e)}")
        return {}


def _nettoyer_nom_client(nom: str) -> str:
    """Clean a raw client name by removing known prefix and suffix parasites and normalising spaces."""
    nom = _PREFIXES_PARASITES.sub("", nom).strip()
    nom = _SUFFIXES_PARASITES.sub("", nom).strip()
    return re.sub(r"\s{2,}", " ", nom).strip()


def _est_nom_valide(nom: str) -> bool:
    """Validate a cleaned client name according to length, character content, and stop‑word rules."""
    if not nom or len(nom) < 2:
        return False
    if re.search(r'\d', nom):
        return False
    mots = nom.strip().split()
    mots_lower = [m.lower() for m in mots]
    if len(mots) > 4:
        return False
    nb_stop = sum(1 for m in mots_lower if m in _MOTS_VIDES_NOM)
    if nb_stop >= 2:
        return False
    if all(m in _MOTS_VIDES_NOM for m in mots_lower):
        return False
    if len(mots) >= 2:
        nb_vides = sum(1 for m in mots_lower if m in _MOTS_VIDES_NOM)
        if nb_vides >= len(mots) - 1:
            mots_pleins = [m for m in mots_lower if m not in _MOTS_VIDES_NOM]
            if not mots_pleins or all(len(m) <= 3 for m in mots_pleins):
                return False
        mots_pleins = [m for m in mots_lower if m not in _MOTS_VIDES_NOM]
        if mots_pleins and all(m in _MOTS_METIER_INVALIDES for m in mots_pleins):
            return False
        return True
    mot_lower = mots[0].lower()
    return mot_lower not in _MOTS_METIER_INVALIDES and len(mots[0]) > 2


def _extraire_code_ou_nom_depuis_texte(db: str) -> tuple[str, str]:
    """Extract either a client code or a client name from free‑form text.

    Returns a tuple `(code, nom)` where only one element is non‑empty depending on what
    was detected. Returns empty strings when no valid code or name is found.
    """
    _EXPRESSIONS_FR_EXCLUES = {
        "A-T-IL", "A-T-ELLE", "A-T-ON", "EST-CE", "EST-IL", "EST-ELLE",
        "SONT-ILS", "SONT-ELLES", "Y-A-T-IL", "N-EST-CE-PAS", "QU-EST-CE",
        "PEUT-IL", "PEUT-ELLE", "DOIT-IL", "DOIT-ELLE", "FAUT-IL",
        "VA-T-IL", "VA-T-ELLE", "AVAIT-IL", "POURRAIT-IL", "POURRAIT-ELLE",
        "DONNE-MOI", "DIS-MOI", "MONTRE-MOI", "LAISSE-MOI", "PRETE-MOI",
        "PARLE-MOI", "EXPLIQUE-MOI", "ENVOIE-MOI", "PRECISE-MOI",
        "INDIQUE-MOI", "RAPPELLE-MOI", "CONFIRME-MOI",
    }
    for excl in _EXPRESSIONS_FR_EXCLUES:
        if excl in db.upper():
            db = re.sub(r"\b" + re.escape(excl) + r"\b", "", db, flags=re.IGNORECASE)
    m_code = re.search(r"\b([A-Z]{2,6}\d{2,})\b", db, re.IGNORECASE)
    if m_code:
        code = m_code.group(1).upper()
        if _PREFIXES_PIECES.match(code):
            print(f"   🔎 [Regex] '{code}' détecté comme numéro de pièce (pas client)")
            return "", ""
        print(f"   🔎 [Regex] code_client direct : '{code}'")
        return code, ""
    m_nom = _PATTERN_NOM_CLIENT.search(db)
    if m_nom:
        nom_brut = m_nom.group(1).strip()
        nom = _nettoyer_nom_client(nom_brut)
        print(f"   🔎 [Regex] Capture brute : '{nom_brut}' → nettoyé : '{nom}'")
        if _est_nom_valide(nom):
            print(f"   ✅ [Regex] nom_client validé : '{nom}'")
            return "", nom
        else:
            print(f"   ⚠️  [Regex] nom_client '{nom}' rejeté")
    return "", ""


def _parse_mcp_response(raw: str | dict) -> dict:
    """Parse a raw MCP response that may be a JSON string or a dictionary.

    Returns a dictionary representation of the response, handling empty or malformed
    inputs by returning an error structure.
    """
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
    """Load article references once via MCP and cache them in `api.common`."""
    global _articles_refs_lock
    if api.common._articles_refs_cache is not None:
        return api.common._articles_refs_cache
    if _articles_refs_lock is None:
        _articles_refs_lock = asyncio.Lock()
    async with _articles_refs_lock:
        if api.common._articles_refs_cache is not None:
            return api.common._articles_refs_cache
        try:
            raw = await mcp_pool.call("nl2sql", "lister_references_articles", {})
            data = _parse_mcp_response(raw)
            api.common._articles_refs_cache = data.get("references", []) if data.get("statut") == "OK" else []
        except Exception as e:
            print(f"   ⚠️  [_charger_refs_articles] MCP indisponible ({e})")
            api.common._articles_refs_cache = []
    return api.common._articles_refs_cache


async def _corriger_ref_article(ref: str, mcp_pool) -> str:
    """Correct an article reference using cached references and fuzzy matching.

    Returns the corrected reference in uppercase, or an empty string if the input is
    invalid or cannot be matched.
    """
    _EXPRESSIONS_FR_EXCLUES = {
        "A-T-IL", "A-T-ELLE", "A-T-ON", "EST-CE", "EST-IL", "EST-ELLE",
        "SONT-ILS", "SONT-ELLES", "Y-A-T-IL", "N-EST-CE-PAS", "QU-EST-CE",
        "PEUT-IL", "PEUT-ELLE", "DOIT-IL", "DOIT-ELLE", "FAUT-IL",
        "VA-T-IL", "VA-T-ELLE", "AVAIT-IL", "POURRAIT-IL", "POURRAIT-ELLE",
        "DONNE-MOI", "DIS-MOI", "MONTRE-MOI", "LAISSE-MOI", "PRETE-MOI",
        "PARLE-MOI", "EXPLIQUE-MOI", "ENVOIE-MOI", "PRECISE-MOI",
        "INDIQUE-MOI", "RAPPELLE-MOI", "CONFIRME-MOI",
    }
    if not ref or ref.upper() in _EXPRESSIONS_FR_EXCLUES or ref.upper() in {"MONTRE-MOI", "DONNE-MOI", "DIS-MOI", "AFFICHE-MOI"}:
        return ""
    refs = await _charger_refs_articles(mcp_pool)
    if ref.upper() in refs:
        return ref.upper()
    matches = difflib.get_close_matches(ref.upper(), refs, n=1, cutoff=0.82)
    if matches:
        print(f"   🔧 [Fuzzy] '{ref}' → '{matches[0]}' (correction automatique)")
        return matches[0]
    return ref


_client_nom_cache: dict[str, str] = {}


async def _rechercher_client_par_nom(nom: str, mcp_pool) -> str:
    """Search for a client code given a client name using MCP calls with caching.

    Returns the found client code or an empty string if no match is found.
    """
    if not nom or len(nom.strip()) < 2:
        return ""
    nom_lower = nom.lower().strip()
    if nom_lower in _client_nom_cache:
        cached = _client_nom_cache[nom_lower]
        if cached:
            print(f"   ⚡ [Cache Nom] '{nom}' → {cached}")
        return cached
    try:
        raw = await mcp_pool.call("actions", "resoudre_tiers", {"code_ou_nom": nom.strip()})
        data = json.loads(raw) if isinstance(raw, str) else raw
        if data.get("statut") == "SUCCES" and data.get("CT_Num"):
            ct_num = data["CT_Num"]
            _client_nom_cache[nom_lower] = ct_num
            print(f"   ✅ [Code Exact] '{nom}' → {ct_num}")
            return ct_num
    except Exception:
        pass
    _MOTS_PARASITES = {"le", "la", "les", "du", "de", "des", "et", "ou", "un", "une", "pour", "avec", "sur"}
    mots_significatifs = [
        m for m in nom_lower.split()
        if m not in _MOTS_PARASITES
        and len(m) > 3
        and not m.isdigit()
        and not re.search(r'\d', m)
    ]
    if not mots_significatifs:
        _client_nom_cache[nom_lower] = ""
        print(f"   ⚠️  [Recherche Nom] '{nom}' → aucun mot significatif, abandon")
        return ""
    print(f"   🔍 [Recherche Nom] '{nom}' → mots : {mots_significatifs}")
    async def _appel_fiche(query: str) -> str:
        try:
            return await mcp_pool.call("nl