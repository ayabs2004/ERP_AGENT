"""
Ce script contient une implémentation d'un interpréteur de commandes basé sur des exemples de phrases pour interagir avec un système de gestion de clients et de fournisseurs. Il utilise des classes de classification pour identifier les commandes et exécuter les actions correspondantes.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path
import yaml
import numpy as np
os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))
import classification.semantic_classifier as sc
CAS_DE_BASE: list[tuple[str, str]] = [('liste tous les clients', 'LISTE_CLIENTS'), ('affiche les clients', 'LISTE_CLIENTS'), ('montre-moi mes clients', 'LISTE_CLIENTS'), ('donne la liste des clients', 'LISTE_CLIENTS'), ("bonjour, peux-tu me dire combien j'ai de clients actifs", 'LISTE_CLIENTS'), ('qui sont nos clients', 'LISTE_CLIENTS'), ('top 5 clients', 'TOP_CLIENTS'), ("meilleurs clients par chiffre d'affaires", 'TOP_CLIENTS'), ('qui achète le plus', 'TOP_CLIENTS'), ('fiche du client CLI001', 'FICHE_CLIENT'), ('informations sur le client Dupont', 'FICHE_CLIENT'), ('profil du client Martin', 'FICHE_CLIENT'), ('quel est le statut du client CLI005', 'STATUT_CLIENT'), ('le client CLI006 est-il bloqué', 'STATUT_CLIENT'), ('validité du client CLI007', 'STATUT_CLIENT'), ('crée un nouveau client', 'CREER_CLIENT'), ('ajoute un client appelé Dupont SARL', 'CREER_CLIENT'), ('bloque le client CLI009', 'MODIFIER_STATUT'), ('débloque le client CLI010', 'MODIFIER_STATUT'), ('que recommandes-tu pour le client CLI016', 'RECOMMANDATION'), ('liste tous les fournisseurs', 'LISTE_FOURNISSEURS'), ('affiche les fournisseurs', 'LISTE_FOURNISSEURS'), ('fiche du fournisseur FOUR002', 'FICHE_FOURNISSEUR'), ('top des fournisseurs', 'TOP_FOURNISSEURS'), ('crée un nouveau fournisseur', 'CREER_FOURNISSEUR'), ('liste tous les articles', 'LISTE_ARTICLES'), ('affiche le catalogue produits', 'LISTE_ARTICLES'), ("quel est le stock de l'article ECRAN4K", 'VERIFIER_STOCK'), ('articles en rupture de stock', 'VERIFIER_STOCK'), ('palmarès des articles les plus vendus', 'PALMARES_ARTICLES'), ('marge brute par article', 'RENTABILITE'), ("quel est le seuil de stock de l'article REF222", 'SEUIL_STOCK'), ('crée un bon de livraison pour le client CLI012', 'GENERER_DOC'), ('génère une facture pour ABC', 'GENERER_DOC'), ('transforme le BL BL000123 en facture', 'TRANSFORMER_DOC'), ('crée un avoir pour la facture FA000789', 'CREER_AVOIR'), ('règle la facture FA000456', 'REGLEMENT'), ('toutes les factures du client CLI008', 'TOUTES_FACTURES_CLIENT'), ('factures impayées', 'FACTURES_NON_REGLEES'), ('factures fournisseur impayées', 'FACTURES_NON_REGLEES_FOURN'), ('documents entre deux dates', 'DOCS_PERIODE'), ('traite la commande complète du client CLI015', 'WORKFLOW_COMMANDE'), ("chiffre d'affaires global", 'CA_GLOBAL'), ('CA par mois', 'SAISONNALITE'), ('délai de paiement moyen', 'DSO'), ('analyse RFM des clients', 'RFM'), ("clients en baisse de chiffre d'affaires", 'CLIENTS_BAISSE'), ('génère une offre de prix pour le client CLI014', 'OFFRE_PRIX_EXCEL'), ('crée une déclaration du mois de juin', 'DECLARATION_EXCEL'), ('exporte la balance âgée', 'BALANCE_AGEE_EXCEL'), ('affiche le tableau de bord', 'DASHBOARD_EXCEL'), ('factures supérieures à 1000 euros', 'NL2SQL_LIBRE'), ('clients ayant plus de 3 factures', 'NL2SQL_LIBRE'), ('clients inactifs depuis 6 mois', 'NL2SQL_LIBRE'), ('quelle est la procédure pour créer un BL', 'RECHERCHE_PROCEDURE'), ('comment fait-on pour bloquer un client', 'RECHERCHE_PROCEDURE'), ('liste toutes les procédures', 'LISTE_PROCEDURES'), ("entrée de stock pour l'article REF789", 'MOUVEMENT_STOCK'), ("propose une commande d'achat pour cet article", 'PROPOSITION_ACHAT')]

def penalite(regression: bool, fp: bool, fn: bool) -> float:
    """
Cette fonction calcule une pénalité en fonction de trois critères de performance d'un modèle de classification : regression, fausse positif et fausse négatif.
"""
    return 5.0 * int(regression) + 2.0 * int(fp) + 1.0 * int(fn)

async def _precompute_embeddings(cas: list[tuple[str, str]], embedder) -> dict[str, list[float]]:
    """
Précalcule les embeddings des questions fournies.
"""
    cache: dict[str, list[float]] = {}
    total = len({sc.preprocess_text(q) for q, _ in cas})
    fait = 0
    for question, _ in cas:
        query_prep = sc.preprocess_text(question)
        if query_prep in cache:
            continue
        try:
            cache[query_prep] = await embedder.aembed_query(query_prep)
        except Exception:
            pass
        fait += 1
        if fait % 20 == 0 or fait == total:
            print(f'   ⏳ Embeddings précalculés : {fait}/{total}')
    return cache

async def evaluer_seuils(action: str, threshold: float, margin: float, cas: list[tuple[str, str]], embeddings_cache: dict, action_centroids: dict, ref_examples: dict) -> float:
    """
Fonction d'évaluation des seuils qui compare les embeddings de requêtes à des centroids d'actions et attribue une pénalité en fonction du résultat.
"""
    seuil_bas = threshold - margin
    total_penalty = 0.0
    for question, action_attendue in cas:
        query_prep = sc.preprocess_text(question)
        query_emb = embeddings_cache.get(query_prep)
        if query_emb is None:
            continue
        cos_centroid = sc._cosine(query_emb, action_centroids.get(action, []))
        topk = sc.get_weighted_top5_hybrid_score(query_emb, query_prep, action) if action in ref_examples else 0.0
        centroid_w = sc.DEFAULTS.get('centroid_weight', 0.6)
        topk_w = sc.DEFAULTS.get('topk_weight', 0.4)
        score = centroid_w * cos_centroid + topk_w * topk
        marge = score - 0.0
        predicted_this_action = score >= threshold and marge >= margin
        if action_attendue == action:
            if not predicted_this_action:
                total_penalty += penalite(False, False, True)
        elif predicted_this_action:
            total_penalty += penalite(False, True, False)
    return total_penalty

async def calibrate_action(action: str, cas: list[tuple[str, str]], embeddings_cache: dict, ref_embeddings: dict, action_centroids: dict, ref_examples: dict) -> tuple[float, float]:
    """
Trouve les meilleures valeurs de seuil et de marge pour une action en calibrant sur un ensemble de cas.
"""
    print(f'   🔧 Calibration de {action}...')
    best_threshold = sc.DEFAULTS.get('threshold', 0.9)
    best_margin = sc.DEFAULTS.get('margin', 0.08)
    best_penalty = float('inf')
    for threshold in np.arange(0.7, 0.98, 0.02):
        for margin in np.arange(0.04, 0.15, 0.02):
            penalty = await evaluer_seuils(action, round(float(threshold), 2), round(float(margin), 2), cas, embeddings_cache, action_centroids, ref_examples)
            if penalty < best_penalty:
                best_penalty = penalty
                best_threshold = round(float(threshold), 2)
                best_margin = round(float(margin), 2)
    print(f'   ✅ {action}: threshold={best_threshold:.2f}, margin={best_margin:.2f}, penalty={best_penalty:.1f}')
    return (best_threshold, best_margin)

async def evaluer_seuil_famille(family: str, seuil: float, cas: list[tuple[str, str]], embeddings_cache: dict) -> float:
    """
Calculer un penalty pour une famille donnée en fonction d'un ensemble de cas et d'un seuil de similarité.
"""
    penalty = 0.0
    for question, action_attendue in cas:
        famille_attendue = sc.ACTION_TO_FAMILY.get(action_attendue, '')
        if not famille_attendue:
            continue
        query_prep = sc.preprocess_text(question)
        query_emb = embeddings_cache.get(query_prep)
        if query_emb is None:
            continue
        centroid = sc._family_centroids.get(family)
        if centroid is None:
            continue
        score = sc._cosine(query_emb, centroid)
        passe = score >= seuil
        if famille_attendue == family:
            if not passe:
                penalty += 3.0
        elif passe:
            penalty += 0.5
    return penalty

async def calibrate_family(family: str, cas: list[tuple[str, str]], embeddings_cache: dict) -> float:
    """
Cette fonction est destinée à trouver le seuil de calibration optimal pour un famille donnée en fonction de la pénalité associée.
"""
    print(f"   🔧 Calibration famille '{family}'...")
    best_seuil = sc.DEFAULTS.get('family_threshold', 0.6)
    best_penalty = float('inf')
    for seuil in np.arange(0.4, 0.85, 0.02):
        penalty = await evaluer_seuil_famille(family, round(float(seuil), 2), cas, embeddings_cache)
        if penalty < best_penalty:
            best_penalty = penalty
            best_seuil = round(float(seuil), 2)
    print(f"   ✅ Famille '{family}': family_threshold={best_seuil:.2f}, penalty={best_penalty:.1f}")
    return best_seuil

async def main():
    """
Affiche les informations de configuration de la calibration.
"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Affiche sans écrire le YAML')
    parser.add_argument('--extra', type=str, default=None, help='Fichier JSON de cas supplémentaires')
    parser.add_argument('--skip-families', action='store_true', help='Ne calibre que les actions, pas les seuils de famille')
    args = parser.parse_args()
    print('⏳ [Calibration] Chargement du classifieur sémantique...')
    await sc.warmup_semantic_classifier()
    cas = list(CAS_DE_BASE)
    if args.extra:
        extra_data = json.loads(Path(args.extra).read_text(encoding='utf-8'))
        for item in extra_data:
            cas.append((item['question'], item['action_attendue']))
        print(f'➕ {len(extra_data)} cas supplémentaires chargés depuis {args.extra}')
    embedder = sc._get_embedder()
    print(f'\n📊 [Calibration] {len(sc.EXEMPLES_PAR_ACTION)} actions, {len(cas)} cas de test\n')
    print('⏳ [Calibration] Précalcul des embeddings (une seule fois, réutilisés pour toute la grille)...')
    embeddings_cache = await _precompute_embeddings(cas, embedder)
    print(f'✅ [Calibration] {len(embeddings_cache)} embeddings uniques en cache.\n')
    new_thresholds: dict[str, dict] = {}
    for action in sc.EXEMPLES_PAR_ACTION.keys():
        if action not in sc._action_centroids:
            print(f'   ⚠️  {action}: pas de centroïde, seuils par défaut conservés')
            continue
        positive_cas = [(q, a) for q, a in cas if a == action]
        if len(positive_cas) < 2:
            print(f'   ℹ️  {action}: < 2 cas positifs, seuils par défaut conservés')
            continue
        threshold, margin = await calibrate_action(action, cas, embeddings_cache, sc._ref_embeddings, sc._action_centroids, sc.EXEMPLES_PAR_ACTION)
        new_thresholds[action] = {'threshold': threshold, 'margin': margin}
    new_family_thresholds: dict[str, float] = {}
    if not args.skip_families:
        print('\n📊 [Calibration] Seuils de famille...\n')
        for family in sc.FAMILY_ACTIONS.keys():
            if family not in sc._family_centroids:
                print(f'   ⚠️  {family}: pas de centroïde, seuil par défaut conservé')
                continue
            cas_famille = [(q, a) for q, a in cas if sc.ACTION_TO_FAMILY.get(a) == family]
            if len(cas_famille) < 2:
                print(f'   ℹ️  {family}: < 2 cas positifs, seuil par défaut conservé')
                continue
            new_family_thresholds[family] = await calibrate_family(family, cas, embeddings_cache)
    print('\n' + '═' * 60)
    print('📋 RÉSULTATS DE CALIBRATION')
    print('═' * 60)
    for action, vals in new_thresholds.items():
        print(f"   {action:30s}: threshold={vals['threshold']:.2f}, margin={vals['margin']:.2f}")
    for family, seuil in new_family_thresholds.items():
        print(f'   [famille] {family:22s}: family_threshold={seuil:.2f}')
    if args.dry_run:
        print('\n[dry-run] Aucune modification du YAML.')
        return
    config_path = Path('semantic_examples.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    updated = 0
    for family, fam_data in config.get('families', {}).items():
        for action, act_data in fam_data.get('actions', {}).items():
            if action in new_thresholds:
                act_data['threshold'] = new_thresholds[action]['threshold']
                act_data['margin'] = new_thresholds[action]['margin']
                updated += 1
        if family in new_family_thresholds:
            fam_data['family_threshold'] = new_family_thresholds[family]
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f'\n✅ YAML mis à jour pour {updated} actions et {len(new_family_thresholds)} familles → {config_path}')
    print('⚡ Invalidation du cache sémantique...')
    cache_path = Path(os.getenv('SEMANTIC_CACHE_PATH', './semantic_embeddings_cache.json'))
    if cache_path.exists():
        cache_path.unlink()
        print('   🗑️  Cache supprimé (sera recalculé au prochain démarrage)')
if __name__ == '__main__':
    asyncio.run(main())