"""
common.py — Module partagé entre orchestrateur_general, formatters, extraction.
Contient les constantes, helpers et cache communs pour éviter les dépendances
circulaires et les `from X import *` fragiles.
"""

import re
from typing import Optional

# ─────────────────────────────────────────────────────────────────────
# HELPERS GÉNÉRAUX
# ─────────────────────────────────────────────────────────────────────
def _safe_str(obj) -> str:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj).encode("utf-8", errors="replace").decode("utf-8")


# ─────────────────────────────────────────────────────────────────────
# CHEMIN DB
# ─────────────────────────────────────────────────────────────────────
# NB : plus de chemin DB codé en dur ici. Toute résolution de connexion
# DB doit passer exclusivement par adaptation.db_adapter.get_connection()
# (lui-même piloté par db_config.json / DB_DRIVER / DB_PATH). Si ce module
# a un jour besoin d'une connexion, importer :
#   from adaptation.db_adapter import get_connection
# et appeler get_connection() au point d'usage plutôt que de recalculer
# un chemin ici.


# ─────────────────────────────────────────────────────────────────────
# CONSTANTES SHARED — extraites de orchestrateur_general.py
# ─────────────────────────────────────────────────────────────────────
_ACTIONS_DEJA_TEXTE: set[str] = {
    "VERIFIER_STOCK", "STATUT_CLIENT",
    "LISTE_FOURNISSEURS", "TOP_FOURNISSEURS", "FICHE_FOURNISSEUR",
}

_STATUTS_ERREUR_MCP: set[str] = {
    "CLIENT_NON_TROUVE", "ARTICLE_NON_TROUVE", "STOCK_INSUFFISANT",
    "CLIENT_BLOQUE", "COMPOSANTS_INSUFFISANTS", "NON_TROUVE",
    "EXISTE_DEJA", "ERREUR",
}

_STATUTS_ACTIONS_V3_OK: set[str] = {
    "GENERE", "TRANSFORME", "CREE", "MODIFIE",
    "REGLE", "MOUVEMENT_ENREGISTRE", "INCHANGE",
}

# Sera peuplé par orchestrateur_general.py après l'import des formateurs
_FORMATEURS_JSON: dict[str, callable] = {}


# ─────────────────────────────────────────────────────────────────────
# CONSTANTES EXTRACTION NOM CLIENT — extraites de orchestrateur_general.py
# ─────────────────────────────────────────────────────────────────────
_PREFIXES_PARASITES = re.compile(
    r"^\s*(?:le\s+|la\s+|les\s+|l['\u2019]\s*|du\s+|de\s+la\s+|de\s+)?",
    re.IGNORECASE
)

_SUFFIXES_PARASITES = re.compile(
    r"\s+(?:dans|de|du|pour|avec|sur|est|a|au|aux|et|ou|les|des|qui|dont|que)\s*$",
    re.IGNORECASE
)

_MOTS_VIDES_NOM: set[str] = {
    "en", "fonction", "du", "de", "des", "le", "la", "les", "un", "une",
    "et", "ou", "que", "qui", "dont", "pour", "avec", "sur", "par",
    "au", "aux", "ce", "cette", "ces", "cet", "nombre", "total",
    "moyenne", "montant", "selon", "chaque", "tous", "toutes",
    "dans", "sans", "sous", "entre", "vers", "chez", "depuis",
}

_MOTS_METIER_INVALIDES: set[str] = {
    "client", "tiers", "societe", "société", "entreprise",
    "sarl", "sa", "sas", "le", "la", "les", "un", "une",
    "pour", "avec", "sur", "du", "de", "plus", "moins",
    "que", "dont", "ayant", "liste", "tous", "toutes",
    "bons", "bon", "livraison", "commande", "fabrication",
    "facture", "factures", "pieces", "piece", "unites", "unite",
    "fournisseur", "fournisseurs", "fourn", "grossiste",
    "achat", "achats", "reception", "réception",
    "article", "articles", "catalogue", "produit", "produits",
    "stock", "stocks", "rupture",
    "impayées", "impayees", "impayés", "impayé",
    "encours", "supérieur", "superieur", "inférieur", "inferieur",
    "actifs", "actif", "inactifs", "inactif", "bloqués", "bloques",
    "reglées", "réglées", "reglés", "réglés",
    "nombre", "montant", "moyenne", "total", "fonction",
    "quantite", "quantité",
}

_MOTS_PREFIX_CLIENT = (
    r"(?:informations?\s+sur\s+(?:le\s+|la\s+)?(?:client\s+|société\s+|tiers\s+)?)"
    r"|(?:fiche\s+(?:du\s+|de\s+la\s+)?(?:client\s+|société\s+|tiers\s+)?)"
    r"|(?:statut\s+(?:actuel\s+)?(?:du\s+|de\s+la\s+)?(?:client\s+|société\s+|tiers\s+)?)"
    r"|(?:(?:non\s+réglées?\s+)?(?:du\s+|de\s+la\s+)?(?:client\s+|tiers\s+))"
    r"|(?:(?:toutes?\s+les\s+)?factures?\s+(?:du\s+|de\s+la\s+)(?:client\s+))"
    r"|(?:pour\s+(?:le\s+|la\s+)?(?:client\s+|tiers\s+)?)"
    r"|(?:(?:du|de|le|la)\s+client\s+)"
)

_PATTERN_NOM_CLIENT = re.compile(
    r"(?:" + _MOTS_PREFIX_CLIENT + r")"
    r"((?:société\s+|sarl\s+|sas\s+|sa\s+)?"
    r"[A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&'.]{1,80}?)"
    r"(?:\s*[?.,;!]|\s*$)",
    re.IGNORECASE
)

_PREFIXES_PIECES = re.compile(r"^(FA|FF|BL|BC|BF|OF|AV|BR|AF)[A-Z0-9]*\d+$", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────
# CACHE RÉFÉRENCES ARTICLES — mutable, partagé par extraction.py
# ─────────────────────────────────────────────────────────────────────
_articles_refs_cache: Optional[list[str]] = None


# ─────────────────────────────────────────────────────────────────────
# GLINER — lazy singleton partagé
# ─────────────────────────────────────────────────────────────────────
ENABLE_GLINER: bool = False  # sera surchargé par la config depuis orchestrateur_general
_gliner_model:      object | None = None
_gliner_load_tried: bool          = False


def _get_gliner_sync() -> object | None:
    """Charge GLiNER une seule fois (thread-safe via asyncio dans l'appelant)."""
    global _gliner_model, _gliner_load_tried
    if not ENABLE_GLINER:
        return None
    if _gliner_load_tried:
        return _gliner_model
    _gliner_load_tried = True
    try:
        from gliner import GLiNER
        import time
        print("   ⏳ [GLiNER] Chargement du modèle...")
        t0 = time.perf_counter()
        _gliner_model = GLiNER.from_pretrained(
            "urchade/gliner_multi-v2.1", load_tokenizer=True
        )
        print(f"   ✅ [GLiNER] Prêt en {time.perf_counter() - t0:.1f}s")
    except ImportError:
        print("   ⚠️  [GLiNER] pip install gliner")
        _gliner_model = None
    except Exception as e:
        print(f"   ⚠️  [GLiNER] {_safe_str(e)}")
        _gliner_model = None
    return _gliner_model