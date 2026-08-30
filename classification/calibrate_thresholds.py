"""Calibrate semantic classifier thresholds.

This script precomputes embeddings for all test cases, evaluates scores for each
action and family, performs a grid‑search to find optimal (threshold, margin)
values, and updates the `semantic_examples.yaml` configuration file. It can be
run normally, in dry‑run mode, or with additional test cases supplied via a
JSON file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
import yaml
import numpy as np

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

import semantic_classifier as sc

CAS_DE_BASE: list[tuple[str, str]] = [
    ("liste tous les clients", "LISTE_CLIENTS"),
    ("affiche les clients", "LISTE_CLIENTS"),
    ("montre-moi mes clients", "LISTE_CLIENTS"),
    ("donne la liste des clients", "LISTE_CLIENTS"),
    ("bonjour, peux-tu me dire combien j'ai de clients actifs", "LISTE_CLIENTS"),
    ("qui sont nos clients", "LISTE_CLIENTS"),
    ("top 5 clients", "TOP_CLIENTS"),
    ("meilleurs clients par chiffre d'affaires", "TOP_CLIENTS"),
    ("qui achète le plus", "TOP_CLIENTS"),
    ("fiche du client CLI001", "FICHE_CLIENT"),
    ("informations sur le client Dupont", "FICHE_CLIENT"),
    ("profil du client Martin", "FICHE_CLIENT"),
    ("quel est le statut du client CLI005", "STATUT_CLIENT"),
    ("le client CLI006 est-il bloqué", "STATUT_CLIENT"),
    ("validité du client CLI007", "STATUT_CLIENT"),
    ("crée un nouveau client", "CREER_CLIENT"),
    ("ajoute un client appelé Dupont SARL", "CREER_CLIENT"),
    ("bloque le client CLI009", "MODIFIER_STATUT"),
    ("débloque le client CLI010", "MODIFIER_STATUT"),
    ("que recommandes-tu pour le client CLI016", "RECOMMANDATION"),
    ("liste tous les fournisseurs", "LISTE_FOURNISSEURS"),
    ("affiche les fournisseurs", "LISTE_FOURNISSEURS"),
    ("fiche du fournisseur FOUR002", "FICHE_FOURNISSEUR"),
    ("top des fournisseurs", "TOP_FOURNISSEURS"),
    ("crée un nouveau fournisseur", "CREER_FOURNISSEUR"),
    ("liste tous les articles", "LISTE_ARTICLES"),
    ("affiche le catalogue produits", "LISTE_ARTICLES"),
    ("quel est le stock de l'article ECRAN4K", "VERIFIER_STOCK"),
    ("articles en rupture de stock", "VERIFIER_STOCK"),
    ("palmarès des articles les plus vendus", "PALMARES_ARTICLES"),
    ("marge brute par article", "RENTABILITE"),
    ("quel est le seuil de stock de l'article REF222", "SEUIL_STOCK"),
    ("crée un bon de livraison pour le client CLI012", "GENERER_DOC"),
    ("génère une facture pour ABC", "GENERER_DOC"),
    ("transforme le BL BL000123 en facture", "TRANSFORMER_DOC"),
    ("crée un avoir pour la facture FA000789", "CREER_AVOIR"),
    ("règle la facture FA000456", "REGLEMENT"),
    ("toutes les factures du client CLI008", "TOUTES_FACTURES_CLIENT"),
    ("factures impayées", "FACTURES_NON_REGLEES"),
    ("factures fournisseur impayées", "FACTURES_NON_REGLEES_FOURN"),
    ("documents entre deux dates", "DOCS_PERIODE"),
    ("traite la commande complète du client CLI015", "WORKFLOW_COMMANDE"),
    ("chiffre d'affaires global", "CA_GLOBAL"),
    ("CA par mois", "SAISONNALITE"),
    ("délai de paiement moyen", "DSO"),
    ("analyse RFM des clients", "RFM"),
    ("clients en baisse de chiffre d'affaires", "CLIENTS_BAISSE"),
    ("génère une offre de prix pour le client CLI014", "OFFRE_PRIX_EXCEL"),
    ("crée une déclaration du mois de juin", "DECLARATION_EXCEL"),
    ("exporte la balance âgée", "BALANCE_AGEE_EXCEL"),
    ("affiche le tableau de bord", "DASHBOARD_EXCEL"),
    ("factures supérieures à 1000 euros", "NL2SQL_LIBRE"),
    ("clients ayant plus de 3 factures", "NL2SQL_LIBRE"),
    ("clients inactifs depuis 6 mois", "NL2SQL_LIBRE"),
    ("quelle est la procédure pour créer un BL", "RECHERCHE_PROCEDURE"),
    ("comment fait-on pour bloquer un client", "RECHERCHE_PROCEDURE"),
    ("liste toutes les procédures", "LISTE_PROCEDURES"),
    ("entrée de stock pour l'article REF789", "MOUVEMENT_STOCK"),
    ("propose une commande d'achat pour cet article", "PROPOSITION_ACHAT"),
]

THRESHOLD_GRID = [round(float(t), 2) for t in np.arange(0.70, 0.98, 0.02)]
MARGIN_GRID = [round(float(m), 2) for m in np.arange(0.04, 0.15, 0.02)]
FAMILY_THRESHOLD_GRID = [round(float(s), 2) for s in np.arange(0.40, 0.85, 0.02)]

def penalite(regression: bool, fp: bool, fn: bool) -> float:
    """Calculate the penalty for a classification outcome."""
    return 5.0 * int(regression) + 2.0 * int(fp) + 1.0 * int(fn)

async def precompute_embeddings(cas: list[tuple[str, str]], embedder) -> dict[str, list]:
    """Compute embeddings for each unique question once.

    This eliminates redundant embedding calculations by caching the result for
    every distinct question in the test set.
    """
    unique_questions = sorted({q for q, _ in cas})
    embeddings: dict[str, list] = {}

    print(f"⏳ Précalcul de {len(unique_questions)} embeddings uniques...")
    t0 = time.time()

    async def embed_one(q: str):
        query_prep = sc.preprocess_text(q)
        try:
            emb = await embedder.aembed_query(query_prep)
        except Exception:
            emb = None
        return q, query_prep, emb

    results = await asyncio.gather(*(embed_one(q) for q in unique_questions))
    for q, query_prep, emb in results:
        if emb is not None:
            embeddings[q] = {"prep": query_prep, "emb": emb}

    print(f"✅ Embeddings précalculés en {time.time() - t0:.2f}s\n")
    return embeddings

def precompute_action_scores(
    action: str,
    cas: list[tuple[str, str]],
    embeddings: dict,
    action_centroids: dict,
    ref_examples: dict,
) -> list[tuple[float, bool]]:
    """Compute a combined score for each test case of a given action.

    The score (centroid similarity weighted with top‑k hybrid score) is
    calculated once per (question, action) pair and reused for all threshold
    and margin combinations.
    """
    centroid_w = sc.DEFAULTS.get("centroid_weight", 0.6)
    topk_w = sc.DEFAULTS.get("topk_weight", 0.4)

    scored: list[tuple[float, bool]] = []
    for question, action_attendue in cas:
        entry = embeddings.get(question)
        if entry is None:
            continue
        query_emb, query_prep = entry["emb"], entry["prep"]

        cos_centroid = sc._cosine(query_emb, action_centroids.get(action, []))
        topk = sc.get_weighted_top5_hybrid_score(query_emb, query_prep, action) if action in ref_examples else 0.0
        score = centroid_w * cos_centroid + topk_w * topk

        scored.append((score, action_attendue == action))
    return scored

def evaluer_seuils_from_scores(
    scored_cas: list[tuple[float, bool]],
    threshold: float,
    margin: float,
) -> float:
    """Evaluate penalty for a given threshold and margin using pre‑computed scores."""
    total_penalty = 0.0
    for score, is_expected in scored_cas:
        marge = score - 0.0
        predicted_this_action = score >= threshold and marge >= margin

        if is_expected:
            if not predicted_this_action:
                total_penalty += penalite(False, False, True)
        else:
            if predicted_this_action:
                total_penalty += penalite(False, True, False)
    return total_penalty

def calibrate_action(action: str, scored_cas: list[tuple[float, bool]]) -> tuple[float, float]:
    """Find the optimal (threshold, margin) for an action via grid search."""
    print(f"   🔧 Calibration de {action}...")

    best_threshold = sc.DEFAULTS.get("threshold", 0.90)
    best_margin = sc.DEFAULTS.get("margin", 0.08)
    best_penalty = float("inf")

    for threshold in THRESHOLD_GRID:
        for margin in MARGIN_GRID:
            penalty = evaluer_seuils_from_scores(scored_cas, threshold, margin)
            if penalty < best_penalty:
                best_penalty = penalty
                best_threshold = threshold
                best_margin = margin

    print(f"   ✅ {action}: threshold={best_threshold:.2f}, margin={best_margin:.2f}, penalty={best_penalty:.1f}")
    return best_threshold, best_margin

def precompute_family_scores(
    family: str,