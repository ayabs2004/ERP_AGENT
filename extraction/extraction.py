"""
extraction.py — Extraction client / article.
Utilise exclusivement common.py comme source de vérité pour les constantes.
"""
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
    nom = _PREFIXES_PARASITES.sub("", nom).strip()
    nom = _SUFFIXES_PARASITES.sub("", nom).strip()
    return re.sub(r"\s{2,}", " ", nom).strip()


def _est_nom_valide(nom: str) -> bool:
    if not nom or len(nom) < 2:
        return False
    if re.search(r'\d', nom):
        return False
    mots = nom.strip().split()
    mots_lower = [m.lower() for m in mots]
    
    # Refuser les captures trop longues (> 4 mots) sauf noms composés connus
    if len(mots) > 4:
        return False
    
    # Refuser si plus d'un mot est un stop-word ou mot interrogatif
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


# ──────────────────────────────────────────────────────────────
# Références articles — cache mutable dans common (via `import common`)
# ──────────────────────────────────────────────────────────────
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
    """Charge (une fois) les refs articles via MCP. Mute common._articles_refs_cache
    directement (via `import common`) pour que le cache soit réellement partagé."""
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
    if not ref:
        return ref
    refs = await _charger_refs_articles(mcp_pool)
    if ref.upper() in refs:
        return ref.upper()
    matches = difflib.get_close_matches(ref.upper(), refs, n=1, cutoff=0.72)
    if matches:
        print(f"   🔧 [Fuzzy] '{ref}' → '{matches[0]}' (correction automatique)")
        return matches[0]
    return ref


# ──────────────────────────────────────────────────────────────
# Recherche client par nom (async/MCP)
# ──────────────────────────────────────────────────────────────
_client_nom_cache: dict[str, str] = {}


async def _rechercher_client_par_nom(nom: str, mcp_pool) -> str:
    if not nom or len(nom.strip()) < 2:
        return ""
    nom_lower = nom.lower().strip()
    if nom_lower in _client_nom_cache:
        cached = _client_nom_cache[nom_lower]
        if cached:
            print(f"   ⚡ [Cache Nom] '{nom}' → {cached}")
        return cached

    # 1) essai code exact d'abord (e.g. GRENA, CARAT)
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
            return await mcp_pool.call("nl2sql", "rechercher_fiche_client", {"code_client": query})
        except Exception:
            return ""

    async def _appel_statut(query: str) -> str:
        try:
            return await mcp_pool.call("nl2sql", "verifier_statut_client", {"code_client": query})
        except Exception:
            return ""

    _CODES_INVALIDES_CACHE = {
        "client", "clients", "tiers", "societe", "société",
        "entreprise", "none", "null", "inconnu", "unknown",
        "non_trouve", "vide", "absent",
    }

    def _extraire_code(text: str) -> str:
        try:
            data = json.loads(text)
            code = data.get("CT_Num", "")
            if (code
                    and code not in ("NON_TROUVE", "")
                    and code.lower() not in _CODES_INVALIDES_CACHE
                    and len(code) >= 3):
                return code
        except Exception:
            pass
        m = re.search(r"CT_Num[:\s]+([A-Z0-9]+)", text, re.IGNORECASE)
        code_m = m.group(1).strip() if m else ""
        return code_m if code_m.lower() not in _CODES_INVALIDES_CACHE else ""

    MAX_RECHERCHE_NOM_CALLS = 6  # plafond configurable d'appels MCP

    # Si trop de mots significatifs, la phrase ressemble à une requête NL2SQL → abandon
    if len(mots_significatifs) > 3:
        _client_nom_cache[nom_lower] = ""
        print(f"   ⚠️  [Recherche Nom] '{nom}' trop long ({len(mots_significatifs)} mots) → NL2SQL_LIBRE")
        return ""

    _nb_appels = 0
    for essai_fn, essai_val in [(_appel_fiche, nom), (_appel_fiche, " ".join(mots_significatifs))]:
        if _nb_appels >= MAX_RECHERCHE_NOM_CALLS:
            break
        t = await essai_fn(essai_val)
        _nb_appels += 1
        if t:
            code = _extraire_code(t)
            if code:
                _client_nom_cache[nom_lower] = code
                print(f"   ✅ [MCP] '{essai_val}' → {code}")
                return code

    for taille in range(len(mots_significatifs), 0, -1):
        for combo in itertools.combinations(mots_significatifs, taille):
            if _nb_appels >= MAX_RECHERCHE_NOM_CALLS:
                break
            sous_nom = " ".join(combo)
            if len(sous_nom) < 2:
                continue
            for fn in (_appel_fiche, _appel_statut):
                if _nb_appels >= MAX_RECHERCHE_NOM_CALLS:
                    break
                t = await fn(sous_nom)
                _nb_appels += 1
                if t:
                    code = _extraire_code(t)
                    if code:
                        _client_nom_cache[nom_lower] = code
                        print(f"   ✅ [MCP Combo] '{sous_nom}' → {code}")
                        return code

    _client_nom_cache[nom_lower] = ""
    print(f"   ⚠️  [Recherche Nom] '{nom}' introuvable")
    return ""