"""
Chargement et gestion des configurations et des données pour l'application semantic embeddings.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import math
import os
import re
from pathlib import Path
import numpy as np
import yaml
try:
    import rapidfuzz
except ImportError:
    rapidfuzz = None
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
EMBED_MODEL = os.getenv('SEMANTIC_EMBED_MODEL', 'nomic-embed-text')
_CACHE_PATH = Path(os.getenv('SEMANTIC_CACHE_PATH', str(Path(__file__).parent / 'semantic_embeddings_cache.json')))
CONFIG_PATH = Path(os.getenv('SEMANTIC_CONFIG_PATH', str(Path(__file__).parent / 'semantic_examples.yaml')))
from langchain_ollama import OllamaEmbeddings
_embedder: OllamaEmbeddings | None = None
_ref_embeddings: dict[str, list[list[float]]] = {}
_ref_lock: asyncio.Lock | None = None
_warmed_up = False
_config: dict = {}
EXEMPLES_PAR_ACTION: dict[str, list[str]] = {}
ACTION_TO_FAMILY: dict[str, str] = {}
FAMILY_ACTIONS: dict[str, list[str]] = {}
DEFAULTS: dict = {'threshold': 0.9, 'margin': 0.08, 'centroid_weight': 0.6, 'topk_weight': 0.4, 'family_threshold': 0.6}
_action_centroids: dict[str, list[float]] = {}
_family_centroids: dict[str, list[float]] = {}
_ref_index: VectorIndex | None = None
NORMALISATION = {'clients': 'client', 'clientèle': 'client', 'clienteles': 'client', 'facturation': 'facture', 'facturé': 'facture', 'facturer': 'facture', 'commande': 'commande', 'commandes': 'commande', 'fournisseurs': 'fournisseur', 'articles': 'article', 'livraison': 'livraison', 'livrer': 'livraison', 'paiement': 'reglement', 'payer': 'reglement', 'règlement': 'reglement', 'régler': 'reglement', 'achats': 'achat', 'acheter': 'achat', 'ventes': 'vente', 'vendre': 'vente', 'stocks': 'stock', 'stocker': 'stock'}
_PREFIXES_POLITESSE = re.compile("^(est-ce possible de|je voudrais|merci de|peux-tu|j'aimerais|pourrais-tu|pouvez-vous|voudriez-vous)\\s+", re.IGNORECASE)

def normaliser_phrase(text: str) -> str:
    """
Remplace les séquences de caractères spécifiques par des valeurs de substitution.
"""
    text = _PREFIXES_POLITESSE.sub('', text.strip())
    text = re.sub('\\bCLI\\d+\\b', '<CLIENT>', text, flags=re.IGNORECASE)
    text = re.sub('\\b(?:FA|FF|FC)\\d+\\b', '<FACTURE>', text, flags=re.IGNORECASE)
    text = re.sub('\\bBL\\d+\\b', '<BL>', text, flags=re.IGNORECASE)
    text = re.sub('\\bFOUR\\d+\\b', '<FOURNISSEUR>', text, flags=re.IGNORECASE)
    text = re.sub('\\b(?:REF|SKU)\\d+\\b', '<ARTICLE>', text, flags=re.IGNORECASE)
    return text

def normaliser_linguistique(texte: str) -> str:
    """
Fonction de normalisation de texte en linguistique : elle supprime les ponctuations et remplace les mots non normalisés par leur forme normalisée.
"""
    words = texte.lower().split()
    normalized_words = []
    for w in words:
        w_clean = w.strip('.,;:!?()')
        w_norm = NORMALISATION.get(w_clean, w_clean)
        normalized_words.append(w_norm)
    return ' '.join(normalized_words)

def preprocess_text(text: str) -> str:
    """
Normalise un texte à l'aide de deux fonctions supplémentaires : normaliser_phrase et normaliser_linguistique.
"""
    return normaliser_linguistique(normaliser_phrase(text))

class VectorIndex:
    """
Calcul et stockage des vecteurs d'embeddings, recherche de similarités entre les vecteurs.
"""

    def __init__(self, dimension: int):
        """
Initialisation d'un objet avec une dimension spécifique et des listes pour stocker les embeddings et la métadonnée.
"""
        self.dimension = dimension
        self.embeddings = []
        self.metadata = []

    def add_vectors(self, vecs: list[list[float]], metadata: list[tuple[str, str, str]]):
        """
Cette fonction ajoute un ensemble de vecteurs associés à des métadonnées à une collection existante d'embeddings.
"""
        if not vecs:
            return
        vecs_np = np.array(vecs, dtype='float32')
        norms = np.linalg.norm(vecs_np, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs_np = vecs_np / norms
        self.embeddings.extend(vecs_np.tolist())
        self.metadata.extend(metadata)

    def search(self, query_vec: list[float], k: int=10) -> list[tuple[float, str, str, str]]:
        """
Fonction utilisée pour effectuer une recherche d'éléments similaires à un vecteur de recherche dans un ensemble d'éléments vectoriels, avec possibilité de spécifier le nombre de résultats à retourner.
"""
        q_np = np.array([query_vec], dtype='float32')
        q_norm = np.linalg.norm(q_np)
        if q_norm > 0:
            q_np = q_np / q_norm
        embs_np = np.array(self.embeddings, dtype='float32')
        if len(embs_np) == 0:
            return []
        scores = np.dot(embs_np, q_np.T).flatten()
        top_indices = np.argsort(scores)[::-1][:k]
        results = []
        for idx in top_indices:
            action, orig, prep = self.metadata[idx]
            results.append((float(scores[idx]), action, orig, prep))
        return results

def load_config_and_examples():
    """
Lecture et mise à jour des configurations et des exemples associés aux actions.
"""
    global _config, EXEMPLES_PAR_ACTION, ACTION_TO_FAMILY, FAMILY_ACTIONS, DEFAULTS
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f'Missing config file {CONFIG_PATH}')
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        _config = yaml.safe_load(f)
    DEFAULTS.update(_config.get('defaults', {}))
    EXEMPLES_PAR_ACTION.clear()
    ACTION_TO_FAMILY.clear()
    FAMILY_ACTIONS.clear()
    for family, fam_data in _config.get('families', {}).items():
        FAMILY_ACTIONS[family] = []
        for action, act_data in fam_data.get('actions', {}).items():
            examples = act_data.get('examples', [])
            EXEMPLES_PAR_ACTION[action] = list(examples)
            ACTION_TO_FAMILY[action] = family
            FAMILY_ACTIONS[family].append(action)
load_config_and_examples()

def _hash_examples_and_config() -> str:
    """
Génère un hachage SHA-256 pour les exemples et la configuration.
"""
    yaml_content = ''
    if CONFIG_PATH.exists():
        yaml_content = CONFIG_PATH.read_text(encoding='utf-8')
    raw = f'{yaml_content}||{EMBED_MODEL}||768||{OLLAMA_BASE_URL}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def _get_embedder() -> OllamaEmbeddings:
    """
Récupère et retourne l'instance d'OllamaEmbeddings si elle n'existe pas déjà, et la cache pour futures requêtes.
"""
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
    return _embedder

def _cosine(a: list[float], b: list[float]) -> float:
    """
Calcule la similarité cosinus entre deux vecteurs numériques.
"""
    dot = sum((x * y for x, y in zip(a, b)))
    normA = math.sqrt(sum((x * x for x in a)))
    normB = math.sqrt(sum((y * y for y in b)))
    if normA == 0 or normB == 0:
        return 0.0
    return dot / (normA * normB)

def _charger_cache() -> dict | None:
    """
Lire et charger les données de cache.
"""
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding='utf-8'))
        if data.get('hash') == _hash_examples_and_config():
            return data.get('embeddings')
    except Exception:
        pass
    return None

def _sauvegarder_cache(embeddings: dict[str, list[list[float]]]):
    """
Sauvegarder les émotions d'un modèle dans un cache au format JSON.
"""
    try:
        _CACHE_PATH.write_text(json.dumps({'hash': _hash_examples_and_config(), 'embeddings': embeddings}, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass

def compute_centroids():
    """
Calcul des centres de gravité pour les actions et les familles de tâches.
"""
    global _action_centroids, _family_centroids, _ref_index
    _action_centroids.clear()
    _family_centroids.clear()
    for action, vecs in _ref_embeddings.items():
        if not vecs:
            continue
        arr = np.array(vecs, dtype='float32')
        centroid = np.mean(arr, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        _action_centroids[action] = centroid.tolist()
    for family, fam_data in _config.get('families', {}).items():
        family_vecs = []
        for action in fam_data.get('actions', {}).keys():
            if action in _ref_embeddings:
                family_vecs.extend(_ref_embeddings[action])
        if not family_vecs:
            continue
        arr = np.array(family_vecs, dtype='float32')
        centroid = np.mean(arr, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        _family_centroids[family] = centroid.tolist()
    if _ref_embeddings:
        first_action = list(_ref_embeddings.keys())[0]
        dim = len(_ref_embeddings[first_action][0])
        _ref_index = VectorIndex(dim)
        all_vecs = []
        metadata = []
        for action, vecs in _ref_embeddings.items():
            examples = EXEMPLES_PAR_ACTION[action]
            for vec, orig in zip(vecs, examples):
                all_vecs.append(vec)
                metadata.append((action, orig, preprocess_text(orig)))
        _ref_index.add_vectors(all_vecs, metadata)

async def warmup_semantic_classifier():
    """
La fonction `warmup_semantic_classifier` charge les exemples et calcule les embeddings de référence pour une classification sémantique.
"""
    global _ref_embeddings, _ref_lock, _warmed_up
    if _ref_lock is None:
        _ref_lock = asyncio.Lock()
    async with _ref_lock:
        if _warmed_up:
            return
        load_config_and_examples()
        cached = _charger_cache()
        if cached:
            _ref_embeddings = cached
            _warmed_up = True
            compute_centroids()
            nb = sum((len(v) for v in cached.values()))
            print(f'   ✅ [Sémantique] {nb} exemples chargés depuis {_CACHE_PATH} — centroïdes calculés.')
            return
        print(f'   ℹ️  [Sémantique] Aucun cache valide trouvé à {_CACHE_PATH} → recalcul complet.')
        print('   ⏳ [Sémantique] Calcul des embeddings de référence...')
        embedder = _get_embedder()
        resultat: dict[str, list[list[float]]] = {}
        for action, phrases in EXEMPLES_PAR_ACTION.items():
            try:
                prepped_phrases = [preprocess_text(p) for p in phrases]
                vecs = await embedder.aembed_documents(prepped_phrases)
                resultat[action] = vecs
            except Exception as e:
                print(f"   ⚠️  [Sémantique] Échec embedding '{action}' : {e}")
        _ref_embeddings = resultat
        _warmed_up = True
        compute_centroids()
        _sauvegarder_cache(resultat)
        nb = sum((len(v) for v in resultat.values()))
        print(f'   ✅ [Sémantique] {nb} exemples encodés et mis en cache.')

def get_lexical_score(s1: str, s2: str) -> float:
    """
Calcule le score lexical de similarité entre deux chaînes de caractères en utilisant la fonction token_set_ratio de rapidfuzz ou une méthode de similarité basée sur les ensembles de mots si cette bibliothèque n'est pas disponible.
"""
    if rapidfuzz is not None:
        return float(rapidfuzz.fuzz.token_set_ratio(s1, s2) / 100.0)
    else:
        w1 = set(s1.split())
        w2 = set(s2.split())
        if not w1 or not w2:
            return 0.0
        return len(w1.intersection(w2)) / len(w1.union(w2))

def get_action_config(action: str) -> dict:
    """
Renvoie la configuration d'une action dans les familles de configuration spécifiées.
"""
    for family, fam_data in _config.get('families', {}).items():
        if action in fam_data.get('actions', {}):
            return fam_data['actions'][action]
    return {}

def get_family_threshold(family: str) -> float:
    """
Fonction permettant de récupérer le seuil de famille spécifique à une famille donnée.
"""
    fam_data = _config.get('families', {}).get(family, {})
    return fam_data.get('family_threshold', DEFAULTS.get('family_threshold', 0.6))

def calculer_similarite_maximale(phrase: str, action: str) -> float:
    """
Calcule la similarité maximale entre une phrase et des exemples associés à une action donnée.
"""
    if action not in EXEMPLES_PAR_ACTION or not EXEMPLES_PAR_ACTION[action]:
        return 0.0
    try:
        embedder = _get_embedder()
        prep_phrase = preprocess_text(phrase)
        vec_q = embedder.embed_query(prep_phrase)
    except Exception as e:
        print(f'   ⚠️  [Sémantique] Embedding synchrone échoué : {e}')
        return 0.0
    vecs_ref = _ref_embeddings.get(action, [])
    examples = EXEMPLES_PAR_ACTION.get(action, [])
    max_sim = 0.0
    for vec_ref, ex in zip(vecs_ref, examples):
        cos_val = _cosine(vec_q, vec_ref)
        lex_val = get_lexical_score(prep_phrase, preprocess_text(ex))
        hybrid = 0.8 * cos_val + 0.2 * lex_val
        if hybrid > max_sim:
            max_sim = hybrid
    return max_sim

def get_weighted_top5_hybrid_score(query_emb: list[float], query_prep: str, action: str) -> float:
    """
Calcule le score hybride pondéré des 5 exemples les plus similaires à un requête.
"""
    vecs = _ref_embeddings.get(action, [])
    examples = EXEMPLES_PAR_ACTION.get(action, [])
    if not vecs:
        return 0.0
    scores = []
    for vec_ref, ex in zip(vecs, examples):
        cos_val = _cosine(query_emb, vec_ref)
        lex_val = get_lexical_score(query_prep, preprocess_text(ex))
        hybrid_score = 0.8 * cos_val + 0.2 * lex_val
        scores.append(hybrid_score)
    scores_sorted = sorted(scores, reverse=True)
    top_k = scores_sorted[:5]
    weights = [0.4, 0.25, 0.15, 0.1, 0.1]
    if len(top_k) < 5:
        w_sub = weights[:len(top_k)]
        sum_w = sum(w_sub)
        if sum_w > 0:
            w_sub = [w / sum_w for w in w_sub]
        else:
            w_sub = [1.0 / len(top_k)] * len(top_k)
        return sum((s * w for s, w in zip(top_k, w_sub)))
    else:
        return sum((s * w for s, w in zip(top_k, weights)))

async def classifier_semantique(question: str) -> tuple[str | None, float, float]:
    """
Classeur et classifie les questions en fonction de leurs scores de similarité avec des centrales sémantiques.
"""
    if not _warmed_up:
        await warmup_semantic_classifier()
    if not _ref_embeddings:
        return (None, 0.0, 0.0)
    try:
        embedder = _get_embedder()
        query_prep = preprocess_text(question)
        query_emb = await embedder.aembed_query(query_prep)
    except Exception as e:
        print(f'   ⚠️  [Sémantique] Embedding question échoué : {e}')
        return (None, 0.0, 0.0)
    family_scores = {}
    for family, centroid in _family_centroids.items():
        family_scores[family] = _cosine(query_emb, centroid)
    if not family_scores:
        return (None, 0.0, 0.0)
    best_family = max(family_scores, key=family_scores.get)
    best_fam_score = family_scores[best_family]
    family_threshold = get_family_threshold(best_family)
    if best_fam_score < family_threshold:
        print(f"   🔎 [Sémantique] Famille peu confiante '{best_family}' score={best_fam_score:.3f} < {family_threshold}")
        return (None, 0.0, 0.0)
    sorted_families = sorted(family_scores.items(), key=lambda x: x[1], reverse=True)
    top_2_families = [x[0] for x in sorted_families[:2]]
    actions_to_check = []
    for fam in top_2_families:
        actions_to_check.extend(FAMILY_ACTIONS.get(fam, []))
    centroid_weight = DEFAULTS.get('centroid_weight', 0.6)
    topk_weight = DEFAULTS.get('topk_weight', 0.4)
    action_scores = {}
    for action in actions_to_check:
        if action not in _action_centroids or action not in _ref_embeddings:
            continue
        cos_centroid = _cosine(query_emb, _action_centroids[action])
        weighted_top5 = get_weighted_top5_hybrid_score(query_emb, query_prep, action)
        combined_score = centroid_weight * cos_centroid + topk_weight * weighted_top5
        action_scores[action] = combined_score
    if not action_scores:
        return (None, 0.0, 0.0)
    sorted_actions = sorted(action_scores.items(), key=lambda x: x[1], reverse=True)
    meilleure_action = sorted_actions[0][0]
    meilleur_score = sorted_actions[0][1]
    deuxieme_score = 0.0
    if len(sorted_actions) > 1:
        deuxieme_score = sorted_actions[1][1]
    sorted_actions = sorted(action_scores.items(), key=lambda x: x[1], reverse=True)
    return (meilleure_action, meilleur_score, deuxieme_score)

async def inserer_exemple_valide(action: str, phrase: str) -> bool:
    """
Vérifier l'exemplarité d'une phrase et la mettre à jour si elle est valable.
"""
    if action not in EXEMPLES_PAR_ACTION:
        print(f"   ⚠️  [Sémantique] Action inconnue '{action}'.")
        return False
    if phrase.strip().lower() in {e.lower() for e in EXEMPLES_PAR_ACTION[action]}:
        return False
    try:
        embedder = _get_embedder()
        prep_phrase = preprocess_text(phrase)
        vec = await embedder.aembed_query(prep_phrase)
    except Exception as e:
        print(f"   ⚠️  [Sémantique] Embedding échoué pour '{phrase}' : {e}")
        return False
    EXEMPLES_PAR_ACTION[action].append(phrase)
    _ref_embeddings.setdefault(action, []).append(vec)
    updated_yaml = False
    for family, fam_data in _config.get('families', {}).items():
        if action in fam_data.get('actions', {}):
            fam_data['actions'][action].setdefault('examples', []).append(phrase)
            updated_yaml = True
            break
    if updated_yaml:
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                yaml.dump(_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print(f"   📥 [Sémantique] Exemple sauvegardé dans le YAML : '{phrase}' → {action}")
        except Exception as e:
            print(f'   ⚠️  [Sémantique] Échec écriture YAML : {e}')
    compute_centroids()
    _sauvegarder_cache(_ref_embeddings)
    return True