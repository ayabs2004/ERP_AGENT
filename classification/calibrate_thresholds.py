"""
calibrate_thresholds.py — Calibration automatique des seuils sémantiques (OPTIMISÉ)
======================================================================================
Objectif : trouver, pour chaque action, le couple (threshold, margin) qui
           minimise la pénalité : 5 * Régressions + 2 * FP + 1 * FN

CE QUI A CHANGÉ PAR RAPPORT À LA VERSION INITIALE (pourquoi c'était lent) :
----------------------------------------------------------------------------
Avant : evaluer_seuils() ré-appelait `embedder.aembed_query(question)` ET
        recalculait `cos_centroid` / `topk` pour CHAQUE combinaison
        (threshold, margin) de la grid search. Or ces valeurs ne dépendent
        QUE de (question, action) — jamais de threshold/margin.

        Pour une seule action calibrée sur 60 cas, avec une grille de
        14 thresholds x 6 margins = 84 combinaisons, ça faisait :
            60 * 84 = 5 040 appels d'embedding async (I/O lent !)
        répétés pour chaque action à calibrer (~50-60 actions) → des
        centaines de milliers d'appels réseau/CPU inutiles.

Après : on calcule chaque embedding UNE SEULE FOIS pour tout le script
        (cache par texte), puis pour chaque action on précalcule le score
        (cos_centroid, topk, score combiné) UNE SEULE FOIS par cas de test.
        Le grid search devient une simple boucle de comparaisons de floats
        en mémoire : ~O(nb_cas * nb_combinaisons) additions, plus aucun
        appel async. Le gain est de plusieurs ordres de grandeur.

Usage :
    python calibrate_thresholds.py
    python calibrate_thresholds.py --dry-run    (affiche sans écrire le YAML)
    python calibrate_thresholds.py --extra cas.json  (ajouter des cas depuis un fichier JSON)

Sortie :
    Met à jour threshold/margin dans semantic_examples.yaml pour chaque action.
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

# Ensure we run in the right working directory
os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

import semantic_classifier as sc

# ─────────────────────────────────────────────────────────────────────
# CAS DE TEST (question, action_attendue)
# ─────────────────────────────────────────────────────────────────────
CAS_DE_BASE: list[tuple[str, str]] = [
    # CLIENT
    ("liste tous les clients",                          "LISTE_CLIENTS"),
    ("affiche les clients",                             "LISTE_CLIENTS"),
    ("montre-moi mes clients",                          "LISTE_CLIENTS"),
    ("donne la liste des clients",                      "LISTE_CLIENTS"),
    ("bonjour, peux-tu me dire combien j'ai de clients actifs", "LISTE_CLIENTS"),
    ("qui sont nos clients",                            "LISTE_CLIENTS"),
    ("top 5 clients",                                   "TOP_CLIENTS"),
    ("meilleurs clients par chiffre d'affaires",        "TOP_CLIENTS"),
    ("qui achète le plus",                              "TOP_CLIENTS"),
    ("fiche du client CLI001",                          "FICHE_CLIENT"),
    ("informations sur le client Dupont",               "FICHE_CLIENT"),
    ("profil du client Martin",                         "FICHE_CLIENT"),
    ("quel est le statut du client CLI005",             "STATUT_CLIENT"),
    ("le client CLI006 est-il bloqué",                  "STATUT_CLIENT"),
    ("validité du client CLI007",                       "STATUT_CLIENT"),
    ("crée un nouveau client",                          "CREER_CLIENT"),
    ("ajoute un client appelé Dupont SARL",             "CREER_CLIENT"),
    ("bloque le client CLI009",                         "MODIFIER_STATUT"),
    ("débloque le client CLI010",                       "MODIFIER_STATUT"),
    ("que recommandes-tu pour le client CLI016",        "RECOMMANDATION"),
    # FOURNISSEUR
    ("liste tous les fournisseurs",                     "LISTE_FOURNISSEURS"),
    ("affiche les fournisseurs",                        "LISTE_FOURNISSEURS"),
    ("fiche du fournisseur FOUR002",                    "FICHE_FOURNISSEUR"),
    ("top des fournisseurs",                            "TOP_FOURNISSEURS"),
    ("crée un nouveau fournisseur",                     "CREER_FOURNISSEUR"),
    # ARTICLE
    ("liste tous les articles",                         "LISTE_ARTICLES"),
    ("affiche le catalogue produits",                   "LISTE_ARTICLES"),
    ("quel est le stock de l'article ECRAN4K",          "VERIFIER_STOCK"),
    ("articles en rupture de stock",                    "VERIFIER_STOCK"),
    ("palmarès des articles les plus vendus",           "PALMARES_ARTICLES"),
    ("marge brute par article",                         "RENTABILITE"),
    ("quel est le seuil de stock de l'article REF222",  "SEUIL_STOCK"),
    # DOCUMENT
    ("crée un bon de livraison pour le client CLI012",  "GENERER_DOC"),
    ("génère une facture pour ABC",                     "GENERER_DOC"),
    ("transforme le BL BL000123 en facture",            "TRANSFORMER_DOC"),
    ("crée un avoir pour la facture FA000789",          "CREER_AVOIR"),
    ("règle la facture FA000456",                       "REGLEMENT"),
    ("toutes les factures du client CLI008",            "TOUTES_FACTURES_CLIENT"),
    ("factures impayées",                               "FACTURES_NON_REGLEES"),
    ("factures fournisseur impayées",                   "FACTURES_NON_REGLEES_FOURN"),
    ("documents entre deux dates",                      "DOCS_PERIODE"),
    ("traite la commande complète du client CLI015",    "WORKFLOW_COMMANDE"),
    # ANALYTIQUE
    ("chiffre d'affaires global",                       "CA_GLOBAL"),
    ("CA par mois",                                     "SAISONNALITE"),
    ("délai de paiement moyen",                         "DSO"),
    ("analyse RFM des clients",                         "RFM"),
    ("clients en baisse de chiffre d'affaires",         "CLIENTS_BAISSE"),
    # EXPORT
    ("génère une offre de prix pour le client CLI014",  "OFFRE_PRIX_EXCEL"),
    ("crée une déclaration du mois de juin",            "DECLARATION_EXCEL"),
    ("exporte la balance âgée",                         "BALANCE_AGEE_EXCEL"),
    ("affiche le tableau de bord",                      "DASHBOARD_EXCEL"),
    # NL2SQL
    ("factures supérieures à 1000 euros",               "NL2SQL_LIBRE"),
    ("clients ayant plus de 3 factures",                "NL2SQL_LIBRE"),
    ("clients inactifs depuis 6 mois",                  "NL2SQL_LIBRE"),
    # PROCEDURE
    ("quelle est la procédure pour créer un BL",        "RECHERCHE_PROCEDURE"),
    ("comment fait-on pour bloquer un client",          "RECHERCHE_PROCEDURE"),
    ("liste toutes les procédures",                     "LISTE_PROCEDURES"),
    # STOCK_MVT
    ("entrée de stock pour l'article REF789",           "MOUVEMENT_STOCK"),
    ("propose une commande d'achat pour cet article",   "PROPOSITION_ACHAT"),
]

# Grilles de recherche (identiques à l'original)
THRESHOLD_GRID = [round(float(t), 2) for t in np.arange(0.70, 0.98, 0.02)]
MARGIN_GRID = [round(float(m), 2) for m in np.arange(0.04, 0.15, 0.02)]
FAMILY_THRESHOLD_GRID = [round(float(s), 2) for s in np.arange(0.40, 0.85, 0.02)]


def penalite(regression: bool, fp: bool, fn: bool) -> float:
    """Calcule la pénalité d'une classification."""
    return 5.0 * int(regression) + 2.0 * int(fp) + 1.0 * int(fn)


# ─────────────────────────────────────────────────────────────────────
# ÉTAPE 1 : précalcul des embeddings (UNE SEULE FOIS pour tout le script)
# ─────────────────────────────────────────────────────────────────────
async def precompute_embeddings(cas: list[tuple[str, str]], embedder) -> dict[str, list]:
    """Calcule l'embedding de chaque question unique une seule fois.
    Élimine les milliers de recalculs redondants de la version originale."""
    unique_questions = sorted({q for q, _ in cas})
    embeddings: dict[str, list] = {}

    print(f"⏳ Précalcul de {len(unique_questions)} embeddings uniques...")
    t0 = time.time()

    # Les embeddings restent async (un seul appel par question, en parallèle)
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


# ─────────────────────────────────────────────────────────────────────
# ÉTAPE 2 : précalcul des scores par action (une fois par (question, action))
# ─────────────────────────────────────────────────────────────────────
def precompute_action_scores(
    action: str,
    cas: list[tuple[str, str]],
    embeddings: dict,
    action_centroids: dict,
    ref_examples: dict,
) -> list[tuple[float, bool]]:
    """Pour chaque cas de test, calcule le score combiné (cos_centroid + topk)
    une seule fois. Retourne [(score, est_action_attendue), ...].
    Ce score ne dépend ni de threshold ni de margin -> calculé une seule fois,
    réutilisé pour les 84 combinaisons de la grid search."""
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
    """Grid-search step : pure arithmétique en mémoire, aucun appel réseau/async.
    C'est ici que se fait tout le gain de perf par rapport à l'original."""
    total_penalty = 0.0
    for score, is_expected in scored_cas:
        marge = score - 0.0  # simplifié, comme dans l'original : une seule action regardée
        predicted_this_action = score >= threshold and marge >= margin

        if is_expected:
            if not predicted_this_action:
                total_penalty += penalite(False, False, True)  # FN
        else:
            if predicted_this_action:
                total_penalty += penalite(False, True, False)  # FP
    return total_penalty


def calibrate_action(action: str, scored_cas: list[tuple[float, bool]]) -> tuple[float, float]:
    """Optimise (threshold, margin) pour une action via grille de recherche,
    en réutilisant les scores déjà précalculés (aucun recalcul d'embedding)."""
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


# ─────────────────────────────────────────────────────────────────────
# Calibration des seuils de FAMILLE (même principe : score précalculé une fois)
# ─────────────────────────────────────────────────────────────────────
def precompute_family_scores(
    family: str,
    cas: list[tuple[str, str]],
    embeddings: dict,
) -> list[tuple[float, bool]]:
    centroid = sc._family_centroids.get(family)
    scored: list[tuple[float, bool]] = []
    if centroid is None:
        return scored
    for question, action_attendue in cas:
        famille_attendue = sc.ACTION_TO_FAMILY.get(action_attendue, "")
        if not famille_attendue:
            continue
        entry = embeddings.get(question)
        if entry is None:
            continue
        score = sc._cosine(entry["emb"], centroid)
        scored.append((score, famille_attendue == family))
    return scored


def evaluer_seuil_famille_from_scores(scored_cas: list[tuple[float, bool]], seuil: float) -> float:
    penalty = 0.0
    for score, is_expected in scored_cas:
        passe = score >= seuil
        if is_expected:
            if not passe:
                penalty += 3.0   # FN famille : perte définitive, coûteux
        else:
            if passe:
                penalty += 0.5   # FP famille : rattrapable au niveau action
    return penalty


def calibrate_family(family: str, scored_cas: list[tuple[float, bool]]) -> float:
    print(f"   🔧 Calibration famille '{family}'...")
    best_seuil = sc.DEFAULTS.get("family_threshold", 0.60)
    best_penalty = float("inf")
    for seuil in FAMILY_THRESHOLD_GRID:
        penalty = evaluer_seuil_famille_from_scores(scored_cas, seuil)
        if penalty < best_penalty:
            best_penalty = penalty
            best_seuil = seuil
    print(f"   ✅ Famille '{family}': family_threshold={best_seuil:.2f}, penalty={best_penalty:.1f}")
    return best_seuil


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans écrire le YAML")
    parser.add_argument("--extra", type=str, default=None, help="Fichier JSON de cas supplémentaires")
    parser.add_argument("--skip-families", action="store_true",
                         help="Ne calibre que les actions, pas les seuils de famille")
    args = parser.parse_args()

    t_start = time.time()

    print("⏳ [Calibration] Chargement du classifieur sémantique...")
    await sc.warmup_semantic_classifier()

    cas = list(CAS_DE_BASE)
    if args.extra:
        extra_data = json.loads(Path(args.extra).read_text(encoding="utf-8"))
        for item in extra_data:
            cas.append((item["question"], item["action_attendue"]))
        print(f"➕ {len(extra_data)} cas supplémentaires chargés depuis {args.extra}")

    embedder = sc._get_embedder()

    print(f"\n📊 [Calibration] {len(sc.EXEMPLES_PAR_ACTION)} actions, {len(cas)} cas de test\n")

    # ── Précalcul UNIQUE des embeddings pour tout le script ─────────
    embeddings = await precompute_embeddings(cas, embedder)

    new_thresholds: dict[str, dict] = {}

    for action in sc.EXEMPLES_PAR_ACTION.keys():
        if action not in sc._action_centroids:
            print(f"   ⚠️  {action}: pas de centroïde, seuils par défaut conservés")
            continue

        # Only calibrate actions with at least 2 positive cases in test set
        positive_cas = [(q, a) for q, a in cas if a == action]
        if len(positive_cas) < 2:
            print(f"   ℹ️  {action}: < 2 cas positifs, seuils par défaut conservés")
            continue

        # Scores calculés UNE FOIS par action, réutilisés pour toute la grille
        scored_cas = precompute_action_scores(
            action, cas, embeddings, sc._action_centroids, sc.EXEMPLES_PAR_ACTION
        )
        threshold, margin = calibrate_action(action, scored_cas)
        new_thresholds[action] = {"threshold": threshold, "margin": margin}

    # ── Calibration des seuils de FAMILLE ────────────────────────────
    new_family_thresholds: dict[str, float] = {}
    if not args.skip_families:
        print("\n📊 [Calibration] Seuils de famille...\n")
        for family in sc.FAMILY_ACTIONS.keys():
            if family not in sc._family_centroids:
                print(f"   ⚠️  {family}: pas de centroïde, seuil par défaut conservé")
                continue
            cas_famille = [(q, a) for q, a in cas if sc.ACTION_TO_FAMILY.get(a) == family]
            if len(cas_famille) < 2:
                print(f"   ℹ️  {family}: < 2 cas positifs, seuil par défaut conservé")
                continue
            scored_family_cas = precompute_family_scores(family, cas, embeddings)
            new_family_thresholds[family] = calibrate_family(family, scored_family_cas)

    print("\n" + "═" * 60)
    print("📋 RÉSULTATS DE CALIBRATION")
    print("═" * 60)
    for action, vals in new_thresholds.items():
        print(f"   {action:30s}: threshold={vals['threshold']:.2f}, margin={vals['margin']:.2f}")
    for family, seuil in new_family_thresholds.items():
        print(f"   [famille] {family:22s}: family_threshold={seuil:.2f}")

    print(f"\n⏱️  Temps total : {time.time() - t_start:.2f}s")

    if args.dry_run:
        print("\n[dry-run] Aucune modification du YAML.")
        return

    # Update YAML
    config_path = Path("semantic_examples.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    updated = 0
    for family, fam_data in config.get("families", {}).items():
        for action, act_data in fam_data.get("actions", {}).items():
            if action in new_thresholds:
                act_data["threshold"] = new_thresholds[action]["threshold"]
                act_data["margin"] = new_thresholds[action]["margin"]
                updated += 1
        if family in new_family_thresholds:
            fam_data["family_threshold"] = new_family_thresholds[family]

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"\n✅ YAML mis à jour pour {updated} actions et {len(new_family_thresholds)} familles → {config_path}")
    print("⚡ Invalidation du cache sémantique...")
    cache_path = Path(os.getenv("SEMANTIC_CACHE_PATH", "./semantic_embeddings_cache.json"))
    if cache_path.exists():
        cache_path.unlink()
        print("   🗑️  Cache supprimé (sera recalculé au prochain démarrage)")


if __name__ == "__main__":
    asyncio.run(main())