"""Module implementing a guided conversational flow for creating a Sage 100 article.
It interacts with the MCP pool to read existing articles, retrieve families,
and finally create a new article based on user inputs, handling validation,
retries and confirmation."""

import logging
import json
from api.mcp_pool import pool as mcp_pool
from api.mcp_actions_sage import _get_conn, T_FAMILLE, C_FA_CODE, C_FA_INTITULE, T_ARTICLE, C_AR_REF, C_AR_DESIGN

logger = logging.getLogger(__name__)

AR_NATURE_OPTIONS = {
    0: "Marchandise (standard)",
    1: "Nomenclature / Article composé",
    2: "Gamme",
    3: "Gamme + Nomenclature",
    4: "Observé sur frais/remises (port, escompte, fidélité) — sens à confirmer",
}

AR_SUIVI_STOCK_OPTIONS = {
    0: "Aucun suivi (services, frais, remises...)",
    1: "Suivi individuel par n° de série/lot (ex: montres, appareils)",
    2: "Aucun suivi de lot observé actuellement sur cette base",
    5: "Suivi par lot à quantité multiple (ex: lingots, matières en vrac)",
}

AR_UNITE_VEN_OPTIONS = {
    1:  "Unité",
    2:  "Kg",
    3:  "Gramme",
    4:  "Litre",
    5:  "Heure",
    6:  "Mètre",
    7:  "m²",
    8:  "m³",
    9:  "Tonne",
    10: "Boîte",
}

def _formater_enum(options: dict) -> str:
    """Return a formatted string listing enum options, each line prefixed with a bullet."""
    return "\n".join(f"  🔸 `{k}` — {v}" for k, v in options.items())

def _formater_enum_suivi_stock(options: dict) -> str:
    """Return a formatted string listing stock‑tracking options, adding a warning note."""
    lignes = [f"  🔸 `{k}` — {v}" for k, v in options.items()]
    lignes.append("")
    lignes.append(
        "⚠️ Ces libellés sont déduits de l'observation des articles existants, "
        "pas de la documentation officielle Sage. En cas de doute, vérifiez le "
        "paramétrage exact dans la fiche article Sage avant de valider."
    )
    return "\n".join(lignes)

def _valider_smallint(question: str, options: dict):
    """Validate that *question* represents an integer present in *options*.
    Returns (value, None) on success or (None, error_message) on failure."""
    try:
        val = int(question.strip())
    except ValueError:
        cles = ", ".join(str(k) for k in options)
        return None, f"⚠️ Saisissez un entier parmi : {cles}"
    if val not in options:
        cles = ", ".join(str(k) for k in options)
        return None, f"⚠️ Valeur invalide. Choisissez parmi : {cles}"
    return val, None

def _est_non(texte: str) -> bool:
    """Return True if *texte* corresponds to a negative answer."""
    t = texte.lower().strip()
    return t in ("non", "n", "annuler", "stop", "non merci", "no")

def _est_oui(texte: str) -> bool:
    """Return True if *texte* corresponds to an affirmative answer."""
    t = texte