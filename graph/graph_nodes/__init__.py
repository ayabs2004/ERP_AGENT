"""
graph_nodes/ — Module des nœuds du graphe LangGraph.
Contient :
  - noeud_classifier() : classification
  - noeud_lecture() : exécution actions lecture
  - noeud_nl2sql_libre() : requêtes NL2SQL
  - noeud_confirmation() : validation écriture
  - noeud_ecriture() : exécution écriture
  - noeud_workflow() : flux workflow
  - noeud_kb() : base de connaissances
  - noeud_synthese() : synthèse réponse
  - noeud_complements() : gestion compléments
  - noeud_collecte_draft() : collecte draft
  - noeud_preview_draft() : preview draft
  - noeud_execution_draft() : exécution draft
"""

import asyncio
import json
import logging
import time
from typing import Any

from common import _safe_str
from session import CopilotState, _etat_initial, _extraire_dernier_document
from extraction import (
    _ner_extraire_entites, _extraire_code_ou_nom_depuis_texte,
    _nettoyer_nom_client, _est_nom_valide, _corriger_ref_article,
)
from classification import _pre_classifier, _est_fallback_generique
from formatting import _formater_reponse_directe, _formater_nl2sql_brut

logger = logging.getLogger("sage.erp.graph_nodes")


# ─────────────────────────────────────────────────────────────────────
# NŒUD CLASSIFIER
# ─────────────────────────────────────────────────────────────────────
async def noeud_classifier(state: CopilotState) -> CopilotState:
    """Classification de la demande."""
    # Implémentation simplifiée - à compléter avec la logique complète
    state["intention"] = "ERP"
    state["action"] = "NL2SQL_LIBRE"
    state["score_confiance"] = 0.8
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD LECTURE
# ─────────────────────────────────────────────────────────────────────
async def noeud_lecture(state: CopilotState) -> CopilotState:
    """Exécution des actions de lecture."""
    # Implémentation simplifiée
    state["reponse_finale"] = "Action lecture non implémentée dans ce module"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD NL2SQL
# ─────────────────────────────────────────────────────────────────────
async def noeud_nl2sql_libre(state: CopilotState) -> CopilotState:
    """Exécution requête NL2SQL."""
    # Implémentation simplifiée
    state["reponse_finale"] = "Action NL2SQL non implémentée dans ce module"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD CONFIRMATION
# ─────────────────────────────────────────────────────────────────────
async def noeud_confirmation(state: CopilotState) -> CopilotState:
    """Validation avant écriture."""
    # Implémentation simplifiée
    state["validation_ok"] = True
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD ÉCRITURE
# ─────────────────────────────────────────────────────────────────────
async def noeud_ecriture(state: CopilotState) -> CopilotState:
    """Exécution des actions d'écriture."""
    # Implémentation simplifiée
    state["reponse_finale"] = "Action écriture non implémentée dans ce module"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD WORKFLOW
# ─────────────────────────────────────────────────────────────────────
async def noeud_workflow(state: CopilotState) -> CopilotState:
    """Exécution workflow."""
    # Implémentation simplifiée
    state["reponse_finale"] = "Workflow non implémenté dans ce module"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD KB
# ─────────────────────────────────────────────────────────────────────
async def noeud_kb(state: CopilotState) -> CopilotState:
    """Recherche dans la base de connaissances."""
    # Implémentation simplifiée
    state["reponse_finale"] = "KB non implémentée dans ce module"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD SYNTHÈSE
# ─────────────────────────────────────────────────────────────────────
async def noeud_synthese(state: CopilotState) -> CopilotState:
    """Synthèse de la réponse."""
    rb = state.get("reponse_brute", "") or ""
    act = state.get("action", "")
    
    if state.get("reponse_finale"):
        return state
    
    if rb.startswith("__ERREUR__"):
        state["reponse_finale"] = f"❌ Erreur : {rb.replace('__ERREUR__:', '')}"
        return state
    
    if rb.startswith("__INCONNU__"):
        state["reponse_finale"] = f"⚠️  Action inconnue : {rb}"
        return state
    
    # Formatage direct
    formatted = _formater_reponse_directe(act, rb)
    if formatted:
        state["reponse_finale"] = formatted
        return state
    
    # Fallback
    state["reponse_finale"] = rb or "⚠️  Aucune réponse."
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD COMPLÉMENTS
# ─────────────────────────────────────────────────────────────────────
async def noeud_complements(state: CopilotState) -> CopilotState:
    """Gestion des compléments d'information."""
    # Implémentation simplifiée
    state["reponse_finale"] = "Compléments non implémentés dans ce module"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD COLLECTE DRAFT
# ─────────────────────────────────────────────────────────────────────
async def noeud_collecte_draft(state: CopilotState) -> CopilotState:
    """Collecte des informations pour draft."""
    # Implémentation simplifiée
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD PREVIEW DRAFT
# ─────────────────────────────────────────────────────────────────────
async def noeud_preview_draft(state: CopilotState) -> CopilotState:
    """Preview du draft."""
    # Implémentation simplifiée
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD EXÉCUTION DRAFT
# ─────────────────────────────────────────────────────────────────────
async def noeud_execution_draft(state: CopilotState) -> CopilotState:
    """Exécution du draft confirmé."""
    # Implémentation simplifiée
    return state