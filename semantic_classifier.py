"""
semantic_classifier.py — Classificateur sémantique par embeddings
===================================================================
Remplace le PREMIER étage de décision par une comparaison sémantique avancée
utilisant des centroïdes de famille/action, un index vectoriel et un score
lexical hybride avec RapidFuzz.

YAML configuration (semantic_examples.yaml) is the single source of truth.
Ce fichier semantic_classifier.py est le cœur de ton système d’intelligence sémantique. C’est clairement la brique la plus importante de toute ton architecture.

Je vais t’expliquer ça simplement mais correctement, sans jargon inutile.

🧠 Rôle global

👉 Ce fichier fait :

transformer une phrase utilisateur → en action ERP (LISTE_CLIENTS, CREER_FACTURE, etc.)

Mais contrairement à un LLM classique, il ne “génère” pas :

👉 il compare sémantiquement la phrase avec des exemples connus.

🧩 Architecture mentale

Ton système fonctionne comme ça :

QUESTION UTILISATEUR
        ↓
embedding (Ollama)
        ↓
comparaison avec exemples connus
        ↓
centroïdes (action + famille)
        ↓
score hybride (cosine + lexical)
        ↓
action choisie
🧠 Les 3 niveaux de ton classifieur
🥇 1. Niveau famille (gros filtre)
_family_centroids

👉 Chaque famille = groupe d’actions

Ex :

CLIENT
FACTURE
STOCK
ANALYTIQUE
👉 rôle :

réduire l’espace de recherche

🥈 2. Niveau action
_action_centroids

👉 Chaque action a un centroïde (moyenne des embeddings)

Ex :

LISTE_CLIENTS
TOP_CLIENTS
CREER_CLIENT

👉 on compare la question avec ces vecteurs

🥉 3. Niveau exemples (très important)
EXEMPLES_PAR_ACTION

👉 chaque action a des phrases exemples

Ex :

LISTE_CLIENTS:
- liste clients
- affiche clients
- donne clients

👉 on calcule similarité avec tous les exemples

⚙️ Comment une phrase est traitée
🧼 1. Préprocessing
preprocess_text()

👉 normalisation en 2 étapes :

a) normalisation métier
CLI001 → <CLIENT>
FA123 → <FACTURE>
b) normalisation linguistique
clients → client
facturation → facture
🧠 2. Embedding
OllamaEmbeddings

👉 transforme texte → vecteur numérique

Ex :

"liste clients"
→ [0.12, -0.88, 0.33, ...]
📦 3. Cache intelligent
semantic_embeddings_cache.json

👉 évite de recalculer embeddings à chaque démarrage

✔ accélère énormément le système

🧠 4. Centroides
Action centroid
moyenne des embeddings d'une action
Family centroid
moyenne de toutes les actions de la famille

👉 ça sert à filtrer vite avant calcul fin

⚖️ 5. Score hybride (très important)

Dans :

get_weighted_top5_hybrid_score()

Tu combines :

🧠 semantic similarity
cosine similarity
📖 lexical similarity
RapidFuzz (token set ratio)
👉 formule :
score = 0.8 * cosine + 0.2 * lexical
🎯 6. Décision finale

Dans :

classifier_semantique()

Pipeline :

1. famille
best_family = max cosine(query, family_centroid)

👉 si score trop faible → rejet direct

2. actions candidates

👉 seulement actions des 2 meilleures familles

3. score action
centroid_score + top5_score
4. choix final
meilleure_action
meilleur_score
deuxieme_score

👉 permet calcul de marge

📏 7. marge (très important)
marge = score1 - score2

👉 sert dans :

apprentissage_semi_auto
validation humaine
zone grise
🧠 8. apprentissage intégré
fonction clé :
inserer_exemple_valide()

👉 quand un humain valide :

ajoute exemple
met à jour YAML
recalcule centroides
met à jour cache
améliore le modèle
🔁 Donc ton système apprend comment ?

👉 boucle complète :

utilisateur → logs
        ↓
correction humaine
        ↓
inserer_exemple_valide()
        ↓
rebuild embeddings
        ↓
meilleure classification
⚠️ point très important

👉 ce système n’est PAS un LLM

C’est :

🧠 un système hybride :
embeddings (semantic search)
centroid clustering
scoring hybride
règles métier
🧠 comment il décide vraiment

Simplifié :

1. comprendre famille
2. réduire actions possibles
3. scorer chaque action
4. comparer top 2
5. choisir max
🚀 pourquoi c’est puissant

Parce que tu as :

✔ apprentissage continu
✔ correction humaine
✔ cache optimisé
✔ hiérarchie (famille → action → exemple)
✔ score hybride robuste
✔ calibration automatique
💡 résumé simple

👉 semantic_classifier.py =

moteur qui transforme du texte en action ERP en utilisant embeddings + centroides + exemples, avec apprentissage continu via feedback humain
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

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL     = os.getenv("SEMANTIC_EMBED_MODEL", "nomic-embed-text")

_CACHE_PATH = Path(os.getenv(
    "SEMANTIC_CACHE_PATH",
    str(Path(__file__).parent / "semantic_embeddings_cache.json")
))
CONFIG_PATH = Path(os.getenv(
    "SEMANTIC_CONFIG_PATH",
    str(Path(__file__).parent / "semantic_examples.yaml")
))

from langchain_ollama import OllamaEmbeddings

_embedder: OllamaEmbeddings | None = None
_ref_embeddings: dict[str, list[list[float]]] = {}   # action -> [embedding, ...]
_ref_lock: asyncio.Lock | None = None
_warmed_up = False

# Global data loaded from YAML config
_config: dict = {}
EXEMPLES_PAR_ACTION: dict[str, list[str]] = {}
ACTION_TO_FAMILY: dict[str, str] = {}
FAMILY_ACTIONS: dict[str, list[str]] = {}
DEFAULTS: dict = {
    "threshold": 0.90,
    "margin": 0.08,
    "centroid_weight": 0.6,
    "topk_weight": 0.4,
    "family_threshold": 0.60
}

_action_centroids: dict[str, list[float]] = {}
_family_centroids: dict[str, list[float]] = {}
_ref_index: VectorIndex | None = None

# Controlled synonym mapping instead of stemmer
NORMALISATION = {
    "clients": "client",
    "clientèle": "client",
    "clienteles": "client",
    "facturation": "facture",
    "facturé": "facture",
    "facturer": "facture",
    "commande": "commande",
    "commandes": "commande",
    "fournisseurs": "fournisseur",
    "articles": "article",
    "livraison": "livraison",
    "livrer": "livraison",
    "paiement": "reglement",
    "payer": "reglement",
    "règlement": "reglement",
    "régler": "reglement",
    "achats": "achat",
    "acheter": "achat",
    "ventes": "vente",
    "vendre": "vente",
    "stocks": "stock",
    "stocker": "stock",
}
_PREFIXES_POLITESSE = re.compile(
    r"^(est-ce possible de|je voudrais|merci de|peux-tu|j'aimerais|"
    r"pourrais-tu|pouvez-vous|voudriez-vous)\s+",
    re.IGNORECASE,
)

def normaliser_phrase(text: str) -> str:
    """Business normalization: replaces client/doc identifiers with placeholders."""
    text = _PREFIXES_POLITESSE.sub("", text.strip())
    # Normalize Client
    text = re.sub(r'\bCLI\d+\b', '<CLIENT>', text, flags=re.IGNORECASE)
    # Normalize Facture (FA or FF or FC or other invoice type)
    text = re.sub(r'\b(?:FA|FF|FC)\d+\b', '<FACTURE>', text, flags=re.IGNORECASE)
    # Normalize BL
    text = re.sub(r'\bBL\d+\b', '<BL>', text, flags=re.IGNORECASE)
    # Normalize Fournisseur
    text = re.sub(r'\bFOUR\d+\b', '<FOURNISSEUR>', text, flags=re.IGNORECASE)
    # Normalize Article
    text = re.sub(r'\b(?:REF|SKU)\d+\b', '<ARTICLE>', text, flags=re.IGNORECASE)
    return text

def normaliser_linguistique(texte: str) -> str:
    """Applies controlled synonym lookup to keep terms canonical."""
    words = texte.lower().split()
    normalized_words = []
    for w in words:
        w_clean = w.strip(".,;:!?()")
        w_norm = NORMALISATION.get(w_clean, w_clean)
        normalized_words.append(w_norm)
    return " ".join(normalized_words)



def preprocess_text(text: str) -> str:
    """Executes full preprocessing chain."""
    return normaliser_linguistique(normaliser_phrase(text))


class VectorIndex:
    """Numpy-based flat index for vector search."""
    def __init__(self, dimension: int):
        self.dimension = dimension
        self.embeddings = []
        self.metadata = []  # list of (action, original_phrase, preprocessed_phrase)

    def add_vectors(self, vecs: list[list[float]], metadata: list[tuple[str, str, str]]):
        if not vecs:
            return
        vecs_np = np.array(vecs, dtype='float32')
        norms = np.linalg.norm(vecs_np, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs_np = vecs_np / norms
        
        self.embeddings.extend(vecs_np.tolist())
        self.metadata.extend(metadata)

    def search(self, query_vec: list[float], k: int = 10) -> list[tuple[float, str, str, str]]:
        """Returns list of (score, action, original_phrase, preprocessed_phrase)"""
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
    """Parses YAML and populates config global states."""
    global _config, EXEMPLES_PAR_ACTION, ACTION_TO_FAMILY, FAMILY_ACTIONS, DEFAULTS
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing config file {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    
    DEFAULTS.update(_config.get("defaults", {}))
    
    EXEMPLES_PAR_ACTION.clear()
    ACTION_TO_FAMILY.clear()
    FAMILY_ACTIONS.clear()
    
    for family, fam_data in _config.get("families", {}).items():
        FAMILY_ACTIONS[family] = []
        for action, act_data in fam_data.get("actions", {}).items():
            examples = act_data.get("examples", [])
            EXEMPLES_PAR_ACTION[action] = list(examples)
            ACTION_TO_FAMILY[action] = family
            FAMILY_ACTIONS[family].append(action)


# Initial load at import time for back-compatibility
load_config_and_examples()


def _hash_examples_and_config() -> str:
    yaml_content = ""
    if CONFIG_PATH.exists():
        yaml_content = CONFIG_PATH.read_text(encoding="utf-8")
    raw = f"{yaml_content}||{EMBED_MODEL}||768||{OLLAMA_BASE_URL}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_embedder() -> OllamaEmbeddings:
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
    return _embedder


def _cosine(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    normA = math.sqrt(sum(x * x for x in a))
    normB = math.sqrt(sum(y * y for y in b))
    if normA == 0 or normB == 0:
        return 0.0
    return dot / (normA * normB)


def _charger_cache() -> dict | None:
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("hash") == _hash_examples_and_config():
            return data.get("embeddings")
    except Exception:
        pass
    return None


def _sauvegarder_cache(embeddings: dict[str, list[list[float]]]):
    try:
        _CACHE_PATH.write_text(
            json.dumps({"hash": _hash_examples_and_config(), "embeddings": embeddings}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def compute_centroids():
    """Computes all Action and Family Centroids, and registers the index."""
    global _action_centroids, _family_centroids, _ref_index
    _action_centroids.clear()
    _family_centroids.clear()
    
    # 1. Action centroids
    for action, vecs in _ref_embeddings.items():
        if not vecs:
            continue
        arr = np.array(vecs, dtype='float32')
        centroid = np.mean(arr, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        _action_centroids[action] = centroid.tolist()
        
    # 2. Family centroids
    for family, fam_data in _config.get("families", {}).items():
        family_vecs = []
        for action in fam_data.get("actions", {}).keys():
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

    # 3. Build VectorIndex
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
    """Loads cache or queries Ollama to compute embeddings, then builds centroids."""
    global _ref_embeddings, _ref_lock, _warmed_up
    if _ref_lock is None:
        _ref_lock = asyncio.Lock()
    async with _ref_lock:
        if _warmed_up:
            return
        
        # Always reload config in case it was updated
        load_config_and_examples()
        
        cached = _charger_cache()
        if cached:
            _ref_embeddings = cached
            _warmed_up = True
            compute_centroids()
            nb = sum(len(v) for v in cached.values())
            print(f"   ✅ [Sémantique] {nb} exemples chargés depuis {_CACHE_PATH} — centroïdes calculés.")
            return
        print(f"   ℹ️  [Sémantique] Aucun cache valide trouvé à {_CACHE_PATH} → recalcul complet.")

        print("   ⏳ [Sémantique] Calcul des embeddings de référence...")
        embedder = _get_embedder()
        resultat: dict[str, list[list[float]]] = {}
        for action, phrases in EXEMPLES_PAR_ACTION.items():
            try:
                # Preprocess examples before embedding
                prepped_phrases = [preprocess_text(p) for p in phrases]
                vecs = await embedder.aembed_documents(prepped_phrases)
                resultat[action] = vecs
            except Exception as e:
                print(f"   ⚠️  [Sémantique] Échec embedding '{action}' : {e}")
        _ref_embeddings = resultat
        _warmed_up = True
        compute_centroids()
        _sauvegarder_cache(resultat)
        nb = sum(len(v) for v in resultat.values())
        print(f"   ✅ [Sémantique] {nb} exemples encodés et mis en cache.")


def get_lexical_score(s1: str, s2: str) -> float:
    """Calculates Token Set Ratio using RapidFuzz, normalized to 0-1."""
    if rapidfuzz is not None:
        return float(rapidfuzz.fuzz.token_set_ratio(s1, s2) / 100.0)
    else:
        w1 = set(s1.split())
        w2 = set(s2.split())
        if not w1 or not w2:
            return 0.0
        return len(w1.intersection(w2)) / len(w1.union(w2))


def get_action_config(action: str) -> dict:
    """Returns the action configurations (threshold/margin) or defaults."""
    for family, fam_data in _config.get("families", {}).items():
        if action in fam_data.get("actions", {}):
            return fam_data["actions"][action]
    return {}


def get_family_threshold(family: str) -> float:
    """Returns the family-specific threshold if calibrated (see
    calibrate_thresholds.py --calibrate-families), otherwise the global
    default. Mirrors get_action_config() but for the family pre-filter."""
    fam_data = _config.get("families", {}).get(family, {})
    return fam_data.get("family_threshold", DEFAULTS.get("family_threshold", 0.60))


def calculer_similarite_maximale(phrase: str, action: str) -> float:
    """Calculates the maximum hybrid similarity between phrase and all examples of action."""
    if action not in EXEMPLES_PAR_ACTION or not EXEMPLES_PAR_ACTION[action]:
        return 0.0
    
    try:
        embedder = _get_embedder()
        prep_phrase = preprocess_text(phrase)
        vec_q = embedder.embed_query(prep_phrase)
    except Exception as e:
        print(f"   ⚠️  [Sémantique] Embedding synchrone échoué : {e}")
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
    """Computes the weighted sum of top-5 hybrid scores for an action's examples."""
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
    
    weights = [0.40, 0.25, 0.15, 0.10, 0.10]
    if len(top_k) < 5:
        w_sub = weights[:len(top_k)]
        sum_w = sum(w_sub)
        if sum_w > 0:
            w_sub = [w / sum_w for w in w_sub]
        else:
            w_sub = [1.0 / len(top_k)] * len(top_k)
        return sum(s * w for s, w in zip(top_k, w_sub))
    else:
        return sum(s * w for s, w in zip(top_k, weights))


async def classifier_semantique(question: str) -> tuple[str | None, float, float]:
    """
    Classifies a query using Family Centroids pre-filtering, then Action Centroids
    and weighted Top-5 hybrid example matching.
    """
    if not _warmed_up:
        await warmup_semantic_classifier()
    if not _ref_embeddings:
        return None, 0.0, 0.0

    try:
        embedder = _get_embedder()
        query_prep = preprocess_text(question)
        query_emb = await embedder.aembed_query(query_prep)
    except Exception as e:
        print(f"   ⚠️  [Sémantique] Embedding question échoué : {e}")
        return None, 0.0, 0.0

    # 1. Family Centroids Classification
    family_scores = {}
    for family, centroid in _family_centroids.items():
        family_scores[family] = _cosine(query_emb, centroid)
        
    if not family_scores:
        return None, 0.0, 0.0
        
    # Get the best family score
    best_family = max(family_scores, key=family_scores.get)
    best_fam_score = family_scores[best_family]

    # Seuil par famille (calibré individuellement si disponible, sinon
    # valeur par défaut globale) — amélioration #2.
    family_threshold = get_family_threshold(best_family)
    if best_fam_score < family_threshold:
        print(f"   🔎 [Sémantique] Famille peu confiante '{best_family}' score={best_fam_score:.3f} < {family_threshold}")
        return None, 0.0, 0.0
        
    # Select Top 2 families to restrict action classification search space
    sorted_families = sorted(family_scores.items(), key=lambda x: x[1], reverse=True)
    top_2_families = [x[0] for x in sorted_families[:2]]
    
    # 2. Action Classification within top 2 families
    actions_to_check = []
    for fam in top_2_families:
        actions_to_check.extend(FAMILY_ACTIONS.get(fam, []))
        
    centroid_weight = DEFAULTS.get("centroid_weight", 0.6)
    topk_weight = DEFAULTS.get("topk_weight", 0.4)
    
    action_scores = {}
    for action in actions_to_check:
        if action not in _action_centroids or action not in _ref_embeddings:
            continue
        # Centroid Cosine Similarity
        cos_centroid = _cosine(query_emb, _action_centroids[action])
        # Top-5 Weighted Hybrid Similarity
        weighted_top5 = get_weighted_top5_hybrid_score(query_emb, query_prep, action)
        # Combined Score
        combined_score = centroid_weight * cos_centroid + topk_weight * weighted_top5
        action_scores[action] = combined_score
        
    if not action_scores:
        return None, 0.0, 0.0
        
    # Sort action scores to get top 1 and top 2 for margin computation
    sorted_actions = sorted(action_scores.items(), key=lambda x: x[1], reverse=True)
    meilleure_action = sorted_actions[0][0]
    meilleur_score = sorted_actions[0][1]
    
    deuxieme_score = 0.0
    if len(sorted_actions) > 1:
        deuxieme_score = sorted_actions[1][1]
    sorted_actions = sorted(action_scores.items(), key=lambda x: x[1], reverse=True)   
    return meilleure_action, meilleur_score, deuxieme_score


async def inserer_exemple_valide(action: str, phrase: str) -> bool:
    """
    Insertion en mémoire ET dans le YAML d'un exemple validé manuellement.
    Re-calcule automatiquement les centroïdes de sa famille et met à jour le cache.
    """
    if action not in EXEMPLES_PAR_ACTION:
        print(f"   ⚠️  [Sémantique] Action inconnue '{action}'.")
        return False
    if phrase.strip().lower() in {e.lower() for e in EXEMPLES_PAR_ACTION[action]}:
        return False  # déjà présent

    try:
        embedder = _get_embedder()
        prep_phrase = preprocess_text(phrase)
        vec = await embedder.aembed_query(prep_phrase)
    except Exception as e:
        print(f"   ⚠️  [Sémantique] Embedding échoué pour '{phrase}' : {e}")
        return False

    # 1. Update in-memory
    EXEMPLES_PAR_ACTION[action].append(phrase)
    _ref_embeddings.setdefault(action, []).append(vec)
    
    # 2. Update YAML configuration file
    updated_yaml = False
    for family, fam_data in _config.get("families", {}).items():
        if action in fam_data.get("actions", {}):
            fam_data["actions"][action].setdefault("examples", []).append(phrase)
            updated_yaml = True
            break
            
    if updated_yaml:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print(f"   📥 [Sémantique] Exemple sauvegardé dans le YAML : '{phrase}' → {action}")
        except Exception as e:
            print(f"   ⚠️  [Sémantique] Échec écriture YAML : {e}")

    # 3. Recalculate centroids & rebuild index
    compute_centroids()
    
    # 4. Save Cache
    _sauvegarder_cache(_ref_embeddings)
    return True