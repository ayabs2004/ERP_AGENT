import re
import difflib
import logging
import sqlite3
from common import (
    _safe_str, _PREFIXES_PARASITES, _SUFFIXES_PARASITES,
    _MOTS_VIDES_NOM, _MOTS_METIER_INVALIDES, _PATTERN_NOM_CLIENT,
    _PREFIXES_PIECES, _db_path, _articles_refs_cache, ENABLE_GLINER,
    _get_gliner_sync,
)

logger = logging.getLogger(__name__)

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
    # PATCH F : si TOUS les mots sont des mots vides → jamais un nom valide
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


def _charger_refs_articles() -> list[str]:
    global _articles_refs_cache
    if _articles_refs_cache is not None:
        return _articles_refs_cache
    try:
        import sqlite3
        conn = sqlite3.connect(str(_db_path))
        rows = conn.execute("SELECT AR_Ref FROM F_ARTICLE").fetchall()
        conn.close()
        _articles_refs_cache = [r[0] for r in rows]
    except Exception:
        _articles_refs_cache = []
    return _articles_refs_cache


def _corriger_ref_article(ref: str) -> str:
    if not ref:
        return ref
    refs = _charger_refs_articles()
    if ref.upper() in refs:
        return ref.upper()
    matches = difflib.get_close_matches(ref.upper(), refs, n=1, cutoff=0.72)
    if matches:
        print(f"   🔧 [Fuzzy] '{ref}' → '{matches[0]}' (correction automatique)")
        return matches[0]
    return ref