"""
Modifie la configuration de sortie pour forcer l'utilisation de l'encodage UTF-8.
"""
from datetime import datetime
import asyncio
import re
import json
from decimal import Decimal
_json_dumps_orig = json.dumps

def _json_dumps_safe(obj, **kwargs):
    """
Convertit une instance d'objets en chaîne de caractères JSON, en convertissant les objets Decimal en nombres flottants.
"""
    kwargs.setdefault('default', lambda o: float(o) if isinstance(o, Decimal) else str(o))
    return _json_dumps_orig(obj, **kwargs)
json.dumps = _json_dumps_safe
import os
import traceback as tb
import time
import sys
import io
import warnings
import shelve
import hashlib
import itertools
from pathlib import Path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import formatting.formatters
from graph.draft_flow import SCHEMAS_DOCUMENTS, est_confirmation_stricte, est_annulation_stricte, champs_manquants as df_champs_manquants, question_pour_champ, injecter_reponse_dans_draft, construire_draft_depuis_state, generer_preview, executer_draft_confirme, generer_pdf_final, ajouter_alerte_bf_requis, resoudre_alerte_bf, formater_alertes_persistantes
from formatting.formatters import _FORMATEURS_JSON, _formater_reponse_directe, _formater_nl2sql_brut
from extraction.extraction import _ner_extraire_entites, _est_nom_valide, _nettoyer_nom_client, _extraire_code_ou_nom_depuis_texte, _corriger_ref_article as _corriger_ref_article_impl, _rechercher_client_par_nom as _rechercher_client_par_nom_impl
from graph.offre_prix_flow import extraire_articles_depuis_demande, initialiser_draft_offre, traiter_reponse_prix, traiter_reponse_remise, generer_pdf_offre_prix, formater_suggestion_prix
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['HF_HUB_DISABLE_EXPERIMENTAL_WARNING'] = '1'
os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
os.environ['TRANSFORMERS_VERBOSITY'] = 'error'

def _fix_encoding():
    """
Définit l'encodage des flux d'entrée/sortie système.
"""
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
            sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
        else:
            raise AttributeError('no reconfigure')
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
_fix_encoding()
from dotenv import load_dotenv
load_dotenv()
import sys
try:
    from database.init_db_complet import init_database_mssql
    init_database_mssql()
except Exception as e:
    print(f'⚠️  [DB] Erreur initialisation MSSQL : {e}', file=sys.stderr)
from typing import TypedDict, Optional
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from api.mcp_pool import pool as mcp_pool
import classification.semantic_classifier as sc
import classification.classification_engine as ce
from cache.response_cache import cache as response_cache
from apprentissage.interaction_logger import logger_decision, detecter_correction
from apprentissage.apprentissage_semi_auto import enregistrer_signal_confirmation, enregistrer_signal_correction
from api.llm_anonymizer import invoke_llm_anonymise, anonymise_sync, deanonymise_with_map
from api.graph_nodes import noeud_planner as _noeud_planner, noeud_hors_sujet as _noeud_hors_sujet, noeud_aide as _noeud_aide, noeud_clarification as _noeud_clarification, noeud_confirmation as _noeud_confirmation, noeud_lecture as _noeud_lecture, noeud_nl2sql_libre as _noeud_nl2sql_libre, noeud_ecriture as _noeud_ecriture, noeud_workflow as _noeud_workflow, noeud_synthese as _noeud_synthese, _executer_suggestion as __executer_suggestion, noeud_kb as _noeud_kb, noeud_modification as _noeud_modification, noeud_modification_confirmation as _noeud_modification_confirmation
from api.graph_nodes.creation_article import noeud_creation_article as _noeud_creation_article
from api.graph_nodes.nomenclature import noeud_nomenclature as _noeud_nomenclature
from api.graph_nodes.modification_nomenclature import noeud_modification_nomenclature as _noeud_modification_nomenclature
FALLBACK_URL = os.getenv('LLM_FALLBACK_URL', 'https://api.groq.com/openai/v1')
FALLBACK_KEY = (os.getenv('LLM_FALLBACK_KEY', '') or '').strip()
FALLBACK_MODEL = os.getenv('LLM_FALLBACK_MODEL', 'openai/gpt-oss-120b')
GROQ_URL = os.getenv('GROQ_URL', FALLBACK_URL)
GROQ_KEY = (os.getenv('GROQ_KEY', '') or FALLBACK_KEY).strip()
MODELE_FAST = os.getenv('GROQ_FAST', 'openai/gpt-oss-20b')
MODELE_SMART = os.getenv('GROQ_SMART', 'openai/gpt-oss-120b')
OLLAMA_TIMEOUT_FAST = float(os.getenv('OLLAMA_TIMEOUT_FAST', '120'))
OLLAMA_TIMEOUT_SMART = float(os.getenv('OLLAMA_TIMEOUT_SMART', '300'))
OLLAMA_WARMUP_TIMEOUT = float(os.getenv('OLLAMA_WARMUP_TIMEOUT', '300'))
if os.getenv('OLLAMA_TIMEOUT'):
    _t = float(os.getenv('OLLAMA_TIMEOUT'))
    OLLAMA_TIMEOUT_FAST = _t
    OLLAMA_TIMEOUT_SMART = max(_t, 60.0)
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
SEUIL_CONFIANCE = float(os.getenv('SEUIL_CONFIANCE', '0.60'))
ENABLE_HALLUCINATION_CHECK = os.getenv('ENABLE_HALLUCINATION_CHECK', 'false').lower() == 'true'
ENABLE_VANNA = os.getenv('ENABLE_VANNA', 'false').lower() == 'true'
ENABLE_MEM0 = os.getenv('ENABLE_MEM0', 'false').lower() == 'true'
ENABLE_GLINER = os.getenv('ENABLE_GLINER', 'false').lower() == 'true'
ENABLE_SEMANTIC_CLASSIFIER = os.getenv('ENABLE_SEMANTIC_CLASSIFIER', 'true').lower() == 'true'
ENABLE_STRUCTURED_EXTRACTION = os.getenv('ENABLE_STRUCTURED_EXTRACTION', 'true').lower() == 'true'
PLANNER_TIMEOUT = float(os.getenv('PLANNER_TIMEOUT', '60'))
ENABLE_LLM_SYNTHESE = os.getenv('ENABLE_LLM_SYNTHESE', 'true').lower() == 'true'
SYNTHESE_TIMEOUT = max(120.0, float(os.getenv('SYNTHESE_TIMEOUT', '120')))
VANNA_GENERATE_TIMEOUT = float(os.getenv('VANNA_GENERATE_TIMEOUT', '300'))
_LLM_MAX_CONCURRENT = int(os.getenv('LLM_MAX_CONCURRENT', '2'))
_llm_semaphore: asyncio.Semaphore | None = None
_DISK_CACHE_PATH = os.getenv('DISK_CACHE_PATH', './disk_cache_sage')
_DISK_CACHE_TTL = float(os.getenv('DISK_CACHE_TTL', '600'))
_disk_cache_lock = None
llm_fast = ChatOpenAI(model=MODELE_FAST, temperature=0, api_key=GROQ_KEY, base_url=GROQ_URL)
llm_smart = ChatOpenAI(model=MODELE_SMART, temperature=0, api_key=GROQ_KEY, base_url=GROQ_URL)
_ollama_warmed_up: dict[str, bool] = {'fast': False, 'smart': False}

def _safe_str(obj) -> str:
    """
Convertir un objet en une chaîne de caractères en utilisant le codage UTF-8 et remplaçant les caractères non reconnus.
"""
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    return str(obj).encode('utf-8', errors='replace').decode('utf-8')

async def _input(prompt: str) -> str:
    """
Cette fonction asynchrone permet de récupérer une entrée utilisateur sans bloquer le thread principal en utilisant asyncio.
"""
    return await asyncio.to_thread(input, prompt)
_MOTS_OUI = {'o', 'ok', 'oui', 'yes', 'y', 'ouais', 'bien sûr', 'vas-y', 'vas y', 'go', 'allez', 'parfait', 'super', "d'accord", 'daccord', 'volontiers', 'affirmatif', 'faites', 'faites-le', 'lance', 'crée', 'créer', 'génère', 'genere', 'fais-le', 'fais le', 'confirmer', 'valider', 'valide', 'confirm'}
_MOTS_NON = {'n', 'non', 'no', 'nope', 'pas', 'annuler', 'annule', 'stop', 'arrête', 'arrete', 'laisse tomber', 'laisse', 'pas maintenant', 'plus tard', 'skip', 'ignore'}

def _est_oui(texte: str) -> bool:
    """
Fonction qui vérifie si un texte est équivalent à un mot oui dans une liste définie, en ignorant les caractères non alphanumériques et en convertissant en minuscule.
"""
    t = ''.join((c for c in texte if c.isalnum() or c.isspace() or c == "'")).lower().strip()
    return t in _MOTS_OUI

def _est_non(texte: str) -> bool:
    """
Fonction qui vérifie si un texte contient des mots non souhaités.
"""
    t = ''.join((c for c in texte if c.isalnum() or c.isspace() or c == "'")).lower().strip()
    return t in _MOTS_NON or any((m in t for m in _MOTS_NON))

def _disk_cache_key(action: str, **kwargs) -> str:
    """
Fonction pour générer une clé de cache utilisant une hache MD5.
"""
    raw = action + ':' + json.dumps(kwargs, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()

async def _disk_cache_get(action: str, **kwargs) -> str | None:
    """
Récupère la valeur d'une entrée de cache stockée sur le disque en fonction de l'action et des arguments fournis.
"""
    global _disk_cache_lock
    if _disk_cache_lock is None:
        _disk_cache_lock = asyncio.Lock()
    key = _disk_cache_key(action, **kwargs)
    try:
        async with _disk_cache_lock:

            def _read():
                """
Fonction pour lire une entrée du cache disque, en vérifiant la date de mise à jour.
"""
                with shelve.open(_DISK_CACHE_PATH) as db:
                    entry = db.get(key)
                    if entry is None:
                        return None
                    value, ts = entry
                    if time.monotonic() - ts > _DISK_CACHE_TTL:
                        del db[key]
                        return None
                    return value
            return await asyncio.to_thread(_read)
    except Exception:
        return None

async def _disk_cache_set(action: str, value: str, **kwargs):
    """
Fonction permettant de mettre à jour la cache de disque en synchronisant les écritures pour éviter les conflits.
"""
    global _disk_cache_lock
    if _disk_cache_lock is None:
        _disk_cache_lock = asyncio.Lock()
    key = _disk_cache_key(action, **kwargs)
    try:
        async with _disk_cache_lock:

            def _write():
                """
Enregistre une clé et une valeur associée avec la date de stockage actuelle dans un fichier de cache sur le disque.
"""
                with shelve.open(_DISK_CACHE_PATH) as db:
                    db[key] = (value, time.monotonic())
            await asyncio.to_thread(_write)
    except Exception:
        pass

async def _warmup_ollama():
    """
Lancement du préchauffage des modèles LLama en mémoire.
"""
    print('🔥 [Ollama] Préchauffage des modèles en mémoire...')

    async def _warm_one(key: str, llm_instance: ChatOllama, nom: str):
        """
async définit et met en place un langage de modèle (LLM) pour une instance Ollama.
"""
        print(f'   ⏳ [{nom}] Chargement en cours...')
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(llm_instance.ainvoke('ok'), timeout=OLLAMA_WARMUP_TIMEOUT)
            _ollama_warmed_up[key] = True
            print(f'   ✅ [{nom}] Prêt en {time.perf_counter() - t0:.1f}s')
        except asyncio.TimeoutError:
            print(f'   ⚠️  [{nom}] Warmup timeout après {time.perf_counter() - t0:.1f}s')
        except Exception as e:
            err = _safe_str(e)
            if any((k in err.lower() for k in ('refused', 'connect', 'unreachable'))):
                print(f'   ❌ [{nom}] Ollama inaccessible → lancez : ollama serve')
            else:
                print(f'   ⚠️  [{nom}] Warmup échoué : {err}')
    await asyncio.gather(_warm_one('fast', llm_fast, MODELE_FAST), _warm_one('smart', llm_smart, MODELE_SMART))
    nb_ok = sum(_ollama_warmed_up.values())
    if nb_ok == 2:
        print(f'\n🔥 [Ollama] ✅ Les 2 modèles sont prêts.\n')
    elif nb_ok == 1:
        ok = MODELE_FAST if _ollama_warmed_up['fast'] else MODELE_SMART
        print(f'\n🔥 [Ollama] ⚠️  1/2 modèle prêt ({ok}).\n')
    else:
        print(f'\n🔥 [Ollama] ❌ Aucun modèle chargé. Vérifiez : ollama serve\n')

async def _invoke_llm(prompt: str, use_smart: bool=False, timeout_override: float | None=None) -> str:
    """
Asynchronement de l'invocation d'un modèle LLM.
"""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(_LLM_MAX_CONCURRENT)
    model = llm_smart if use_smart else llm_fast
    key = 'smart' if use_smart else 'fast'
    nom = MODELE_SMART if use_smart else MODELE_FAST
    timeout = timeout_override or (OLLAMA_TIMEOUT_SMART if use_smart else OLLAMA_TIMEOUT_FAST)
    prompt_u = prompt.encode('utf-8', errors='replace').decode('utf-8')
    async with _llm_semaphore:
        try:
            r = await asyncio.wait_for(model.ainvoke(prompt_u), timeout=timeout)
            _ollama_warmed_up[key] = True
            content = r.content
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            return content.strip()
        except asyncio.TimeoutError:
            print(f'   ⚠️  [{nom}] Timeout ({timeout}s).')
            if FALLBACK_KEY:
                return await _invoke_fallback(prompt_u)
            return f'⚠️  Le modèle [{nom}] ne répond pas (timeout {timeout}s). Veuillez réessayer ou configurer LLM_FALLBACK_KEY.'
        except Exception as e:
            err = _safe_str(e)
            if any((k in err.lower() for k in ('refused', 'connect', 'unreachable'))):
                print(f'   ❌ [{nom}] Ollama inaccessible.')
                if FALLBACK_KEY:
                    return await _invoke_fallback(prompt_u)
                return '❌ Ollama inaccessible. Lancez `ollama serve`.'
            if FALLBACK_KEY:
                return await _invoke_fallback(prompt_u)
            raise

async def _invoke_fallback(prompt_utf8: str) -> str:
    """
Renvoi le résultat de l'invite de l'API IA de fallback, ou un message d'erreur si l'invite échoue.
"""
    print(f'   🔄 Fallback → {FALLBACK_MODEL}')
    try:
        from langchain_openai import ChatOpenAI
        fb = ChatOpenAI(model=FALLBACK_MODEL, temperature=0, api_key=FALLBACK_KEY, base_url=FALLBACK_URL)
        r = await fb.ainvoke(prompt_utf8)
        content = r.content
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        return content.strip()
    except Exception as e2:
        return f'⚠️  Service IA temporairement indisponible. ({_safe_str(e2)})'
_gliner_model: object | None = None
_gliner_load_tried: bool = False
_gliner_lock: asyncio.Lock | None = None

def _get_gliner_sync() -> object | None:
    """
Récupérer le modèle GLiNER s'il est chargé, sinon essayer de le charger.
"""
    global _gliner_model, _gliner_load_tried
    if not ENABLE_GLINER:
        return None
    if _gliner_load_tried:
        return _gliner_model
    _gliner_load_tried = True
    try:
        from gliner import GLiNER
        print('   ⏳ [GLiNER] Chargement du modèle...')
        t0 = time.perf_counter()
        _gliner_model = GLiNER.from_pretrained('urchade/gliner_multi-v2.1', load_tokenizer=True)
        print(f'   ✅ [GLiNER] Prêt en {time.perf_counter() - t0:.1f}s')
    except ImportError:
        print('   ⚠️  [GLiNER] pip install gliner')
        _gliner_model = None
    except Exception as e:
        print(f'   ⚠️  [GLiNER] {_safe_str(e)}')
        _gliner_model = None
    return _gliner_model

async def _get_gliner_async() -> object | None:
    """
Récupère un objet Gliner synchronisé pour éviter des problèmes de threading.
"""
    global _gliner_lock, _gliner_load_tried
    if not ENABLE_GLINER:
        return None
    if _gliner_load_tried:
        return _gliner_model
    if _gliner_lock is None:
        _gliner_lock = asyncio.Lock()
    async with _gliner_lock:
        if _gliner_load_tried:
            return _gliner_model
        return await asyncio.to_thread(_get_gliner_sync)
_mem0_client: object | None = None
_mem0_load_tried: bool = False
_mem0_lock: asyncio.Lock | None = None
MEM0_USER_ID = os.getenv('MEM0_USER_ID', 'erp_copilot_user')
MEM0_EMBED_MODEL = os.getenv('MEM0_EMBED_MODEL', 'nomic-embed-text')
MEM0_EMBED_DIMS = int(os.getenv('MEM0_EMBED_DIMS', '768'))
MEM0_DB_PATH = os.getenv('MEM0_DB_PATH', './mem0_qdrant_db')
MEM0_LLM_PROVIDER = os.getenv('MEM0_LLM_PROVIDER', '')
MEM0_LLM_MODEL = os.getenv('MEM0_LLM_MODEL', 'llama3.2:3b')

def _get_mem0_sync() -> object | None:
    """
Rôle de la fonction : Récupérer la client Mem0 synchronisée.
"""
    global _mem0_client, _mem0_load_tried
    if not ENABLE_MEM0:
        return None
    if _mem0_load_tried:
        return _mem0_client
    _mem0_load_tried = True
    try:
        import logging
        logging.getLogger('mem0').setLevel(logging.ERROR)
        logging.getLogger('httpx').setLevel(logging.ERROR)
        logging.getLogger('qdrant_client').setLevel(logging.ERROR)
        from mem0 import Memory
        print('   ⏳ [Mem0] Initialisation...')
        t0 = time.perf_counter()
        if MEM0_LLM_PROVIDER.lower() == 'ollama':
            llm_config = {'provider': 'ollama', 'config': {'model': MEM0_LLM_MODEL, 'temperature': 0, 'ollama_base_url': OLLAMA_BASE_URL}}
            print(f'   [Mem0] LLM → Ollama ({MEM0_LLM_MODEL})')
        else:
            llm_config = {'provider': 'openai', 'config': {'model': MODELE_FAST, 'temperature': 0, 'api_key': GROQ_KEY, 'openai_base_url': GROQ_URL}}
            print(f'   [Mem0] LLM → Groq ({MODELE_FAST})')
        _mem0_client = Memory.from_config({'vector_store': {'provider': 'qdrant', 'config': {'collection_name': 'erp_copilot_memories', 'path': MEM0_DB_PATH, 'embedding_model_dims': MEM0_EMBED_DIMS}}, 'llm': llm_config, 'embedder': {'provider': 'ollama', 'config': {'model': MEM0_EMBED_MODEL, 'ollama_base_url': OLLAMA_BASE_URL}}})
        print(f'   ✅ [Mem0] Prêt en {time.perf_counter() - t0:.1f}s')
    except ImportError:
        print('   ⚠️  [Mem0] pip install mem0ai')
        _mem0_client = None
    except Exception as e:
        print(f'   ⚠️  [Mem0] {_safe_str(e)}')
        _mem0_client = None
    return _mem0_client

async def _get_mem0_async() -> object | None:
    """
Fonction asynchrone chargement de données de mémoire n°0.
"""
    global _mem0_lock, _mem0_load_tried
    if not ENABLE_MEM0:
        return None
    if _mem0_load_tried:
        return _mem0_client
    if _mem0_lock is None:
        _mem0_lock = asyncio.Lock()
    async with _mem0_lock:
        if _mem0_load_tried:
            return _mem0_client
        return await asyncio.to_thread(_get_mem0_sync)

def _mem0_rechercher_sync(requete: str) -> str:
    """
Rechercher les informations synchronisées dans la mémoire commune pour une requête donnée.
"""
    mem = _get_mem0_sync()
    if mem is None:
        return ''
    try:
        results = mem.search(requete, filters={'user_id': MEM0_USER_ID}, limit=3)
        items = results.get('results', results) if isinstance(results, dict) else results
        return '\n'.join((r['memory'] for r in items if isinstance(r, dict) and 'memory' in r))
    except Exception:
        return ''

async def _mem0_rechercher(requete: str) -> str:
    """
Développer en parallèle une fonction asynchrone et synchronne pour la recherche dans la mémoire.
"""
    return await asyncio.to_thread(_mem0_rechercher_sync, requete)

def _mem0_sauvegarder(message: str, reponse: str):
    """
Sauvegarde un message de conversation dans la mémoire de synchronisation.
"""
    mem = _get_mem0_sync()
    if mem is None:
        return
    try:
        mem.add([{'role': 'user', 'content': message}, {'role': 'assistant', 'content': reponse}], user_id=MEM0_USER_ID)
    except Exception:
        pass
_vanna_client = None
_vanna_load_tried = False
_vanna_lock: asyncio.Lock | None = None
_vanna_retrain_lock: asyncio.Lock | None = None

def _get_vanna_sync():
    """
Cette fonction, `_get_vanna_sync`, est responsable de la configuration et de la connexion à l'API Vanna si elle est activée dans les paramètres du programme.
"""
    global _vanna_client, _vanna_load_tried
    if not ENABLE_VANNA:
        return None
    if _vanna_load_tried:
        return _vanna_client
    _vanna_load_tried = True
    from api.mcp_actions_sage import _is_mssql
    try:
        from adaptation.db_adapter import table as _ta, col as _co
        if _ta('doc_entete') != 'F_DOCENTETE' or _co('doc_entete', 'piece') != 'DO_Piece':
            print('⚠️  [Vanna] db_config.json ne correspond pas au schéma Sage100 par défaut sur lequel Vanna est entraîné → Vanna désactivé pour cette base.')
            print("      (Pour utiliser Vanna avec un schéma personnalisé, implémentez l'Option B)")
            _vanna_client = None
            return None
    except Exception as _e_schema:
        print(f'⚠️  [Vanna] Impossible de vérifier le schéma : {_e_schema} → Vanna désactivé par précaution.')
        _vanna_client = None
        return None
    import os
    os.environ['ANONYMIZED_TELEMETRY'] = 'False'
    import logging
    logging.getLogger('chromadb').setLevel(logging.ERROR)
    logging.getLogger('chromadb.segment').setLevel(logging.ERROR)
    try:
        import posthog
        posthog.capture = lambda *args, **kwargs: None
    except ImportError:
        pass
    try:
        from vanna.openai import OpenAI_Chat
        from vanna.chromadb import ChromaDB_VectorStore
        from openai import OpenAI as _OpenAIClient

        class VannaERP(ChromaDB_VectorStore, OpenAI_Chat):
            """
Classe de gestion d'un serveur ERP et chat intégré basé sur la plateforme Vanna, héritant des fonctionnalités de stockage vectoriel et de chat basé sur OpenAI.
"""

            def __init__(self, config=None, client=None):
                """
Classe qui gère la connexion à un vecteur de stockage de données ChromaDB et à OpenAI pour une communication avec un chat.
"""
                ChromaDB_VectorStore.__init__(self, config=config)
                OpenAI_Chat.__init__(self, client=client, config=config)
        _groq_client = _OpenAIClient(api_key=GROQ_KEY, base_url=GROQ_URL)
        vanna_config = {'model': MODELE_FAST, 'path': './vanna_erp_db'}
        try:
            from chromadb.utils import embedding_functions
            import os
            _hf_cache = os.path.join(os.environ.get('HF_HOME', os.path.join(os.path.expanduser('~'), '.cache', 'huggingface')), 'hub', 'models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2')
            if os.path.isdir(_hf_cache):
                os.environ.setdefault('HF_HUB_OFFLINE', '1')
                vanna_config['embedding_function'] = embedding_functions.SentenceTransformerEmbeddingFunction(model_name='paraphrase-multilingual-MiniLM-L12-v2')
                print('   ℹ️  [Vanna] Embedding multilingue (cache local) configuré')
            else:
                print('   ℹ️  [Vanna] Embedding ChromaDB par défaut (aucun téléchargement requis)')
        except Exception as e:
            print(f'   ⚠️  [Vanna] Embedding multilingue non disponible: {_safe_str(e)}')
        vn = VannaERP(config=vanna_config, client=_groq_client)
        try:
            existing = vn.get_training_data()
            nb_existing = len(existing) if existing is not None else 0
            print(f'   ℹ️  [Vanna] get_training_data() → {nb_existing} lignes, type={type(existing)}')
        except Exception as e:
            print(f'   ⚠️  [Vanna] get_training_data() a échoué : {_safe_str(e)}')
            nb_existing = 0
        from vanna_training_neutral import construire_exemples_entrainement, calculer_hash_entrainement, doit_reentrainer, marquer_entrainement_fait
        from adaptation.db_adapter import table as _t_hash, col as _c_hash
        _exemples_hash = construire_exemples_entrainement(_is_mssql(), table=_t_hash, col=_c_hash)
        _doc_str_hash = ''
        hash_actuel = calculer_hash_entrainement([] if nb_existing > 0 else [], _doc_str_hash, _exemples_hash)
        if nb_existing > 0 and doit_reentrainer(vn, hash_actuel):
            try:
                existing_data = vn.get_training_data()
                if existing_data is not None and (not existing_data.empty):
                    for _id in existing_data['id'].tolist():
                        vn.remove_training_data(_id)
                    print(f'   🗑️  [Vanna] {len(existing_data)} entrées purgées avant ré-entraînement.')
            except Exception as e:
                print(f'⚠️  [Vanna] Purge avant ré-entraînement échouée : {e}')
            _vanna_entrainer_schema(vn)
            marquer_entrainement_fait(hash_actuel)
            print('✅ [Vanna] Ré-entraîné sur le nouveau schéma/exemples.')
        elif nb_existing == 0:
            _vanna_entrainer_schema(vn)
            marquer_entrainement_fait(hash_actuel)
            print('✅ [Vanna] Initialisé (Groq) et entraîné sur le schéma Sage 100.')
        else:
            print(f'   ℹ️  [Vanna] {nb_existing} exemples déjà en base, contenu inchangé → entraînement ignoré')
            print("      (tapez 'vanna_retrain' pour forcer un ré-entraînement propre)")
        _vanna_client = vn
    except ImportError:
        print('⚠️  [Vanna] pip install vanna chromadb')
        _vanna_client = None
    except Exception as e:
        print(f'⚠️  [Vanna] {_safe_str(e)}')
        _vanna_client = None
    return _vanna_client

async def _get_vanna_async():
    """
Fonction d'asynchrone chargement de la client Vanna.
"""
    global _vanna_lock, _vanna_load_tried
    if not ENABLE_VANNA:
        return None
    if _vanna_load_tried:
        return _vanna_client
    if _vanna_lock is None:
        _vanna_lock = asyncio.Lock()
    async with _vanna_lock:
        if _vanna_load_tried:
            return _vanna_client
        print('   ⏳ [Vanna] Chargement ChromaDB + modèle...')
        t0 = time.perf_counter()
        result = await asyncio.to_thread(_get_vanna_sync)
        if result:
            print(f'   ✅ [Vanna] Prêt en {time.perf_counter() - t0:.1f}s')
        return result
_vanna_train_count = 0

def _vanna_entrainer_schema(vn, *args, **kwargs):
    """
Générer le schéma de la base de données Vanna en fonction de la configuration actuelle, en créant les tables nécessaires.
"""
    global _vanna_client, _vanna_train_count
    if isinstance(vn, str):
        return
    _vanna_train_count += 1
    if _vanna_train_count > 1:
        print(f'   ⚠️ [Vanna] _vanna_entrainer_schema ignoré (appel dupliqué #{_vanna_train_count})')
        import traceback
        traceback.print_stack()
        return
    from api.mcp_actions_sage import _is_mssql, _get_conn
    from adaptation.db_adapter import table as _t, col as _c
    if _is_mssql():
        from generer_ddl_vanna import generer_ddl_tables, TABLES_UTILISEES
        conn = _get_conn()
        try:
            tables_ddl = generer_ddl_tables(conn, TABLES_UTILISEES)
        finally:
            conn.close()
    else:
        tables_ddl = [f"CREATE TABLE {_t('clients_fournisseurs')} (\n                {_c('clients_fournisseurs', 'code')}      TEXT PRIMARY KEY,\n                {_c('clients_fournisseurs', 'nom')}       TEXT,\n                {_c('clients_fournisseurs', 'type_tiers')} INTEGER,\n                {_c('clients_fournisseurs', 'sommeil')}   TEXT,\n                {_c('clients_fournisseurs', 'encours')}   REAL DEFAULT 0.0\n            )", f"CREATE TABLE {_t('articles')} (\n                {_c('articles', 'ref')}         TEXT PRIMARY KEY,\n                {_c('articles', 'designation')} TEXT,\n                {_c('articles', 'prix_achat')}  REAL,\n                {_c('articles', 'prix_vente')}  REAL,\n                {_c('articles', 'type_article')} INTEGER\n            )", f"CREATE TABLE {_t('stock')} (\n                {_c('stock', 'ref')}           TEXT PRIMARY KEY,\n                {_c('stock', 'qte_stock')}     REAL,\n                {_c('stock', 'qte_commande')}  REAL\n            )", f"CREATE TABLE {_t('nomenclature')} (\n                {_c('nomenclature', 'ref_pf')} TEXT,\n                {_c('nomenclature', 'ref_mp')} TEXT,\n                {_c('nomenclature', 'qte')}    REAL,\n                PRIMARY KEY ({_c('nomenclature', 'ref_pf')}, {_c('nomenclature', 'ref_mp')})\n            )", f"CREATE TABLE {_t('doc_entete')} (\n                {_c('doc_entete', 'piece')}      TEXT PRIMARY KEY,\n                {_c('doc_entete', 'domaine')}    INTEGER,\n                {_c('doc_entete', 'type')}       INTEGER,\n                {_c('doc_entete', 'date')}       TEXT,\n                {_c('doc_entete', 'reference')}  TEXT,\n                {_c('doc_entete', 'code_tiers')} TEXT\n            )", f"CREATE TABLE {_t('doc_ligne')} (\n                {_c('doc_ligne', 'ligne')}          INTEGER PRIMARY KEY AUTOINCREMENT,\n                {_c('doc_ligne', 'piece')}          TEXT,\n                {_c('doc_ligne', 'ref_article')}    TEXT,\n                {_c('doc_ligne', 'qte')}            REAL,\n                {_c('doc_ligne', 'prix_unitaire')}  REAL\n            )", f"CREATE TABLE {_t('reglements')} (\n                {_c('reglements', 'piece')}          TEXT,\n                {_c('reglements', 'mode_paiement')}  TEXT,\n                {_c('reglements', 'montant')}        REAL,\n                {_c('reglements', 'date_reglement')} TEXT\n            )"]
    for ddl in tables_ddl:
        vn.train(ddl=ddl)
    vn.train(documentation=f"""\n    RÈGLES SAGE 100 — colonnes réelles via db_config.json :\n    Table entête document : {_t('doc_entete')}\n      - pièce         : {_c('doc_entete', 'piece')}\n      - domaine       : {_c('doc_entete', 'domaine')}\n      - type document : {_c('doc_entete', 'type')}\n    \n    RÈGLE ANTI-HALLUCINATION CRITIQUE :\n      - N'utilise JAMAIS un nom de colonne qui n'apparaît pas explicitement \n        dans les CREATE TABLE ci-dessus. Si une information demandée ne \n        correspond à aucune colonne existante, explique-le plutôt que \n        d'inventer un nom de colonne plausible.\n    \n    INSTRUCTIONS DE STYLE SQL :\n      - Prefer simple JOINs over CTEs or subqueries when the same result can \n        be achieved, since simpler SQL is easier to validate and less error-prone.\n      - date          : {_c('doc_entete', 'date')}\n      - code tiers    : {_c('doc_entete', 'code_tiers')}\n    Types de documents ({_c('doc_entete', 'type')} / {_c('doc_entete', 'domaine')}) :\n      - type=6  domaine=0 → factures de vente\n      - type=3  domaine=0 → bons de livraison (BL)\n      - type=1  domaine=0 → bons de commande client (BC)\n      - type=25 domaine=2 → ordres de fabrication (OF)\n      - type=26 domaine=2 → bons de fabrication (BF)\n      - type=16 domaine=1 → factures fournisseur (achat)\n      - type=13 domaine=1 → bons de réception fournisseur\n      - type=11 domaine=1 → bons de commande fournisseur\n      - type=5  domaine=0 → avoirs de vente\n    Table tiers : {_t('clients_fournisseurs')}\n      - code     : {_c('clients_fournisseurs', 'code')}\n      - nom      : {_c('clients_fournisseurs', 'nom')}\n      - type (0=client,1=fourn) : {_c('clients_fournisseurs', 'type_tiers')}\n    Table articles : {_t('articles')}  |  stock : {_t('stock')}\n    Table lignes   : {_t('doc_ligne')} |  règlements : {_t('reglements')}\n\n    RÈGLE IMPORTANTE : les montants de documents (HT, TTC) ne sont JAMAIS\n    stockés sur une colonne d'entête. Ils doivent TOUJOURS être calculés par :\n    SUM(doc_ligne.qte * doc_ligne.prix_unitaire)\n    en joignant doc_entete à doc_ligne sur la pièce.\n\n    RÈGLES MÉTIER SUPPLÉMENTAIRES :\n      - Une facture impayée est une pièce de vente (type=6, domaine=0) dont la\n        pièce n'apparaît PAS dans la table reglements.\n      - "Achat" correspond toujours à domaine=1 et "vente" à domaine=0.\n      - "Client actif" signifie validite != 'BLOQUE'.\n      - "Encours" est la somme des factures non réglées d'un client, pas une\n        colonne directement stockée dans doc_entete.\n      - Le code tiers dans doc_entete.code_tiers identifie un client ou un\n        fournisseur selon doc_entete.domaine.\n      - Un bon de livraison est type=3 domaine=0, un bon de commande client est\n        type=1 domaine=0, un ordre de fabrication est type=25 domaine=2.\n\n    SYNONYMES MÉTIER UTILES :\n      - "commande" peut désigner un bon de commande OU une facture selon le\n        contexte ; privilégier le BC si le mot "commande" est explicite.\n      - "réception" désigne un BL_ACHAT côté fournisseur.\n      - "impayé", "non réglé", "en souffrance" et "en attente" peuvent être\n        synonymes dans le contexte des factures.\n      - "CA" et "chiffre d'affaires" sont équivalents.\n""")
    from vanna_training_neutral import construire_exemples_entrainement
    from adaptation.db_adapter import table as _t, col as _c
    exemples = construire_exemples_entrainement(_is_mssql(), table=_t, col=_c)
    for question, sql in exemples:
        vn.train(question=question, sql=sql)
    print(f'   📚 [Vanna] {len(exemples)} exemples + schéma entraînés.')

def _vanna_generer_sql(question: str) -> tuple[str | None, float]:
    """
Générer une requête SQL en fonction d'une question en fonction de la base de données et de la configuration actuels.
"""
    from vanna_training_neutral import generer_sql_thread_safe
    from api.mcp_actions_sage import _is_mssql
    from adaptation.db_adapter import table as _t_gen, col as _c_gen
    return generer_sql_thread_safe(_vanna_client, question, VANNA_GENERATE_TIMEOUT, _is_mssql(), table=_t_gen, col=_c_gen)

def _valider_sql(sql: str) -> tuple[bool, str]:
    """
Valide la syntaxe d'une requête SQL et renvoie un couple contenant un indicateur d'acceptabilité et un message d'erreur si applicable.
"""
    from vanna_training_neutral import valider_sql_dialecte
    from api.mcp_actions_sage import _is_mssql
    ok, _score = valider_sql_dialecte(sql, _is_mssql())
    return (ok, '' if ok else 'parse error')

def _init_langsmith():
    """
Initialisation de la configuration pour LangSmith en fonction de clés d'API et d'une variable de trace.
"""
    api_key = (os.getenv('LANGCHAIN_API_KEY') or '').strip()
    tracing = os.getenv('LANGCHAIN_TRACING_V2', 'false').lower() == 'true'
    if api_key and tracing:
        os.environ.setdefault('LANGCHAIN_PROJECT', 'copilot-erp-sage100')
        print('✅ [LangSmith] Tracing activé.')
    else:
        print('ℹ️  [LangSmith] Non configuré.')
_init_langsmith()
ACTIONS_LECTURE = {'TOP_CLIENTS', 'LISTE_CLIENTS', 'LISTE_ARTICLES', 'PALMARES_ARTICLES', 'CA_GLOBAL', 'CLIENTS_BAISSE', 'FACTURES_NON_REGLEES', 'FACTURES_NON_REGLEES_FOURN', 'TOUTES_FACTURES_CLIENT', 'VERIFIER_STOCK', 'FICHE_CLIENT', 'DOCS_PERIODE', 'RENTABILITE', 'SAISONNALITE', 'DSO', 'RFM', 'STATUT_CLIENT', 'LISTE_FOURNISSEURS', 'FICHE_FOURNISSEUR', 'TOP_FOURNISSEURS', 'AFFICHER_NOMENCLATURE', 'LIRE_ENCOURS_CLIENT'}
ACTIONS_NL2SQL = {'NL2SQL_LIBRE', 'LISTE_FACTURES'}
ACTIONS_EXPORT = {'OFFRE_PRIX_EXCEL', 'DECLARATION_EXCEL', 'BALANCE_AGEE_EXCEL', 'DASHBOARD_EXCEL'}
ACTIONS_ECRITURE = {'CREER_CLIENT', 'CREER_FOURNISSEUR', 'CREER_ARTICLE', 'CREER_NOMENCLATURE', 'MODIFIER_STATUT', 'MODIFIER_ARTICLE', 'MODIFIER_CLIENT', 'MODIFIER_FOURNISSEUR', 'GENERER_DOC', 'TRANSFORMER_DOC', 'CREER_AVOIR', 'REGLEMENT', 'MOUVEMENT_STOCK', 'PROPOSITION_ACHAT', 'OFFRE_PRIX'}
ACTIONS_WORKFLOW = {'WORKFLOW_COMMANDE'}
ACTIONS_KB = {'RECHERCHE_PROCEDURE', 'RECOMMANDATION', 'SEUIL_STOCK', 'LISTE_PROCEDURES'}
ACTIONS_HUB = {'VALIDER_DEMANDE', 'RESOUDRE_TYPE_DOC', 'GET_SCHEMA', 'CONTEXTE_CLIENT'}
ACTIONS_ENRICHIR_RAG = {'CLIENTS_BAISSE', 'DSO', 'FACTURES_NON_REGLEES', 'RFM'}
ACTIONS_SYNTHESE_LITE = {'GENERER_DOC', 'TRANSFORMER_DOC', 'CREER_AVOIR', 'REGLEMENT', 'MODIFIER_STATUT', 'CREER_CLIENT', 'CREER_FOURNISSEUR', 'MOUVEMENT_STOCK', 'FACTURES_NON_REGLEES_FOURN', 'VERIFIER_STOCK', 'FICHE_CLIENT', 'STATUT_CLIENT', 'LIRE_ENCOURS_CLIENT'}
_STATUTS_ACTIONS_V3_OK = {'GENERE', 'TRANSFORME', 'CREE', 'MODIFIE', 'REGLE', 'MOUVEMENT_ENREGISTRE', 'INCHANGE'}
_STATUTS_ERREUR_MCP = {'CLIENT_NON_TROUVE', 'ARTICLE_NON_TROUVE', 'STOCK_INSUFFISANT', 'CLIENT_BLOQUE', 'COMPOSANTS_INSUFFISANTS', 'NON_TROUVE', 'EXISTE_DEJA', 'ERREUR'}
MOTS_REFERENCE_DOCUMENT = ('précédent', 'précédente', 'dernier', 'dernière', 'celui-ci', 'celle-ci', 'ce document', 'ce bl', 'cette facture')
_TYPES_DOC_INVALIDES_COMME_ARTICLE = {'OF', 'BF', 'BL', 'BL_ACHAT', 'FA_ACHAT', 'FA', 'FC', 'BC', 'BC_ACHAT', 'FACTURE', 'AVOIR', 'AV'}
TYPES_DOC_FABRICATION = {'OF', 'BF'}
_EXPRESSIONS_FR_EXCLUES = {'A-T-IL', 'A-T-ELLE', 'A-T-ON', 'EST-CE', 'EST-IL', 'EST-ELLE', 'SONT-ILS', 'SONT-ELLES', 'Y-A-T-IL', 'N-EST-CE-PAS', 'QU-EST-CE', 'PEUT-IL', 'PEUT-ELLE', 'DOIT-IL', 'DOIT-ELLE', 'FAUT-IL', 'VA-T-IL', 'VA-T-ELLE', 'AVAIT-IL', 'POURRAIT-IL', 'POURRAIT-ELLE', 'DONNE-MOI', 'DIS-MOI', 'MONTRE-MOI', 'LAISSE-MOI', 'PRETE-MOI', 'PARLE-MOI', 'EXPLIQUE-MOI', 'ENVOIE-MOI', 'PRECISE-MOI', 'INDIQUE-MOI', 'RAPPELLE-MOI', 'CONFIRME-MOI'}
_EXCL_ARTICLE = {'CLI', 'BL', 'FA', 'FC', 'BC', 'OF', 'BF', 'BA', 'AV', 'ERP', 'NL2SQL', 'SQL', 'PDF', 'KPI', 'DSO', 'RFM', 'CA', 'CREE', 'CREER', 'POUR', 'AVEC', 'PIECES', 'PIECE', 'PCS', 'UNITE', 'UNITES', 'LE', 'LA', 'LES', 'UN', 'UNE', 'DES', 'OK', 'OUI', 'NON', 'LANCE', 'GENERE', 'FAIRE', 'NOUVEAU', 'PROD', 'INT', 'PRODINT', 'SAGE', 'LISTE', 'DONNE', 'AFFICHE', 'TOUS', 'TOUTES', 'MONTRE', 'CLIENTS', 'ARTICLES', 'CLIENT', 'ARTICLE', 'ENCOURS', 'STATUT', 'FICHE', 'INFO', 'FOURNISSEUR', 'FOURNISSEURS', 'FOUR', 'GROSSISTE', 'FOURN', 'ACHAT', 'ACHATS', 'COMMANDE', 'COMMANDES', 'FACTURES', 'FACTURE', 'FOURNISSEUR', 'FOURNISSEURS', 'LISTE', 'DETAIL', 'DETAILS', 'RAPPORT', 'STOCK', 'STOCKS', 'DISPONIBLE', 'DISPONIBLES', 'RESTANT', 'RUPTURE', 'RUPTURES', 'FAIBLE', 'FAIBLES', 'ONT', 'PASSE', 'COMMANDE', 'DEPUIS', 'MOIS', 'EST', 'SONT', 'AVEZ', 'AVONS', 'AVAIT', 'NON', 'PAS', 'SANS', 'AUCUN', 'AUCUNE', 'FACTURE', 'FACTURES', 'LISTE', 'DONNE', 'MONTRE', 'QUEL', 'QUELS', 'QUELLE', 'QUELLES', 'QUI', 'QUOI', 'COMMENT', 'COMBIEN', 'POURQUOI', 'INACTIFS', 'BLOQUES', 'BLOQUE', 'ACTIFS', 'VALIDE', 'SUSPECT', 'VENDUS', 'ACHETÉS', 'COMMANDÉS', 'GLOBAL', 'TOTAL', 'MENSUEL', 'ANNUEL', 'CLIENTS', 'ARTICLES', 'FOURNISSEURS', 'PRIX', 'TARIF', 'COUT', 'COÛT', 'VALEUR', 'MONTANT', 'DT', 'EUR', 'EUROS'}
_MOTS_GENERIQUES_NER = {'client', 'tiers', 'le', 'la', 'les', 'un', 'une', 'des', 'pour', 'avec', 'article', 'produit', 'référence', 'ref', 'piece', 'pièce', 'unité', 'unite', 'quantite', 'quantité', 'société', 'societe', 'entreprise', 'volume', 'achat', 'achats', 'par'}
_MARQUEURS_NL2SQL_FORCE = {'mois par mois', 'évolution', 'tendance', 'uniquement', 'seulement', 'croisement', 'en commun', 'meilleurs clients', 'top.*client.*fourni', 'vendus à un seul', 'having', 'ratio', 'panier moyen', 'taux de', 'par nombre de commandes', 'nombre de commandes', 'commandés ce mois', 'commandé ce mois', 'inférieur au seuil', 'stock insuffisant', 'trier par commandes', 'classement', 'classé', 'classe', 'classer', 'classés', 'classee', 'classees'}
_MOTS_QUALIFICATIFS_FILTRAGE = ('impayé', 'impayés', 'ne paient pas', 'plus de', 'moins de', 'supérieur', 'supérieures', 'inférieur', 'avec des', 'qui ont', 'par ca', 'par chiffre', 'top', 'meilleurs', 'plus gros', 'inactif', 'bloqué', 'encours')
CAPACITES_SYSTEME = "Ce que je sais faire sur votre ERP Sage 100 :\n  📊 Lecture & Analyse  : liste clients/articles, top clients CA, palmarès, CA global, saisonnalité, rentabilité, DSO, RFM\n  🧾 Factures           : toutes les factures d'un client, factures non réglées/impayées\n  🔍 Recherche          : fiche client, statut client, stock article, documents par période\n  📁 Export Excel       : offre de prix, déclaration fiscale, balance âgée, dashboard KPI\n  ✍️  Écriture          : créer client, modifier statut, générer BL/Facture/BC/OF/BF, transformer document, créer avoir, régler facture, mouvement stock\n  🔄 Workflow           : flux commande complet (vérification → production → livraison → facturation)\n  📚 Base de connaissances : procédures internes, recommandations, seuils de stock"
_LLM_PLACEHOLDERS = {'INCONNU', 'AUCUN', 'N/A', '0', '-', '', 'VALEUR_NON_REPRESENTE', 'VALEUR_NON_REPRÉSENTE', 'NON_REPRESENTE', 'NON_REPRÉSENTE', 'NULL', 'NONE', 'VIDE', 'ABSENT', 'NA', 'NON_RENSEIGNE', 'NON_RENSEIGNÉ', 'INDEFINI', 'INDÉFINI', 'UNDEFINED', 'UNKNOWN', 'VALEUR', 'VALEUR_MANQUANTE', 'MANQUANT', 'NOM_COMPLET_OU_CODE_OU_INCONNU', 'VALEUR_OU_INCONNU', 'CODE_CLIENT', 'CODE_OU_INCONNU'}
_ACTIONS_TOUTES_CONNUES = ACTIONS_LECTURE | ACTIONS_NL2SQL | ACTIONS_EXPORT | ACTIONS_ECRITURE | ACTIONS_WORKFLOW | ACTIONS_KB | ACTIONS_HUB | {'AMBIGUE'}
_LLM_PLACEHOLDERS |= _ACTIONS_TOUTES_CONNUES

def _clean(v: str) -> str:
    """
Supprime les guillemets et apostrophes d'un texte, puis renvoie le texte propre si ce n'est pas un placeholder.
"""
    v = v.replace('"', '').replace("'", '').strip()
    return '' if v.upper() in _LLM_PLACEHOLDERS else v
_PATTERNS_PRECLASS = [("lots?\\s+(?:encore\\s+)?disponibles?\\s+(?:pour|de|du|d['\\u2019])", 'NL2SQL_LIBRE'), ("quels?\\s+lots?\\s+disponibles?\\s+(?:pour|de|du|d['\\u2019])", 'NL2SQL_LIBRE'), ('quantit[eé]\\s+restante\\s+(?:du\\s+|de\\s+)?lot\\b', 'NL2SQL_LIBRE'), ('\\blot\\b.{0,20}est[\\s-]il\\s+(?:encore\\s+)?disponible', 'NL2SQL_LIBRE'), ("d['\\u2019]o[u\\u00f9]\\s+vient\\s+(?:le\\s+)?lot\\b", 'NL2SQL_LIBRE'), ('origine\\s+(?:du\\s+)?lot\\b', 'NL2SQL_LIBRE'), ('sur\\s+quel\\s+bl\\s+.{0,20}lot\\b', 'NL2SQL_LIBRE'), ('lots?\\s+.{0,20}(?:expirent?|p[eé]rim[eé]s?|p[eé]remption)', 'NL2SQL_LIBRE'), ('lots?\\s+[eé]puis[eé]s?', 'NL2SQL_LIBRE'), ("marge\\s+(?:brute\\s+)?(?:sur|de|pour)\\s+(?:l['\\u2019]article\\s+)?[A-Za-z0-9\\-]+", 'NL2SQL_LIBRE'), ('liste[s\\s]*(?:de[s\\s]*)?(?:bf|of|bl|factures?|bc)\\b', 'NL2SQL_LIBRE'), ('transform[e\\s]+.{0,60}\\bof\\b.{0,60}\\bbf\\b', 'TRANSFORMER_DOC'), ('transform[e\\s]+.{0,60}\\bbl\\b.{0,60}facture', 'TRANSFORMER_DOC'), ('transform[e\\s]+.{0,30}\\bbc\\b.{0,20}\\bbl\\b', 'TRANSFORMER_DOC'), ('transform[e\\s]+.{0,15}(?:fa|bl|bc|of|bf)\\d+', 'TRANSFORMER_DOC'), ('transform[e\\s]+.{0,15}[a-z]{2}\\d{6,}', 'TRANSFORMER_DOC'), ('(?:transform|passe|converti).{0,30}num[eé]ro.{0,60}\\b(?:of|bl|bc|bf|fa)[A-Z0-9]+.{0,20}\\b(?:bf|bl|facture|bc)\\b', 'TRANSFORMER_DOC'), ('convert[i\\s]+.{0,30}(?:bl|of|bc).{0,20}(?:facture|bf|bl)', 'TRANSFORMER_DOC'), ('facturer\\s+(?:le\\s+)?bl\\b', 'TRANSFORMER_DOC'), ('passer\\s+(?:le\\s+)?(?:bl|of)\\b.{0,20}en\\b', 'TRANSFORMER_DOC'), ('(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:la\\s+|une\\s+|le\\s+|un\\s+)?bf\\s+(?:pour|de|à\\s+partir\\s+de)\\s+.{0,10}\\bof[a-z0-9]*\\d+', 'TRANSFORMER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:la\\s+|une\\s+|le\\s+|un\\s+)?facture(?:\\s*(?:d['’]achat|d'achat|achat|fournisseur))?\\s+(?:pour|de|à\\s+partir\\s+de)\\s+.{0,15}\\b(?:OF|BL|BC|BF|FA|BR|FBL)[0-9A-Z]{5,9}\\b", 'TRANSFORMER_DOC'), ('(?:liste[s]?|affiche|montre|donne|quels?|tous?|toutes?)\\s+.{0,30}(?:bons?\\s+de\\s+r[eé]ception|r[eé]ceptions?\\s+fournisseur|livraisons?\\s+fournisseur|bl\\s+achat)', 'NL2SQL_LIBRE'), ('bl\\s+achat|bon\\s+de\\s+r[eé]ception|r[eé]ception\\s+fournisseur|livraison\\s+fournisseur', 'GENERER_DOC'), ('(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+.{0,20}bl\\s+achat', 'GENERER_DOC'), ('(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+.{0,20}r[eé]ception\\s+fournisseur', 'GENERER_DOC'), ('r[eé]gler?\\s+(la\\s+|une\\s+|les\\s+)?(?:facture|fa)\\s+[A-Z0-9]+', 'REGLEMENT'), ('r[eé]glement\\s+(?:de\\s+la\\s+)?(?:facture|fa)\\s+[A-Z0-9]+', 'REGLEMENT'), ('change.{0,30}(?:statut|status).{0,30}(?:facture|fa)\\s+[A-Z0-9]+', 'REGLEMENT'), ('marquer?\\s+(?:la\\s+)?(?:facture|fa)\\s+[A-Z0-9]+.{0,30}r[eé]gl[eé]', 'REGLEMENT'), ('(?:facture|fa)\\s+([A-Z0-9]{3,})\\s+.{0,20}r[eé]gl[eé]e?', 'REGLEMENT'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?bl\\b", 'GENERER_DOC'), ('\\bbl\\s+(pour|client|cli|de\\s+\\d)', 'GENERER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?of\\b", 'GENERER_DOC'), ('ordre\\s+de\\s+fabrication', 'GENERER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?bf\\b", 'GENERER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t)|[eé]tabli[rs])\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?facture", 'GENERER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?bc\\b", 'GENERER_DOC'), ('bon\\s+de\\s+commande', 'GENERER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?bon\\b", 'GENERER_DOC'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation)\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+|un\\s+nouveau\\s+|nouveau\\s+)?client", 'CREER_CLIENT'), ('enregistr(?:er?|ez?)\\s+(?:un\\s+|le\\s+)?(?:nouveau\\s+)?client', 'CREER_CLIENT'), ('saisi[rs]?\\s+(?:un\\s+|le\\s+)?(?:nouveau\\s+)?client', 'CREER_CLIENT'), ('nouveau\\s+client', 'CREER_CLIENT'), ('ajouter?\\s+(un\\s+)?client', 'CREER_CLIENT'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation)\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+|un\\s+nouveau\\s+|nouveau\\s+)?fournisseur", 'CREER_FOURNISSEUR'), ('enregistr(?:er?|ez?)\\s+(?:un\\s+|le\\s+)?(?:nouveau\\s+)?fournisseur', 'CREER_FOURNISSEUR'), ('saisi[rs]?\\s+(?:un\\s+|le\\s+)?(?:nouveau\\s+)?fournisseur', 'CREER_FOURNISSEUR'), ('nouveau\\s+fournisseur', 'CREER_FOURNISSEUR'), ('ajouter?\\s+(un\\s+)?fournisseur', 'CREER_FOURNISSEUR'), ('modifier?\\s+(?:le\\s+|un\\s+|mon\\s+)?client', 'MODIFIER_CLIENT'), ('(?:changer?|mettre?\\s+[\\u00e0a]\\s+jour|actualiser?|\\u00e9diter?)\\s+(?:le\\s+|un\\s+|mon\\s+)?client', 'MODIFIER_CLIENT'), ('modifier?\\s+(?:le\\s+|un\\s+|mon\\s+)?fournisseur', 'MODIFIER_FOURNISSEUR'), ('(?:changer?|mettre?\\s+[\\u00e0a]\\s+jour|actualiser?|\\u00e9diter?)\\s+(?:le\\s+|un\\s+|mon\\s+)?fournisseur', 'MODIFIER_FOURNISSEUR'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation)\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|l['\\u2019]|le\\s+|la\\s+|un\\s+nouveau\\s+|nouveau\\s+)?articles?", 'CREER_ARTICLE'), ("enregistr(?:er?|ez?)\\s+(?:un\\s+|l['\\u2019])?(?:nouveau\\s+)?articles?", 'CREER_ARTICLE'), ("saisi[rs]?\\s+(?:un\\s+|l['\\u2019])?(?:nouveau\\s+)?articles?", 'CREER_ARTICLE'), ('nouveau\\s+articles?', 'CREER_ARTICLE'), ('ajouter?\\s+(un\\s+)?articles?', 'CREER_ARTICLE'), ('(?:cr[eé][eé]?(?:r|er|z)?|ajouter?)\\s+(?:une\\s+)?nomenclature', 'CREER_NOMENCLATURE'), ('(?:cr[eé][eé]?(?:r|er|z)?|ajouter?)\\s+(?:des\\s+)?composants?', 'CREER_NOMENCLATURE'), ("(?:affiche|montre|donne|voir|consulter|liste)\\s+(?:la\\s+)?nomenclature\\s+(?:de|du|pour|d['\\u2019])\\s+.+", 'AFFICHER_NOMENCLATURE'), ("nomenclature\\s+(?:de|du|pour|d['\\u2019])\\s+.+", 'AFFICHER_NOMENCLATURE'), ('modifier?\\s+(?:la\\s+)?nomenclature', 'MODIFIER_NOMENCLATURE'), ('[eé]diter?\\s+(?:la\\s+)?nomenclature', 'MODIFIER_NOMENCLATURE'), ('g[eé]rer?\\s+(?:la\\s+)?nomenclature', 'MODIFIER_NOMENCLATURE'), ('supprimer?\\s+(?:un\\s+)?composant\\s+de', 'MODIFIER_NOMENCLATURE'), ('retirer?\\s+(?:un\\s+)?composant\\s+de', 'MODIFIER_NOMENCLATURE'), ('changer?\\s+(?:la\\s+)?nomenclature', 'MODIFIER_NOMENCLATURE'), ('bloquer?\\s+(le\\s+)?client', 'MODIFIER_STATUT'), ('d[e\\u00e9]bloquer?\\s+(le\\s+)?client', 'MODIFIER_STATUT'), ('r[e\\u00e9]activer?\\s+(le\\s+)?client', 'MODIFIER_STATUT'), ('bloquer?\\s+(le\\s+)?fournisseur', 'MODIFIER_STATUT'), ('d[e\\u00e9]bloquer?\\s+(le\\s+)?fournisseur', 'MODIFIER_STATUT'), ('r[e\\u00e9]activer?\\s+(le\\s+)?fournisseur', 'MODIFIER_STATUT'), ('modifier?\\s+(le\\s+)?statut', 'MODIFIER_STATUT'), ("modifier?\\s+(l['\\u2019]|un\\s+|une\\s+|le\\s+)?articles?\\b", 'MODIFIER_ARTICLE'), ("(?:changer?|mettre?\\s+[àa]\\s+jour|actualiser?)\\s+(l['\\u2019]|un\\s+|une\\s+|le\\s+)?articles?\\b", 'MODIFIER_ARTICLE'), ("modifier?\\s+(la\\s+|le\\s+)?(d[eé]signation|prix\\s+(?:d['\\u2019]\\s*achat|de\\s+vente|achat|vente)|type)\\s+.{0,20}articles?", 'MODIFIER_ARTICLE'), ('^modifier?\\s+(?!.*\\b(?:statut|client|fournisseur|facture|bl|bc|of|bf|commande)\\b)[a-z][a-z0-9\\-]{2,}\\s*$', 'MODIFIER_ARTICLE'), ("(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|fai(?:s|re|t))\\s+(?:d['\\u2019]|de\\s+|un\\s+|une\\s+|le\\s+|la\\s+)?avoir", 'CREER_AVOIR'), ('r[eé]gler?\\s+(la\\s+|une\\s+|les\\s+)?factures?', 'REGLEMENT'), ('r[eé]glement\\s+(d.une\\s+|de\\s+la\\s+)?facture', 'REGLEMENT'), ('payer?\\s+(la\\s+|une\\s+|les\\s+)?factures?', 'REGLEMENT'), ('payer?\\s+(?:la\\s+)?(?:facture\\s+)?(?:FA|BL|BC|BF)\\d+', 'REGLEMENT'), ('paiement\\s+(d.une\\s+|de\\s+la\\s+)?facture', 'REGLEMENT'), ('change.{0,20}statut.{0,20}facture.{0,20}r[eé]gl[eé]', 'REGLEMENT'), ("fiche\\s+technique\\s+(?:du|de\\s+la|de\\s+l['\\u2019]|de|d['\\u2019])\\s+\\S+", 'RECHERCHE_PROCEDURE'), ("caract[eé]ristiques?\\s+(?:du|de\\s+la|de\\s+l['\\u2019]|de|d['\\u2019])\\s+\\S+", 'RECHERCHE_PROCEDURE'), ('r[eé]clamations?\\s+.{0,20}articles?', 'RECHERCHE_PROCEDURE'), ('articles?\\s+.{0,20}r[eé]clamations?', 'RECHERCHE_PROCEDURE'), ('r[eé]clamations?', 'RECHERCHE_PROCEDURE'), ('motifs?\\s+de\\s+r[eé]clamation', 'RECHERCHE_PROCEDURE'), ('\\bd[eé]fauts?\\b', 'RECHERCHE_PROCEDURE'), ('\\bpannes?\\b', 'RECHERCHE_PROCEDURE'), ('\\bsav\\b', 'RECHERCHE_PROCEDURE'), ('tol[eé]rance', 'RECHERCHE_PROCEDURE'), ('proc[eé]d[eé]\\s+de\\s+fabrication', 'RECHERCHE_PROCEDURE'), ('\\bmati[eè]re\\b', 'RECHERCHE_PROCEDURE'), ('temp[eé]rature', 'RECHERCHE_PROCEDURE'), ('\\bprocess\\b', 'RECHERCHE_PROCEDURE'), ('pr[eé]caution', 'RECHERCHE_PROCEDURE'), ('garantie', 'RECHERCHE_PROCEDURE'), ('\\bremise\\b', 'RECHERCHE_PROCEDURE'), ('conditions?\\s+(commerciales?|n[eé]goci[eé]es?)', 'RECHERCHE_PROCEDURE'), ('command[eé]e?s?\\s+par\\s+email', 'RECHERCHE_PROCEDURE'), ('email\\s+de\\s+commande', 'RECHERCHE_PROCEDURE'), ('\\bclient\\b.{0,25}\\best[\\s-]il\\s+bloqu[eé]', 'STATUT_CLIENT'), ('\\bclient\\b.{0,25}\\best[\\s-]il\\s+(?:actif|valide|suspect)', 'STATUT_CLIENT'), ('le\\s+client\\s+[A-Z0-9]+\\s+est[\\s-]il', 'STATUT_CLIENT'), ('(?:liste|donne|affiche|montre).{0,30}bons?\\s+de\\s+livraison', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre).{0,20}\\bbl\\b.{0,20}client', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre).{0,30}\\bbl\\b', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre).{0,30}bons?\\s+de\\s+commande', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre).{0,30}bons?\\s+de\\s+fabrication', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre).{0,30}ordres?\\s+de\\s+fabrication', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre|quels?).{0,20}\\bof\\b', 'NL2SQL_LIBRE'), ('bons?\\s+de\\s+livraison\\s+(?:du\\s+|de\\s+)?client', 'NL2SQL_LIBRE'), ('\\bbl\\b.{0,30}(?:du\\s+|de\\s+)?client', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre).{0,20}\\bbl\\b.{0,40}(?:mois|p[eé]riode|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)', 'NL2SQL_LIBRE'), ('\\bbl\\b.{0,20}(?:du\\s+mois|de\\s+(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre))', 'NL2SQL_LIBRE'), ('bons?\\s+de\\s+livraison.{0,40}(?:mois|p[eé]riode|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)', 'NL2SQL_LIBRE'), ('top\\s*\\d*\\s*clients?\\s+par\\s+(?:ca|chiffre)', 'TOP_CLIENTS'), ('meilleurs?\\s+clients?\\s+par\\s+ca\\b', 'TOP_CLIENTS'), ('clients?\\s+avec\\s+des\\s+impay[eé]s?', 'NL2SQL_LIBRE'), ('clients?\\s+qui\\s+ne\\s+paient\\s+pas', 'NL2SQL_LIBRE'), ('clients?\\s+qui\\s+ont\\s+pass[eé]\\s+plus\\s+de\\s+\\d+\\s+commandes?', 'NL2SQL_LIBRE'), ('clients?\\s+ayant\\s+des\\s+factures?\\s+sup[eé]rieures?\\s+[àa]\\s+\\d+', 'NL2SQL_LIBRE'), ('articles?\\s+(?:qui\\s+)?co[uû]tent\\s+(?:plus|moins)\\s+(?:de|que)\\s*\\d+', 'NL2SQL_LIBRE'), ('articles?\\s+(?:dont|avec).{0,20}prix.{0,20}(?:sup[eé]r|inf[eé]r|plus|moins|>|<)\\s*\\d+', 'NL2SQL_LIBRE'), ('factures?\\s+(?:sup[eé]rieure?s?\\s+[àa]|plus\\s+(?:de|que)|>\\s*)\\s*\\d+', 'NL2SQL_LIBRE'), ('factures?\\s+(?:inf[eé]rieure?s?\\s+[àa]|moins\\s+(?:de|que)|<\\s*)\\s*\\d+', 'NL2SQL_LIBRE'), ('factures?\\s+entre\\s+\\d+\\s+et\\s+\\d+', 'NL2SQL_LIBRE'), ('clients?\\s+(?:ayant|avec|qui\\s+ont)\\s+(?:des?\\s+)?factures?', 'NL2SQL_LIBRE'), ('clients?\\s+(?:dont|avec)\\s+(?:un\\s+)?(?:ca|chiffre).{0,30}\\d+', 'NL2SQL_LIBRE'), ('clients?\\s+(?:dont|avec)\\s+(?:un\\s+)?encours.{0,30}\\d+', 'NL2SQL_LIBRE'), ('articles?\\s+(?:dont|avec).{0,30}stock.{0,20}\\d+', 'NL2SQL_LIBRE'), ('articles?.{0,20}stock.{0,20}(?:inf[eé]r|sup[eé]r|<|>)\\s*\\d+', 'NL2SQL_LIBRE'), ('articles?\\s+(?:vendus?|achet[eé]s?)\\s+(?:plus|moins)\\s+(?:de|que)\\s+\\d+', 'NL2SQL_LIBRE'), ('top\\s+\\d+\\s+(?!clients?)(?:articles?|produits?|références?)', 'NL2SQL_LIBRE'), ('(?:liste|donne|affiche|montre)\\s+.{0,40}\\b(?:o[ùu]|mais|dont|sauf|seulement|uniquement|filtre)\\b', 'NL2SQL_LIBRE'), ("factures?\\s+(?:du\\s+|de\\s+|d['\\u2019]?\\s*)?(?:mois\\s+(?:de\\s+)?)?(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|jan|fév|mar|avr|jun|jul|aoû|sep|oct|nov|déc)", 'NL2SQL_LIBRE'), ('factures?\\s+(?:du\\s+)?mois\\s+\\d{1,2}', 'NL2SQL_LIBRE'), ("factures?\\s+(?:de\\s+)?(?:l['\\u2019]ann[eé]e|\\d{4})", 'NL2SQL_LIBRE'), ('factures?\\s+(?:d\\s+|de\\s+)?(?:trimestre|semestre)', 'NL2SQL_LIBRE'), ('(?:liste|affiche|montre|donne).{0,30}factures?.{0,30}(?:mois|ann[eé]e|p[eé]riode|semaine)', 'NL2SQL_LIBRE'), ('(?:liste|affiche|montre|donne).{0,30}factures?.{0,30}(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)', 'NL2SQL_LIBRE'), ('articles?\\s+(?:dont|avec|au|ayant).{0,30}(?:prix|tarif|co[uû]t).{0,30}(?:sup[eé]r|inf[eé]r|d[eé]passe|plus|moins|\\>|\\<)', 'NL2SQL_LIBRE'), ('articles?\\s+(?:dont|avec).{0,30}prix.{0,30}\\d+', 'NL2SQL_LIBRE'), ("(?:prix|tarif)\\s+(?:de\\s+vente|d['\\u2019]achat).{0,30}(?:sup[eé]r|inf[eé]r|d[eé]passe|plus|moins|\\>|\\<)", 'NL2SQL_LIBRE'), ('articles?\\s+(?:dont|avec).{0,30}(?:marge|rentabilit)', 'NL2SQL_LIBRE'), ("clients?\\s+(?:qui\\s+ont|ayant|avec).{0,30}(?:plus\\s+de|plus\\s+qu[e'\\u2019]|au\\s+moins)\\s+\\d+\\s+(?:commandes?|factures?|achats?)", 'NL2SQL_LIBRE'), ("clients?\\s+(?:qui\\s+ont|ayant|avec).{0,30}(?:moins\\s+de|moins\\s+qu[e'\\u2019])\\s+\\d+\\s+(?:commandes?|factures?|achats?)", 'NL2SQL_LIBRE'), ('clients?\\s+(?:pass[eé]|effectu[eé]).{0,20}(?:plus\\s+de|au\\s+moins)\\s+\\d+\\s+(?:commandes?|achats?)', 'NL2SQL_LIBRE'), ('clas(?:se|sement|s[eé])\\s+.{0,30}clients?.{0,30}(?:nombre|nb)\\s+(?:de\\s+)?commandes?', 'NL2SQL_LIBRE'), ('clients?.{0,30}(?:tri[eé]s?|class[eé]s?|ordonn[eé]s?|rang[eé]s?).{0,30}(?:nombre|nb).{0,20}commandes?', 'NL2SQL_LIBRE'), ('clients?.{0,30}par\\s+(?:nombre|nb)\\s+(?:de\\s+)?commandes?', 'NL2SQL_LIBRE'), ('(?:nombre|nb)\\s+(?:de\\s+)?commandes?\\s+(?:par\\s+)?client', 'NL2SQL_LIBRE'), ('qui\\s+(?:commande|achète|a\\s+achet[eé])\\s+le\\s+plus', 'NL2SQL_LIBRE'), ('clas(?:se|ser|s[eé]s?)\\s+les\\s+clients?\\s+.{0,30}(?:chiffre|ca\\b)', 'NL2SQL_LIBRE'), ('(?:articles?|produits?)\\s+(?:sans\\s+stock|en\\s+rupture)', 'NL2SQL_LIBRE'), ("stock\\s+(?:disponible|actuel|restant)\\s+(?:pour|de|d['\\u2019])\\s+\\S+", 'VERIFIER_STOCK'), ("stock\\s+de\\s+l['\\u2019]article", 'VERIFIER_STOCK'), ('quel\\s+est\\s+le\\s+stock', 'VERIFIER_STOCK'), ('combien\\s+(?:de\\s+)?stock', 'VERIFIER_STOCK'), ('stock\\s+(?:disponible|actuel|restant)\\s*(?:est\\s+)?(?:[<>=]|inf[eé]r|sup[eé]r)\\s*\\d+', 'NL2SQL_LIBRE'), ('stock\\s+(?:nul|à\\s+z[ée]ro)', 'NL2SQL_LIBRE'), ('(?:articles?|produits?).{0,20}stock.{0,20}(?:inf[ée]r|nul|z[ée]ro|<\\s*0)', 'NL2SQL_LIBRE'), ("stock\\s+(?:disponible|actuel|restant)\\s+de\\s+l['\\u2019]article", 'VERIFIER_STOCK'), ('articles?.{0,40}command[eé]s?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique)', 'NL2SQL_LIBRE'), ('articles?.{0,30}(?:stock\\s+(?:faible|bas|insuffisant|inf[eé]r|critique)|sous.{0,10}seuil).{0,40}(?:command[eé]|achet[eé])', 'NL2SQL_LIBRE'), ('rupture.{0,20}command[eé]|command[eé].{0,20}rupture', 'NL2SQL_LIBRE'), ('clients?\\s+(?:actifs?|avec|ayant|dont).{0,80}(?:factures?\\s+impay[eé]es?|encours|ca\\b)', 'NL2SQL_LIBRE'), ('clients?.{0,50}(?:encours\\s+sup[eé]r|encours\\s+>\\s*\\d+|encours\\s+plus)', 'NL2SQL_LIBRE'), ('factures?\\s+entre\\s+(?:le\\s+)?\\d{4}-\\d{2}-\\d{2}\\s+et\\s+(?:le\\s+)?\\d{4}-\\d{2}-\\d{2}', 'DOCS_PERIODE'), ('documents?\\s+entre\\s+(?:le\\s+)?\\d{4}-\\d{2}-\\d{2}\\s+et\\s+(?:le\\s+)?\\d{4}-\\d{2}-\\d{2}', 'DOCS_PERIODE'), ('(?:documents?|factures?|bls?)\\b.{0,60}\\bentre\\s+(?:le\\s+)?\\d{4}-\\d{2}-\\d{2}\\s+(?:et|au)\\s+(?:le\\s+)?\\d{4}-\\d{2}-\\d{2}', 'DOCS_PERIODE'), ('clients?\\s+bloqu[eé]s?', 'NL2SQL_LIBRE'), ('bloqu[eé]s?\\s+clients?', 'NL2SQL_LIBRE'), ('quels?\\s+clients?.{0,30}bloqu[eé]', 'NL2SQL_LIBRE'), ('clients?\\s+inactifs?', 'CLIENTS_INACTIFS'), ('clients?\\s+sans\\s+commande', 'CLIENTS_INACTIFS'), ('(?:clients?|qui)\\s+(?:sont\\s+)?en\\s+baisse\\s+(?:de\\s+)?(?:ca|chiffre)', 'CLIENTS_BAISSE'), ('baisse\\s+(?:de\\s+)?(?:ca|chiffre|revenu)', 'CLIENTS_BAISSE'), ("encours\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?client\\b.{1,60}\\b", 'LIRE_ENCOURS_CLIENT'), ("(?:quel\\s+(?:est\\s+)?(?:l['\\u2019])?|donne(?:\\s*-?\\s*moi)?(?:\\s+l['\\u2019])?|affiche(?:\\s+l['\\u2019])?)encours.{0,60}", 'LIRE_ENCOURS_CLIENT'), ("encours\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?client", 'LIRE_ENCOURS_CLIENT'), ('cr[eé]dit\\s+(du\\s+)?client', 'NL2SQL_LIBRE'), ('solde\\s+(du\\s+)?client', 'NL2SQL_LIBRE'), ('limite\\s+(du\\s+)?client', 'NL2SQL_LIBRE'), ("encours\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?fournisseur", 'NL2SQL_LIBRE'), ("cr[eé]dit\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?fournisseur", 'NL2SQL_LIBRE'), ("solde\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?fournisseur", 'NL2SQL_LIBRE'), ("limite\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?fournisseur", 'NL2SQL_LIBRE'), ('liste\\s+les?\\s+fournisseurs', 'LISTE_FOURNISSEURS'), ('liste\\s+(tous\\s+)?(les\\s+)?fournisseurs?', 'LISTE_FOURNISSEURS'), ('(tous|toutes)\\s+(les\\s+)?fournisseurs?', 'LISTE_FOURNISSEURS'), ('affiche\\s+(les\\s+)?fournisseurs?', 'LISTE_FOURNISSEURS'), ('montre\\s+(moi\\s+)?(les\\s+)?fournisseurs?', 'LISTE_FOURNISSEURS'), ('donne\\s+(moi\\s+)?(les\\s+)?fournisseurs?', 'LISTE_FOURNISSEURS'), ('fiche\\s+(du\\s+|de\\s+)?fournisseur', 'FICHE_FOURNISSEUR'), ('info\\w*\\s+(sur\\s+)?(le\\s+)?fournisseur', 'FICHE_FOURNISSEUR'), ('fournisseurs?\\s+actifs?', 'LISTE_FOURNISSEURS'), ('quels?\\s+fournisseurs?', 'LISTE_FOURNISSEURS'), ('top\\s*\\d*\\s*fournisseurs?', 'TOP_FOURNISSEURS'), ('meilleurs?\\s+fournisseurs?', 'TOP_FOURNISSEURS'), ('achats?\\s+(par\\s+)?fournisseur', 'TOP_FOURNISSEURS'), ('commandes?\\s+(chez|aupres|auprès)\\s+', 'NL2SQL_LIBRE'), ('bons?\\s+de\\s+commande\\s+(du\\s+|de\\s+)?fournisseur', 'NL2SQL_LIBRE'), ('top\\s*\\d*\\s*clients?\\s+par\\s+(?:ca|chiffre)', 'TOP_CLIENTS'), ('meilleurs?\\s+clients?\\s+par\\s+ca\\b', 'TOP_CLIENTS'), ('clients?\\s+avec\\s+des\\s+impay[eé]s?', 'NL2SQL_LIBRE'), ('clients?\\s+qui\\s+ne\\s+paient\\s+pas', 'NL2SQL_LIBRE'), ('clients?\\s+qui\\s+ont\\s+pass[eé]\\s+plus\\s+de\\s+\\d+\\s+commandes?', 'NL2SQL_LIBRE'), ('clients?\\s+ayant\\s+des\\s+factures?\\s+sup[eé]rieures?\\s+[àa]\\s+\\d+', 'NL2SQL_LIBRE'), ('clients?.{0,60}(?:impay[eé]|non\\s+r[eé]gl[eé]|encours|ca\\b|chiffre\\s+d.affaires|ne\\s+pa(?:ient?|yer)|plus\\s+de\\s+\\d+\\s+factures?|moins\\s+de\\s+\\d+\\s+factures?)', 'NL2SQL_LIBRE'), ('(?:impay[eé]|non\\s+r[eé]gl[eé]).{0,40}clients?', 'NL2SQL_LIBRE'), ('quel\\s+client.{0,30}(?:plus\\s+gros|plus\\s+grand|meilleur|plus\\s+haut|maximum|encours|ca\\b|chiffre\\s+d.affaires)', 'NL2SQL_LIBRE'), ('liste\\s+(tous\\s+)?(les\\s+|des\\s+)?clients?\\s*$', 'LISTE_CLIENTS'), ('(tous|toutes)\\s+(les\\s+)?clients?\\s*$', 'LISTE_CLIENTS'), ('affiche\\s+(les\\s+)?clients?\\s*$', 'LISTE_CLIENTS'), ('montre\\s+(moi\\s+)?(les\\s+)?clients?\\s*$', 'LISTE_CLIENTS'), ('donne\\s+(moi\\s+)?(les\\s+)?clients?\\s*$', 'LISTE_CLIENTS'), ('clients?\\s+actifs?\\s*$', 'LISTE_CLIENTS'), ('top\\s*\\d*\\s*clients?', 'TOP_CLIENTS'), ('meilleurs?\\s+clients?', 'TOP_CLIENTS'), ('clients?\\s+(par\\s+)?ca\\b', 'TOP_CLIENTS'), ("fiche\\s+(du\\s+|de\\s+|d['\\u2019]?\\s*)?client", 'FICHE_CLIENT'), ('info\\w*\\s+(sur\\s+)?(le\\s+)?client', 'FICHE_CLIENT'), ('d[eé]tail\\s+(du\\s+)?client', 'FICHE_CLIENT'), ('profil\\s+(du\\s+)?client', 'FICHE_CLIENT'), ('statut\\s+(du\\s+|de\\s+)?client', 'STATUT_CLIENT'), ('client\\s+est.il\\s+bloqu[eé]', 'STATUT_CLIENT'), ('produits?\\s+finis?|articles?\\s+finis?', 'NL2SQL_LIBRE'), ('mati[èe]res?\\s+premi[eè]res?|mati[èe]re\\s+premi[eè]re', 'NL2SQL_LIBRE'), ("prix\\s+de\\s+(?:l['\\u2019]article\\s+)?[A-Za-z0-9\\-]+", 'VERIFIER_STOCK'), ('liste\\s+(tous\\s+)?(les\\s+)?articles?', 'LISTE_ARTICLES'), ('(tous|toutes)\\s+(les\\s+)?articles?', 'LISTE_ARTICLES'), ('catalogue\\s*(articles?|produits?)?', 'LISTE_ARTICLES'), ('tous\\s+(les\\s+)?produits?', 'LISTE_ARTICLES'), ('affiche\\s+(les\\s+)?articles?', 'LISTE_ARTICLES'), ('liste\\s+(les\\s+)?produits?', 'LISTE_ARTICLES'), ('articles?\\s+en\\s+rupture', 'VERIFIER_STOCK'), ('rupture\\s+de\\s+stock', 'VERIFIER_STOCK'), ("stock\\s+(?:disponible|actuel|restant)\\s+de\\s+l['\\u2019]article", 'VERIFIER_STOCK'), ("stock\\s+de\\s+l['\\u2019]article", 'VERIFIER_STOCK'), ('quel\\s+est\\s+le\\s+stock', 'VERIFIER_STOCK'), ('stock\\s+(?:disponible|actuel|restant)', 'VERIFIER_STOCK'), ('combien\\s+(?:de\\s+)?stock', 'VERIFIER_STOCK'), ('anomalies?\\s+.{0,20}stocks?', 'NL2SQL_LIBRE'), ('stock\\s+n[eé]gatif', 'NL2SQL_LIBRE'), ("clients?.{0,50}n['\\u2019]ont\\s+pas\\s+command[eé]", 'CLIENTS_INACTIFS'), ('clients?.{0,30}(?:pas\\s+command[eé]|pas\\s+achet[eé]).{0,30}(?:depuis|\\d+\\s+mois)', 'CLIENTS_INACTIFS'), ('quels?\\s+clients?.{0,50}(?:depuis\\s+\\d+|depuis\\s+(?:un|une|deux|trois|\\d+)\\s+mois)', 'CLIENTS_INACTIFS'), ('clients?.{0,20}inactifs?.{0,20}(?:depuis|mois|\\d+)', 'CLIENTS_INACTIFS'), ('ca\\s+(par\\s+)?mois', 'SAISONNALITE'), ('ca\\s+mensuel', 'SAISONNALITE'), ('chiffre\\s+d.affaires?\\s+(par\\s+)?mois', 'SAISONNALITE'), ('factures?\\s+(non\\s+r[eé]gl[eé]es?|impay[eé]es?|en\\s+attente).{0,30}fournisseur', 'FACTURES_NON_REGLEES_FOURN'), ('fournisseur.{0,30}factures?\\s+(non\\s+r[eé]gl[eé]es?|impay[eé]es?|en\\s+attente)', 'FACTURES_NON_REGLEES_FOURN'), ('impay[eé]es?.{0,20}fournisseur', 'FACTURES_NON_REGLEES_FOURN'), ('fournisseur.{0,20}impay[eé]es?', 'FACTURES_NON_REGLEES_FOURN'), ('achats?\\s+(non\\s+r[eé]gl[eé]s?|impay[eé]s?)', 'FACTURES_NON_REGLEES_FOURN'), ('factures?\\s+(non\\s+r[eé]gl|impay|en\\s+attente)', 'FACTURES_NON_REGLEES'), ('(impay[eé]es?|non\\s+r[eé]gl[eé]es?)', 'FACTURES_NON_REGLEES'), ('listes?\\s+(toutes?\\s+)?(des\\s+|les\\s+)?factures?(?:\\s+compl[eè]tes?)?\\s*$', 'LISTE_FACTURES'), ('(?:affiche|montre|donne)\\s+(toutes?\\s+)?(des\\s+|les\\s+)?factures?(?:\\s+compl[eè]tes?)?$', 'LISTE_FACTURES'), ('toutes?\\s+(des\\s+|les\\s+)?factures?(?:\\s+compl[eè]tes?)?$', 'LISTE_FACTURES'), ("listes?\\s+(des\\s+|les\\s+)?factures?\\s+d[\\s']un\\s+fournisseur\\s+pr[eé]cis", 'NL2SQL_LIBRE'), ('toutes?\\s+les?\\s+factures?\\s+(du\\s+|de\\s+)?fournisseur', 'NL2SQL_LIBRE'), ('factures?\\s+(du\\s+|de\\s+)?fournisseur', 'NL2SQL_LIBRE'), ('toutes?\\s+les?\\s+factures?\\s+(du\\s+|de\\s+)?client', 'TOUTES_FACTURES_CLIENT'), ('factures?\\s+du\\s+client', 'TOUTES_FACTURES_CLIENT'), ('(d[eé]lai|dso|retard)\\s+(de\\s+)?paiement', 'DSO'), ('\\bdso\\b', 'DSO'), ('\\brfm\\b', 'RFM'), ('analyse\\s+rfm', 'RFM'), ('segmentation\\s+clients?', 'RFM'), ('d[eé]claration\\s*(fiscale|tva|mensuelle)?', 'DECLARATION_EXCEL'), ('(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\\w*|exporte?(?:r|z)?)\\s+.{0,15}d[eé]claration', 'DECLARATION_EXCEL'), ('tableau\\s+de\\s+bord', 'DASHBOARD_EXCEL'), ('\\bdashboard\\b', 'DASHBOARD_EXCEL'), ('\\bkpi\\b', 'DASHBOARD_EXCEL'), ('r[eé]sum[eé]\\s+(g[eé]n[eé]ral|global)?', 'DASHBOARD_EXCEL'), ('palm[aà]r[eè]s', 'PALMARES_ARTICLES'), ('articles?\\s+les?\\s+plus?\\s+vendus?', 'PALMARES_ARTICLES'), ('meilleurs?\\s+articles?', 'PALMARES_ARTICLES'), ('marge\\s+(brute\\s+)?par\\s+article', 'RENTABILITE'), ('rentabilit[eé]\\s+(des?\\s+)?articles?', 'RENTABILITE'), ('taux\\s+de\\s+marge', 'RENTABILITE'), ('clients?\\s+en\\s+baisse', 'CLIENTS_BAISSE'), ('clients?\\s+baisse\\s+ca', 'CLIENTS_BAISSE'), ('documents?\\s+entre\\s+\\d{4}', 'DOCS_PERIODE'), ('documents?\\s+du\\s+\\d{4}', 'DOCS_PERIODE'), ('factures?\\s+du\\s+mois\\s+d.{1,10}(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)', 'DOCS_PERIODE'), ('(?:liste[s]?|affiche|montre|donne|quels?|tous?|toutes?)\\s+.{0,30}(?:bons?\\s+de\\s+livraison|bons?\\s+de\\s+fabrication)(?!\\s+(?:pour|client|cli))', 'NL2SQL_LIBRE'), ('bon\\s+de\\s+livraison', 'GENERER_DOC'), ('bon\\s+de\\s+fabrication', 'GENERER_DOC')]
_MARQUEURS_NL2SQL_FORCE_RE = [re.compile('\\b' + re.escape(m) + '\\b', re.IGNORECASE) for m in _MARQUEURS_NL2SQL_FORCE]
_RX_ARTICLES_VENDUS_PERIODE = re.compile('(articles?\\s+les?\\s+plus?\\s+vendus?|meilleurs?\\s+articles?|palmar[eè]s).{0,40}(ce\\s+mois|cette\\s+semaine|cette\\s+ann[eé]e|en\\s+\\d{4}|du\\s+mois)|(ce\\s+mois|cette\\s+semaine|cette\\s+ann[eé]e|en\\s+\\d{4}|du\\s+mois).{0,40}(articles?\\s+les?\\s+plus?\\s+vendus?|meilleurs?\\s+articles?|palmar[eè]s)', re.IGNORECASE)
_RX_ARTICLES_QUALIFIES = re.compile('articles?\\s+(?:jamais\\s+vendus?|sans\\s+ventes?|non\\s+vendus?|les\\s+plus|les\\s+moins|dont|avec|command[eé]s?\\s+par|achet[eé]s?\\s+par)', re.IGNORECASE)
_RX_CLIENT_AVEC_FILTRE_ARTICLE = re.compile('articles?\\s+.{0,15}command[eé]s?\\s+par\\s+le\\s+client', re.IGNORECASE)
_RX_CA_AVEC_PERIODE = re.compile('(chiffre\\s+d.affaires?|\\bca\\b|\\bfactures?\\b).{0,60}(mois\\s+dernier|semaine\\s+derni[eè]re|ann[eé]e\\s+derni[eè]re|cette\\s+semaine|ce\\s+mois|cette\\s+ann[eé]e|trimestre|semestre|compar[eé]|par\\s+rapport|\\bvs\\b|entre\\s+\\d{4}-\\d{2}-\\d{2})|(mois\\s+dernier|semaine\\s+derni[eè]re|ann[eé]e\\s+derni[eè]re|cette\\s+semaine|ce\\s+mois|cette\\s+ann[eé]e|trimestre|semestre|compar[eé]|par\\s+rapport).{0,60}(chiffre\\s+d.affaires?|\\bca\\b|\\bfactures?\\b)', re.IGNORECASE)
_RX_CA_AVEC_PERIODE = re.compile('(chiffre\\s+d.affaires?|\\bca\\b).{0,60}(mois\\s+dernier|semaine\\s+derni[eè]re|ann[eé]e\\s+derni[eè]re|cette\\s+semaine|ce\\s+mois|cette\\s+ann[eé]e|trimestre|semestre|compar[eé]|par\\s+rapport|\\bvs\\b|il\\s+y\\s+a\\s+\\d+\\s+semaines?|derniers?\\s+\\d+\\s+mois|derni[eè]res?\\s+ann[eé]es?|[eé]volution)|(mois\\s+dernier|semaine\\s+derni[eè]re|ann[eé]e\\s+derni[eè]re|cette\\s+semaine|ce\\s+mois|cette\\s+ann[eé]e|trimestre|semestre|compar[eé]|par\\s+rapport|[eé]volution).{0,60}(chiffre\\s+d.affaires?|\\bca\\b)', re.IGNORECASE)
_RX_ARTICLES_MOINS_VENDUS = re.compile('moins\\s+vendus?|les?\\s+moins\\s+bonnes?\\s+ventes?', re.IGNORECASE)

def _pre_classifier(question: str) -> str | None:
    """
Rôle de la fonction `_pre_classifier`\xa0: Cette fonction effectue un classement préliminaire d'une question en fonction de régularités de syntaxe ou de mots-clés.
"""
    q = question.lower().strip()
    if _RX_ARTICLES_VENDUS_PERIODE.search(q):
        return 'NL2SQL_LIBRE'
    if _RX_CA_AVEC_PERIODE.search(q):
        return 'NL2SQL_LIBRE'
    if _RX_ARTICLES_QUALIFIES.search(q) or _RX_CLIENT_AVEC_FILTRE_ARTICLE.search(q):
        return 'NL2SQL_LIBRE'
    if _RX_CA_AVEC_PERIODE.search(q):
        return 'NL2SQL_LIBRE'
    if _RX_ARTICLES_MOINS_VENDUS.search(q):
        return 'NL2SQL_LIBRE'
    if re.search('(?:moyenne|montant|total)\\s+(?:du\\s+montant\\s+)?(?:des?\\s+)?factures?\\s+(?:par\\s+|du\\s+)?(?:trimestre|semestre|mois|ann[eé]e)', q, re.IGNORECASE):
        return 'NL2SQL_LIBRE'
    if any((p.search(q) for p in _MARQUEURS_NL2SQL_FORCE_RE)):
        return 'NL2SQL_LIBRE'
    for pattern, action in _PATTERNS_PRECLASS:
        if re.search(pattern, q, re.IGNORECASE):
            print(f'   ⚡ [PreClass] {action} (regex, 0ms)')
            return action
    if re.search("chiffre d['’ ]affaires (total|global)", q, re.IGNORECASE):
        return 'CA_GLOBAL'
    if any((p.search(q) for p in _MARQUEURS_NL2SQL_FORCE_RE)):
        return 'NL2SQL_LIBRE'
    return None

def _est_action_pdf(texte: str) -> bool:
    """
Vérifie si un texte contient des mots-clés liés à l'ouverture ou à la lecture de fichiers PDF.
"""
    q = (texte or '').strip().lower()
    return any((kw in q for kw in ['ouvrir pdf', 'voir pdf', 'pdf', 'télécharger pdf', 'telecharger pdf', 'afficher pdf', 'open pdf']))

class CopilotState(TypedDict):
    """
Cette classe définit le type de données pour un état de copilote.
"""
    demande_brute: str
    intention: str
    action: str
    ambigue: bool
    score_confiance: float
    code_client: str
    code_fournisseur: str
    ref_article: str
    quantite: float
    seuil_jours_impaye: float
    num_piece: str
    type_doc: str
    type_doc_code: int
    date_debut: str
    date_fin: str
    mode_paiement: str
    validation_ok: bool
    hub_validation: str
    reponse_brute: str
    rag_complement: str
    reponse_finale: str
    hallucination_flag: bool
    mem0_contexte: str
    dernier_type_doc: str
    dernier_num_piece: str
    dernier_code_client: str
    dernier_ref_article: str
    dernier_quantite: float
    plan_execution: list
    etape_courante: int
    nom_client_brut: str
    suggestion_en_attente: dict
    pending_action: dict
    pending_document: dict
    attente_complements: bool
    document_draft: dict
    statut_draft: str
    pdf_path: str
    num_of_resolu: str
    dernier_action_classifiee: str
    derniere_question_classifiee: str
    statut_confirmation: str
    ct_validite: str
    numero_piece_paiement: str
    modification_en_cours: dict
    attente_confirmation: bool
    draft_status: str
    action_buttons: list
    suggestions: list
    intitule: str
    adresse: str
    complement: str
    code_postal: str
    ville: str
    pays: str
    contact: str
    telephone: str
    email: str
    site: str
    creation_article_en_cours: dict
    nomenclature_en_cours: dict
    modification_nomenclature_en_cours: dict

def _etat_initial(demande: str, contexte_session: dict | None=None) -> CopilotState:
    """
Cette fonction retourne un objet CopilotState contenant les informations nécessaires pour lancer la copilot, en fonction d'une demande et d'un contexte de session.
"""
    ctx = contexte_session or {}
    dd = ctx.get('dernier_document', {})
    _dernier_num = ctx.get('dernier_num_piece', '') or dd.get('num_piece', '')
    _dernier_type = ctx.get('dernier_type_doc', '') or dd.get('type_doc', '')
    return CopilotState(demande_brute=demande, intention='', action='', ambigue=False, score_confiance=1.0, code_client=ctx.get('code_client', ''), code_fournisseur=ctx.get('code_fournisseur', ''), ref_article='', quantite=0.0, seuil_jours_impaye=0.0, num_piece='', type_doc='', type_doc_code=0, date_debut='', date_fin='', mode_paiement='Virement', validation_ok=False, hub_validation='', reponse_brute='', rag_complement='', reponse_finale='', hallucination_flag=False, mem0_contexte='', dernier_type_doc=_dernier_type, dernier_num_piece=_dernier_num, dernier_code_client=ctx.get('dernier_code_client', ''), dernier_ref_article=ctx.get('dernier_ref_article', ''), dernier_quantite=ctx.get('dernier_quantite', 0.0), plan_execution=[], etape_courante=0, nom_client_brut=ctx.get('dernier_nom_client', ''), suggestion_en_attente={}, pending_action=ctx.get('pending_action', {}), document_draft={}, statut_draft='', pdf_path='', pending_document=ctx.get('pending_document', {}), attente_complements=False, ct_validite=ctx.get('ct_validite', 'VALIDE'), num_of_resolu='', dernier_action_classifiee=ctx.get('dernier_action_classifiee', ''), derniere_question_classifiee=ctx.get('derniere_question_classifiee', ''), statut_confirmation=ctx.get('statut_confirmation', ''), numero_piece_paiement='', modification_en_cours=ctx.get('modification_en_cours', {}), attente_confirmation=ctx.get('attente_confirmation', False), draft_status='', action_buttons=[], suggestions=[], intitule=ctx.get('intitule', ''), adresse=ctx.get('adresse', ''), complement=ctx.get('complement', ''), code_postal=ctx.get('code_postal', ''), ville=ctx.get('ville', ''), pays=ctx.get('pays', ''), contact=ctx.get('contact', ''), telephone=ctx.get('telephone', ''), email=ctx.get('email', ''), site=ctx.get('site', ''), creation_article_en_cours=ctx.get('creation_article_en_cours', {}), nomenclature_en_cours=ctx.get('nomenclature_en_cours', {}), modification_nomenclature_en_cours=ctx.get('modification_nomenclature_en_cours', {}))

def verifier_document_incomplet(state):
    """
Vérifie si un document incomplet a été soumis en fonction de son type de document.
"""
    doc = state.get('pending_document', {})
    type_doc = doc.get('type_doc')
    if type_doc in ('BL_ACHAT', 'FA_ACHAT'):
        champs = ['code_fournisseur', 'ref_article', 'quantite', 'prix_unitaire']
    elif type_doc == 'BL':
        champs = ['code_client', 'ref_article', 'quantite']
    elif type_doc == 'CLIENT_CREATION':
        champs = ['nom_client_brut', 'intitule', 'ct_validite', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site']
    elif type_doc == 'DOCS_PERIODE_INFOS':
        champs = ['date_debut', 'date_fin']
    elif type_doc == 'FOURNISSEUR_CREATION':
        champs = ['nom_client_brut', 'intitule', 'ct_validite', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site']
    elif type_doc == 'REGLEMENT_INFOS':
        champs = ['mode_paiement']
        _mode_actuel = str(doc.get('mode_paiement') or '').strip().capitalize()
        if _mode_actuel in ('Cheque', 'Traite'):
            champs.append('numero_piece_paiement')
    else:
        return None
    champs_saisis = doc.get('_champs_saisis', set())
    return [c for c in champs if c not in champs_saisis and (not doc.get(c)) and (doc.get(c) != 0)]

async def noeud_complements(state):
    """
Fonction qui gère les compléments manquants d'un document en fonction de l'état donné.
"""
    manquants = verifier_document_incomplet(state)
    if not manquants:
        state['attente_complements'] = False
        return state
    champ = manquants[0]
    questions = {'code_fournisseur': 'Quel fournisseur ?', 'code_client': 'Quel client ?', 'ref_article': 'Quelle référence article ?', 'quantite': 'Quelle quantité ?', 'prix_unitaire': 'Quel prix unitaire ?', 'nom_client_brut': 'Quel est le nom à créer ?', 'intitule': 'Quelle est la raison sociale ?', 'ct_validite': 'Quel statut pour ce client ? (VALIDE / SUSPECT / BLOQUE)', 'adresse': "Quelle est l'adresse postale ?", 'complement': "Complément d'adresse (si applicable) ?", 'code_postal': 'Quel est le code postal ?', 'ville': 'Quelle est la ville ?', 'pays': 'Quel est le pays ?', 'contact': 'Qui est le contact principal ?', 'telephone': 'Quel est le numéro de téléphone ?', 'email': "Quelle est l'adresse e-mail ?", 'site': 'Quel est le site web (si applicable) ?', 'mode_paiement': 'Quel mode de paiement ? (Virement / Cheque / Traite / Especes / CB)', 'numero_piece_paiement': 'Quel est le numéro du chèque / de la traite ?'}
    state['attente_complements'] = True
    state['reponse_finale'] = questions[champ]
    return state
_VERBES_NOUVELLE_DEMANDE = re.compile('^(?:cr[eé]e[rz]?|modifie[rz]?|liste[rz]?|affiche[rz]?|montre[rz]?|donne[rz]?|g[eé]n[eé]r\\w*|transforme[rz]?|r[eè]gle[rz]?|annule[rz]?|stop|quitter)\\b', re.IGNORECASE)

async def _corriger_ref_article(ref: str) -> str:
    """
Cette fonction permet de corriger une référence d'article en utilisant la pool de travail MCP.
"""
    return await _corriger_ref_article_impl(ref, mcp_pool)

async def _rechercher_client_par_nom(nom: str) -> str:
    """
Récupérer le client dont le nom est passé en argument.
"""
    return await _rechercher_client_par_nom_impl(nom, mcp_pool)
_VALEURS_VIDES = {'', '.', '-', '--', 'n/a', 'na', 'none', 'aucun', 'aucune'}

def _est_reponse_vide(texte: str) -> bool:
    """
Fonction qui vérifie si un texte est vide ou contient des valeurs prédéfinies comme vide.
"""
    return str(texte).strip().lower() in _VALEURS_VIDES

async def _verifier_nom_tiers_existe_mcp(nom: str, type_tiers: int) -> bool:
    """
Vérifie si un nom de tiers existe dans la base de données MCP.
"""
    if not nom:
        return False
    try:
        raw = await mcp_pool.call('actions', 'verifier_nom_tiers_existe', {'intitule': nom, 'type_tiers': type_tiers})
        return bool(_parse_mcp_response(raw).get('existe'))
    except Exception as e:
        print(f'⚠️ [Vérif nom tiers] {e}')
        return False

async def injecter_complement(state):
    """
Injecte des données complémentaires dans l'état d'une demande de document.
"""
    if not state.get('attente_complements'):
        return state
    m_dates2 = None
    texte = state['demande_brute'].strip()
    if _VERBES_NOUVELLE_DEMANDE.match(texte):
        state['attente_complements'] = False
        state['pending_document'] = {}
        return state
    texte = re.sub('^(?:nom|client|fournisseur|intitul[eé])\\s*[:=]\\s*', '', texte, flags=re.IGNORECASE).strip()
    doc = state.get('pending_document', {})
    if not doc.get('prix_unitaire') and doc.get('type_doc') == 'BL_ACHAT':
        try:
            doc['prix_unitaire'] = float(texte.replace(',', '.'))
        except Exception:
            pass
    elif not doc.get('quantite') and doc.get('type_doc') in ('BL_ACHAT', 'BL'):
        m = re.search('(\\d+(?:[.,]\\d+)?)', texte)
        if m:
            doc['quantite'] = float(m.group(1).replace(',', '.'))
    elif not doc.get('ref_article') and doc.get('type_doc') in ('BL_ACHAT', 'BL'):
        doc['ref_article'] = texte
    elif not doc.get('code_fournisseur') and doc.get('type_doc') in ('BL_ACHAT', 'FA_ACHAT'):
        err = await _verifier_fournisseur_draft(texte)
        if err:
            state['attente_complements'] = False
            state['pending_document'] = {}
            state['reponse_finale'] = err
            return state
        doc['code_fournisseur'] = texte
    elif not doc.get('code_client') and doc.get('type_doc') == 'BL':
        err = await _verifier_client_draft(texte)
        if err:
            state['attente_complements'] = False
            state['pending_document'] = {}
            state['reponse_finale'] = err
            return state
        doc['code_client'] = texte
    elif 'nom_client_brut' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('nom_client_brut')
        t = texte.strip()
        if _est_reponse_vide(t):
            doc['nom_client_brut'] = ''
        else:
            type_tiers = 0 if doc['type_doc'] == 'CLIENT_CREATION' else 1
            if await _verifier_nom_tiers_existe_mcp(t, type_tiers):
                state['attente_complements'] = True
                state['pending_document'] = {}
                state['reponse_finale'] = f"❌ '{val_intitule}' existe déjà. Impossible de le recréer."
                return state
            doc['nom_client_brut'] = t
    elif 'intitule' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('intitule')
        val_intitule = '' if _est_reponse_vide(texte) else texte.strip()
        if val_intitule:
            type_tiers = 0 if doc['type_doc'] == 'CLIENT_CREATION' else 1
            if await _verifier_nom_tiers_existe_mcp(val_intitule, type_tiers):
                state['attente_complements'] = False
                state['pending_document'] = {}
                state['_creation_annulee'] = True
                state['reponse_finale'] = f"❌ '{val_intitule}' existe déjà. Impossible de le recréer."
                return state
        doc['intitule'] = val_intitule
    elif 'ct_validite' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('ct_validite')
        v = texte.strip().upper()
        doc['ct_validite'] = v if v in ('VALIDE', 'SUSPECT', 'BLOQUE') else 'VALIDE'
    elif 'adresse' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('adresse')
        doc['adresse'] = '' if _est_reponse_vide(texte) else texte.strip()
    elif 'complement' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('complement')
        doc['complement'] = '' if _est_reponse_vide(texte) else texte.strip()
    elif 'code_postal' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('code_postal')
        m = re.search('(\\d{2,10})', texte)
        doc['code_postal'] = m.group(1) if m else '' if _est_reponse_vide(texte) else texte.strip()
    elif 'ville' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('ville')
        doc['ville'] = '' if _est_reponse_vide(texte) else texte.strip()
    elif 'pays' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('pays')
        doc['pays'] = '' if _est_reponse_vide(texte) else texte.strip()
    elif 'contact' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('contact')
        doc['contact'] = '' if _est_reponse_vide(texte) else texte.strip()
    elif 'telephone' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('telephone')
        m = re.search('(\\+?\\d[\\d\\s\\-().]{4,}\\d)', texte)
        doc['telephone'] = m.group(1).strip() if m else '' if _est_reponse_vide(texte) else texte.strip()
    elif 'email' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('email')
        m = re.search('[\\w\\.-]+@[\\w\\.-]+\\.\\w+', texte)
        doc['email'] = m.group(0) if m else '' if _est_reponse_vide(texte) else texte.strip()
    elif 'site' not in doc.get('_champs_saisis', set()) and doc.get('type_doc') in ('CLIENT_CREATION', 'FOURNISSEUR_CREATION'):
        doc.setdefault('_champs_saisis', set()).add('site')
        doc['site'] = '' if _est_reponse_vide(texte) else texte.strip()
    elif not doc.get('mode_paiement') and doc.get('type_doc') == 'REGLEMENT_INFOS':
        t = texte.strip().lower()
        _MODE_MAP = {'cheque': 'Cheque', 'chèque': 'Cheque', 'virement': 'Virement', 'especes': 'Especes', 'espèces': 'Especes', 'cb': 'CB', 'carte': 'CB', 'traite': 'Traite'}
        mode_trouve = next((v for k, v in _MODE_MAP.items() if k in t), None)
        doc['mode_paiement'] = mode_trouve or texte.strip().capitalize()
    elif doc.get('type_doc') == 'REGLEMENT_INFOS' and doc.get('mode_paiement') in ('Cheque', 'Traite') and (not doc.get('numero_piece_paiement')):
        doc['numero_piece_paiement'] = texte.strip()
    elif doc.get('type_doc') == 'DOCS_PERIODE_INFOS' and (not (doc.get('date_debut') and doc.get('date_fin'))):
        m_dates2 = re.search('(\\d{4}-\\d{2}-\\d{2})\\s*(?:et|au|,|-)?\\s*(\\d{4}-\\d{2}-\\d{2})?', texte)
    m_annee = re.search('\\b(20\\d{2})\\b', texte)
    if m_dates2 and m_dates2.group(2):
        doc['date_debut'] = m_dates2.group(1)
        doc['date_fin'] = m_dates2.group(2)
    elif m_annee:
        annee = m_annee.group(1)
        demande_orig = doc.get('demande_originale', '')
        m_jours = re.search('(\\d{1,2})(?:er)?\\s+(?:et|au)\\s+(?:le\\s+)?(\\d{1,2})\\s+(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)', demande_orig, re.IGNORECASE)
        _MOIS_NUM = {'janvier': 1, 'février': 2, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'août': 8, 'aout': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12, 'decembre': 12}
        if m_jours:
            j1, j2, mois_nom = m_jours.groups()
            mnum = _MOIS_NUM.get(mois_nom.lower(), 1)
            doc['date_debut'] = f'{annee}-{mnum:02d}-{int(j1):02d}'
            doc['date_fin'] = f'{annee}-{mnum:02d}-{int(j2):02d}'
    doc['_champs_saisis'] = doc.get('_champs_saisis', set()) | {'date_debut', 'date_fin'} if doc.get('date_debut') and doc.get('date_fin') else doc.get('_champs_saisis', set())
    state['pending_document'] = doc
    return state

def _fusionner_demandes(precedente: str, complement: str) -> str:
    """
Cette fonction permet de fusionner deux demandes en une seule, en les séparant clairement les unes des autres.
"""
    return f"Demande initiale : {precedente}\nPrécision apportée par l'utilisateur : {complement}"

async def _arbitrer_semantique_llm(question, decision_sem, action_llm):
    """
Cette fonction arbitre la décision d'un modèle LLM par rapport à une décision sémantique, retournant la décision du LLM si elle correspond ou si la décision sémantique est nulle.
"""
    if decision_sem is None:
        return action_llm
    if action_llm == decision_sem.action:
        return action_llm
    return None

async def _verifier_rag(question):
    """
Vérifie l'existence et le type d'un document KB (Knowledge Base) en fonction de la question donnée.
"""
    try:
        texte = await mcp_pool.call('kb', 'classifier_document', {'requete': question})
        import json
        doc = json.loads(texte)
        if doc.get('score', 0) >= 0.82:
            dt = doc.get('doc_type')
            if dt in ('relance_commerciale', 'recouvrement'):
                return 'RECOMMANDATION'
            if dt in ('procedure', 'fiche_article', 'reclamation_sav'):
                return 'RECHERCHE_PROCEDURE'
    except Exception as e:
        print(f'   ⚠️  [RAG Check] {e}')
    return None
_RX_PIECE_HEX = '\\b((?:FA|BL|BC|BF|FF|AV|BR|AF)[0-9A-F]{5,9})\\b'
_RX_PIECE_DOC = re.compile('\\b((?:OF|BL|BC|BF|FA|BR)[0-9A-F]{5,9})\\b', re.IGNORECASE)
_RX_PIECES_REGLEMENT = re.compile(_RX_PIECE_HEX, re.IGNORECASE)

def _extraire_dernier_document(final_state: dict) -> dict | None:
    """
Extraire les informations d'un document financier à partir d'un état final.
"""
    if final_state.get('action') != 'GENERER_DOC':
        return None
    if not final_state.get('validation_ok', True):
        return None
    rb = final_state.get('reponse_brute', '') or ''
    try:
        data = json.loads(rb)
        if data.get('DO_Piece'):
            return {'type_doc': final_state.get('type_doc') or 'BL', 'num_piece': data['DO_Piece'], 'code_client': data.get('DO_Tiers', final_state.get('code_client', '')), 'ref_article': data.get('AR_Ref', final_state.get('ref_article', ''))}
    except Exception:
        pass
    m = _RX_PIECE_DOC.search(rb)
    if not m:
        return None
    return {'type_doc': final_state.get('type_doc') or 'BL', 'num_piece': m.group(1), 'code_client': final_state.get('code_client', ''), 'ref_article': final_state.get('ref_article', '')}
_VERBES_ACTION_MULTI = 'cr[eé]e(?:r|z)?|g[ée]n[ée]r\\w*|transforme(?:r|z)?|r[eè]gle(?:r|z)?|lance(?:r|z)?|fai(?:s|t|re)|liste(?:r|z)?|affiche(?:r|z)?|montre(?:r|z)?|v[eé]rifie(?:r|z)?|cherche(?:r|z)?|recherche(?:r|z)?|confirme(?:r|z)?|envoie(?:r|z)?|supprime(?:r|z)?|exporte(?:r|z)?|[eé]dite(?:r|z)?|ajoute(?:r|z)?|calcule(?:r|z)?|dis|dire|indique(?:r|z)?|pr[eé]cise(?:r|z)?|donne(?:r|z)?'
_NOMS_INFO_MULTI = 'stocks?|caract[eé]ristiques?|prix|tarifs?|garanties?|tol[eé]rances?|r[eé]clamations?|disponibilit[eé]s?|d[eé]lais?|encours|statuts?|conditions?'
_CONNECTEURS = re.compile(f'\\bpuis\\b|\\bensuite\\b|\\baprès\\b|\\bet\\s+(?=(?:{_VERBES_ACTION_MULTI}))|\\bet\\s+(?=(?:les?\\s+|la\\s+|des\\s+)?(?:{_NOMS_INFO_MULTI})\\b)|\\bet\\s+aussi\\b|\\bde\\s+plus\\b|\\bégalement\\b|,\\s+(?=(?:{_VERBES_ACTION_MULTI}))', re.IGNORECASE)
_PHRASES_SPLIT_RE = re.compile('(?<=[.?!])\\s+')
_VERBE_DEBUT_PHRASE_RE = re.compile('^(?:' + _VERBES_ACTION_MULTI + '|quels?|quelles?|combien|qui|quel\\s+est|quelle\\s+est|est[\\s-]ce)\\b', re.IGNORECASE)

def _contient_multi_demandes(texte: str) -> bool:
    """
Vérifie si un texte contient au moins deux demandes ou deux phrases séparées par un verbe.
"""
    if _CONNECTEURS.search(texte):
        return True
    if texte.count('?') >= 2:
        return True
    phrases = [p.strip() for p in _PHRASES_SPLIT_RE.split(texte) if p.strip()]
    if len(phrases) >= 2:
        nb_phrases_action = sum((1 for p in phrases if _VERBE_DEBUT_PHRASE_RE.match(p)))
        if nb_phrases_action >= 2:
            return True
    return False

async def decouper_demande_composite(demande: str) -> list[dict]:
    """
Cette fonction décompose une demande complexe en une liste d'actions atomiques.
"""
    if not _contient_multi_demandes(demande):
        return [{'demande': demande, 'sequentiel': False, 'index': 0}]
    prompt = f'''Tu es un expert ERP Sage 100.\nAnalyse ce message et découpe-le en actions atomiques.\nMessage : "{demande}"\nRÈGLES :\n1. Chaque action doit être complète\n2. Indique si l'action dépend du résultat de la précédente (sequentiel: true)\n3. Si une seule action → retourne un tableau avec un seul élément\nRéponds UNIQUEMENT avec ce JSON :\n[{{"demande": "texte complet action 1", "sequentiel": false}},\n {{"demande": "texte complet action 2", "sequentiel": true}}]'''
    try:
        texte = await invoke_llm_anonymise(prompt, _invoke_llm, use_smart=False)
        texte = texte.replace('```json', '').replace('```', '').strip()
        m = re.search('\\[.*\\]', texte, re.DOTALL)
        if not m:
            return [{'demande': demande, 'sequentiel': False, 'index': 0}]
        parsed = json.loads(m.group(0))
        if not isinstance(parsed, list) or not parsed:
            return [{'demande': demande, 'sequentiel': False, 'index': 0}]
        result = []
        for i, item in enumerate(parsed):
            if isinstance(item, str):
                result.append({'demande': item.strip(), 'sequentiel': False, 'index': i})
            elif isinstance(item, dict):
                d = item.get('demande', '').strip()
                if d:
                    result.append({'demande': d, 'sequentiel': bool(item.get('sequentiel', False)), 'index': i})
        if len(result) <= 1:
            return [{'demande': demande, 'sequentiel': False, 'index': 0}]
        return result
    except Exception:
        return [{'demande': demande, 'sequentiel': False, 'index': 0}]

def _decouper_reglement_multiple(demande: str) -> list[str] | None:
    """
Cette fonction découpe une demande de règlement en plusieurs pièces et les associe à un mode de paiement.
"""
    if not re.search('r[eé]gl|paye?r?|paiement', demande, re.IGNORECASE):
        return None
    pieces = list(dict.fromkeys((p.upper() for p in _RX_PIECES_REGLEMENT.findall(demande))))
    if len(pieces) <= 1:
        return None
    m_mode = re.search('(?:par\\s+)(chèque|cheque|virement|espèces?|especes?|cb|carte|traite)', demande, re.IGNORECASE)
    suffixe = f' par {m_mode.group(1)}' if m_mode else ''
    return [f'règle la facture {p}{suffixe}' for p in pieces]
_REFS_CONTEXTUELLES = re.compile('\\b(ce|cet|cette|celui-ci|celle-ci|ce\\s+bl|cette\\s+facture|ce\\s+document|le\\s+même|la\\s+même|ce\\s+of|ce\\s+bon|(?:son|sa|ses|leur|leurs)\\s+(?:encours|statut|stock|solde|chiffre|ca|historique|dernier|dernière|facture|commande|bl)|lui)\\b', re.IGNORECASE)

def _resoudre_references(demande: str, contexte_precedent: dict) -> str:
    """
Résoud les références au contexte d'une demande en fonction d'informations contextuelles fournies.
"""
    if not _REFS_CONTEXTUELLES.search(demande):
        return demande
    num_piece = contexte_precedent.get('num_piece', '')
    type_doc = contexte_precedent.get('type_doc', '')
    demande_resolue = demande
    if num_piece:
        demande_resolue = re.sub('\\b(ce|cet|cette|celui-ci|celle-ci|ce\\s+bl|cette\\s+facture|ce\\s+document|ce\\s+of)\\b', f'le {type_doc} {num_piece}', demande_resolue, flags=re.IGNORECASE)
    if demande_resolue != demande:
        print(f"   🔗 [Résolution] '{demande}' → '{demande_resolue}'")
    return demande_resolue

async def _hub_resoudre_type_doc(libelle: str) -> dict:
    """
Résout le type d'un document en fonction de son libellé et renvoie le résultat sous forme de dictionnaire.
"""
    if not libelle:
        return {}
    try:
        text = await mcp_pool.call('hub', 'resoudre_type_document', {'libelle': libelle})
        return json.loads(text)
    except Exception:
        return {}

async def _hub_valider_demande(type_action: str, payload: dict) -> dict:
    """
Cette fonction effectue une validation de demande métier sur un hub.
"""
    try:
        text = await asyncio.wait_for(mcp_pool.call('hub', 'valider_demande_metier', {'type_action': type_action, 'payload': json.dumps(payload, ensure_ascii=False)}), timeout=15.0)
        return json.loads(text)
    except asyncio.TimeoutError:
        return {'valide': False, 'message': '⚠️ Validation hub non disponible (timeout). Opération bloquée par précaution.'}
    except Exception as e:
        return {'valide': False, 'message': _safe_str(e)}

async def _hub_contexte_client(code_client: str, statut: str, stock_dispo: float, qte: float) -> dict:
    """
Crée un contexte client en appelant le service "hub" et renvoie un dictionnaire contenant les informations relatives au client.
"""
    try:
        text = await mcp_pool.call('hub', 'construire_contexte_client', {'code_client': code_client, 'statut': statut, 'stock_disponible': stock_dispo, 'quantite_demandee': qte})
        return json.loads(text)
    except Exception:
        return {'decision': 'VALIDER', 'alertes': [], 'pret_pour_livraison': True}
import difflib
_articles_refs_cache: list[str] | None = None
_articles_refs_cache: list[str] | None = None
_articles_refs_lock: asyncio.Lock | None = None

def _parse_mcp_response(raw: str | dict) -> dict:
    """
Convertit une réponse de type MCP (Message Commande Paramètres) en un dictionnaire Python.
"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {'statut': 'ERREUR', 'message': 'Réponse vide'}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {'statut': 'ERREUR', 'message': str(raw)}

async def _mcp_workflow_bl_achat(code_fournisseur: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None) -> dict:
    """
Fonction permettant d'appeler la workflow BL_ACHAT du serveur MCP en passant un payload de données.
"""
    payload = {'code_fournisseur': code_fournisseur, 'ref_article': ref_article, 'quantite': quantite, 'prix_unitaire': prix_unitaire}
    if date_doc:
        payload['date_doc'] = date_doc
    raw = await mcp_pool.call('actions', 'workflow_bl_achat', payload)
    return _parse_mcp_response(raw)

async def _mcp_workflow_facture(code_client: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None) -> dict:
    """
Crée et génère une facture pour le client avec le code spécifié, et la renvoie sous forme de dictionnaire.
"""
    payload = {'type_doc': 'FACTURE', 'code_client': code_client, 'ref_article': ref_article, 'qte': quantite, 'prix_unitaire': prix_unitaire}
    if date_doc:
        payload['date_doc'] = date_doc
    raw = await mcp_pool.call('actions', 'generer_document_sage', payload)
    return _parse_mcp_response(raw)

async def _mcp_workflow_bl(code_client: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None) -> dict:
    """
Définit le workflow de la commande de base (BL) pour un article spécifique d'un client.
"""
    payload = {'code_client': code_client, 'ref_article': ref_article, 'quantite': quantite, 'prix_unitaire': prix_unitaire}
    if date_doc:
        payload['date_doc'] = date_doc
    raw = await mcp_pool.call('actions', 'workflow_bl', payload)
    return _parse_mcp_response(raw)

async def _mcp_workflow_of(ref_article: str, quantite: float, code_client: str='PROD-INT') -> dict:
    """
Fonction asynchrone qui appelle le workflow de gestion de stocks en utilisant l'API de MCP.
"""
    raw = await mcp_pool.call('actions', 'workflow_of', {'ref_article': ref_article, 'quantite': quantite, 'code_client': code_client})
    return _parse_mcp_response(raw)

async def _mcp_workflow_bf(ref_article: str, quantite: float, num_of: str='', code_client: str='PROD-INT') -> dict:
    """
Rôle de cette fonction\xa0: exécuter un workflow «\xa0workflow_bf\xa0» via l'API MCP.

Rôle de cette fonction\xa0: récupérer des données résultant de l'exécution de ce workflow.
"""
    raw = await mcp_pool.call('actions', 'workflow_bf', {'ref_article': ref_article, 'quantite': quantite, 'num_of': num_of, 'code_client': code_client})
    return _parse_mcp_response(raw)

async def _mcp_transformer_document(num_piece_source: str, type_destination: str) -> dict:
    """
Cette fonction exécute une transformation de document à l'aide d'un service externe (MCP) et retourne le résultat sous forme de dictionnaire.
"""
    raw = await mcp_pool.call('actions', 'transformer_document', {'num_piece_source': num_piece_source, 'type_destination': type_destination})
    return _parse_mcp_response(raw)

async def _enrichir_draft_client_article(draft: dict) -> dict:
    """
Cette fonction enrichit un brouillon de client article en récupérant des informations supplémentaires via des appels à des bases de données.
"""
    code_client = draft.get('code_client', '')
    ref_article = draft.get('ref_article', '')
    if code_client and (not draft.get('intitule_client')):
        try:
            txt = await mcp_pool.call('nl2sql', 'rechercher_fiche_client', {'code_client': code_client})
            if txt and txt.strip():
                data = json.loads(txt)
                if data.get('CT_Intitule'):
                    draft['intitule_client'] = data['CT_Intitule']
        except Exception as e:
            print(f'   ⚠️  [Enrichissement] intitulé client : {_safe_str(e)}')
    if ref_article and (not draft.get('prix_unitaire')):
        try:
            txt = await mcp_pool.call('nl2sql', 'executer_sql_vanna', {'sql': f"SELECT AR_PrixVen FROM F_ARTICLE WHERE UPPER(AR_Ref)=UPPER('{ref_article}')", 'description': f'Prix de vente de {ref_article}'})
            if txt and txt.strip():
                data = json.loads(txt)
                rows = data.get('resultats') or data.get('rows') or []
                if rows and isinstance(rows, list):
                    prix = rows[0].get('AR_PrixVen')
                    if prix is not None:
                        draft['prix_unitaire'] = float(prix)
        except Exception as e:
            print(f'   ⚠️  [Enrichissement] prix article : {_safe_str(e)}')
    return draft

async def _resoudre_tiers_mcp(code_ou_nom: str) -> dict:
    """
Cette fonction permet d'obtenir les informations relatives à un tiers (client ou fournisseur) via son code ou son nom.
"""
    raw = await mcp_pool.call('actions', 'resoudre_tiers', {'code_ou_nom': code_ou_nom})
    return _parse_mcp_response(raw)

async def _verifier_client_draft(code_client: str) -> str:
    """
Vérifie le statut d'un client en fonction d'un code client fourni.
"""
    data = await _resoudre_tiers_mcp(code_client)
    if data.get('statut') != 'SUCCES' and code_client.upper() == 'PROD-INT':
        await mcp_pool.call('actions', 'assurer_tiers_interne', {'code_client': code_client})
        data = await _resoudre_tiers_mcp(code_client)
    if data.get('statut') != 'SUCCES':
        return f"🚫 Client **'{code_client}'** introuvable dans la base. Vérifiez le code et réessayez."
    if code_client.upper() == 'PROD-INT':
        return ''
    if data.get('CT_Type') != 0:
        return f"🚫 **'{code_client}'** existe mais n'est pas un client (c'est un fournisseur). Vérifiez le code et réessayez."
    statut_cl = str(data.get('CT_Sommeil') or 0).upper()
    if statut_cl in ('1', 'BLOQUE', 'SOMMEIL', 'TRUE'):
        return f"🚫 Client **{data.get('CT_Intitule', code_client)}** ({code_client}) est **BLOQUÉ**. Impossible de continuer.\nContactez le service comptabilité."
    return ''

async def _verifier_fournisseur_draft(code_fournisseur: str) -> str:
    """
Cette fonction vérifie si un fournisseur avec le code spécifié existe dans la base de données et est bien un fournisseur.
"""
    data = await _resoudre_tiers_mcp(code_fournisseur)
    if data.get('statut') != 'SUCCES':
        return f"🚫 Fournisseur **'{code_fournisseur}'** introuvable dans la base. Vérifiez le code et réessayez."
    if data.get('CT_Type') != 1:
        return f"🚫 **'{code_fournisseur}'** existe mais n'est pas un fournisseur (c'est un client). Vérifiez le code et réessayez."
    return ''

async def _verifier_client_des_saisie(draft: dict) -> tuple[bool, str]:
    """
Vérifie si les codes client et fournisseur sont corrects dans un brouillon de saisie.
"""
    for champ in ('code_client', 'code_fournisseur'):
        code = (draft or {}).get(champ)
        if not code:
            continue
        if champ == 'code_client':
            err = await _verifier_client_draft(code)
        else:
            err = await _verifier_fournisseur_draft(code)
        if err:
            return (False, err)
    return (True, '')

async def _lire_article_draft(ref_article: str) -> tuple[dict | None, str]:
    """
Lecture d'un article en cours de rédaction en base de données.
"""
    raw = await mcp_pool.call('actions', 'lire_article', {'ref_article': ref_article})
    data = _parse_mcp_response(raw)
    if data.get('statut') != 'SUCCES':
        return (None, f"🚫 Article **'{ref_article}'** introuvable dans la base. Vérifiez la référence et réessayez.")
    return (data, '')

async def noeud_collecte_draft(state: CopilotState) -> CopilotState:
    """
Cette fonction est appelée pour collecter les informations nécessaires pour créer un nouveau document de type "offre de prix" dans l'état de l'assistant en conversation avec l'utilisateur.
"""
    demande = state['demande_brute']
    draft_existant = state.get('document_draft') or {}

    def _to_float(v) -> float:
        """
Convertit une valeur en flottant, retournant 0.0 si la valeur est None.
"""
        return float(v) if v is not None else 0.0
    if state.get('action') == 'OFFRE_PRIX':
        statut_offre_actuel = draft_existant.get('statut_offre', '')
        if not draft_existant or not statut_offre_actuel:
            refs = extraire_articles_depuis_demande(demande)
            if not refs:
                state['reponse_finale'] = 'Quel(s) article(s) souhaitez-vous inclure dans l\'offre de prix ? (ex: "offre de prix pour ECRAN4K et LAPTOP")'
                state['statut_draft'] = 'COLLECTE'
                state['document_draft'] = {'statut_offre': 'ATTENTE_ARTICLES', 'action': 'OFFRE_PRIX'}
                return state
            draft = await initialiser_draft_offre(refs)
            state['document_draft'] = draft
            state['statut_draft'] = draft['statut_offre']
            state['reponse_finale'] = formater_suggestion_prix(draft['articles'][0], 0, len(draft['articles']))
            return state
        if statut_offre_actuel.startswith('ATTENTE_PRIX'):
            draft, message = traiter_reponse_prix(draft_existant, demande)
            state['document_draft'] = draft
            state['reponse_finale'] = message
            state['statut_draft'] = draft.get('statut_offre', '')
            if not state['statut_draft']:
                state['document_draft'] = {}
            return state
        if statut_offre_actuel == 'ATTENTE_REMISE':
            draft, message = traiter_reponse_remise(draft_existant, demande)
            state['document_draft'] = draft
            state['reponse_finale'] = message
            if draft.get('statut_offre') == 'PRET':
                state['statut_draft'] = 'CONFIRME'
            else:
                state['statut_draft'] = draft.get('statut_offre', '')
                if not state['statut_draft']:
                    state['document_draft'] = {}
            return state
    if draft_existant and state.get('statut_draft') == 'PREVIEW':
        if est_confirmation_stricte(demande) or _est_oui(demande):
            state['statut_draft'] = 'CONFIRME'
            return state
        if est_annulation_stricte(demande) or _est_non(demande):
            state['statut_draft'] = ''
            state['document_draft'] = {}
            state['reponse_finale'] = '🛑 Document annulé.'
            return state
        if _est_action_pdf(demande):
            pdf_p = state.get('pdf_path') or draft_existant.get('pdf_path', 'documents_generes/BL_EN ATTENTE_DRAFT.pdf')
            state['statut_draft'] = 'PREVIEW'
            state['reponse_finale'] = f'📄 Aperçu PDF brouillon : {pdf_p}\n\nTapez **CONFIRM** (ou VALIDATE) pour créer le document définitif, ou **ANNULER** pour abandonner.'
            return state
        state['statut_draft'] = ''
        state['document_draft'] = {}
    if draft_existant and state.get('statut_draft') == 'COLLECTE':
        champ_avant = df_champs_manquants(draft_existant.get('type_doc', ''), draft_existant)
        premier_champ = champ_avant[0] if champ_avant else None
        draft = injecter_reponse_dans_draft(draft_existant.get('type_doc', ''), draft_existant, demande)
        if premier_champ == 'code_client' and draft.get('code_client'):
            err = await _verifier_client_draft(draft['code_client'])
            if err:
                state['reponse_finale'] = err
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
        if premier_champ == 'code_fournisseur' and draft.get('code_fournisseur'):
            err = await _verifier_fournisseur_draft(draft['code_fournisseur'])
            if err:
                state['reponse_finale'] = err
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
        if premier_champ == 'ref_article' and draft.get('ref_article'):
            _, err = await _lire_article_draft(draft['ref_article'])
            if err:
                state['reponse_finale'] = err
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
        state['document_draft'] = draft
    else:
        state['document_draft'] = construire_draft_depuis_state(state)
        draft_type = (state['document_draft'].get('type_doc') or '').upper()
        if draft_type == 'BF' and (not state['document_draft'].get('num_of')):
            num_of_candidat = state.get('num_piece') or state['document_draft'].get('num_piece_source') or state.get('dernier_num_piece')
            if num_of_candidat and num_of_candidat.upper().startswith('OF'):
                state['document_draft']['num_of'] = num_of_candidat
    state['document_draft'] = await _enrichir_draft_client_article(state['document_draft'])
    from graph.draft_flow import _enrichir_facture_depuis_bl, _enrichir_bf_depuis_of
    state['document_draft'] = _enrichir_facture_depuis_bl(state['document_draft'])
    state['document_draft'] = _enrichir_bf_depuis_of(state['document_draft'])
    draft = state['document_draft']
    type_doc = (draft.get('type_doc') or '').upper()
    try:
        ok_saisie, err_saisie = await _verifier_client_des_saisie(draft)
        if not ok_saisie:
            state['reponse_finale'] = err_saisie
            state['statut_draft'] = ''
            state['document_draft'] = {}
            return state
        if draft.get('code_client'):
            err = await _verifier_client_draft(draft['code_client'])
            if err:
                state['reponse_finale'] = err
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
        if draft.get('code_fournisseur'):
            err = await _verifier_fournisseur_draft(draft['code_fournisseur'])
            if err:
                state['reponse_finale'] = err
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
        article_data = None
        if draft.get('ref_article'):
            article_data, err = await _lire_article_draft(draft['ref_article'])
            if err:
                state['reponse_finale'] = err
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
    except Exception as e:
        print(f'Erreur inattendue lors de la vérification initiale : {_safe_str(e)}')
    manquants = df_champs_manquants(state['document_draft'].get('type_doc', ''), state['document_draft'])
    if manquants:
        state['statut_draft'] = 'COLLECTE'
        state['reponse_finale'] = question_pour_champ(state['document_draft']['type_doc'], manquants[0])
        return state
    try:
        if type_doc == 'BL' and article_data and draft.get('quantite'):
            stock_dispo = _to_float(article_data.get('AS_QteSto'))
            qte_demandee = _to_float(draft.get('quantite'))
            if stock_dispo < qte_demandee:
                manque = qte_demandee - stock_dispo
                state['reponse_finale'] = f"🚫 Stock insuffisant pour **{article_data.get('AR_Design', draft['ref_article'])}** ({article_data.get('AR_Ref', draft['ref_article'])}).\n   Disponible : **{stock_dispo:.0f} u** | Demandé : **{qte_demandee:.0f} u** | Manque : **{manque:.0f} u**\n\n   Lancez un Ordre de Fabrication si nécessaire."
                state['statut_draft'] = ''
                state['document_draft'] = {}
                return state
    except Exception as e:
        print(f'⚠️ [Vérif pré-brouillon] Erreur : {e}')
        state['reponse_finale'] = "🚫 Impossible de vérifier les données du document (client, stock, encours) suite à une erreur technique. Le document n'a pas été créé. Réessayez, et si le problème persiste contactez le support."
        state['statut_draft'] = ''
        state['document_draft'] = {}
        return state
    state['statut_draft'] = 'PREVIEW'
    return state

async def noeud_preview_draft(state: CopilotState) -> CopilotState:
    """
Fonction qui prépare un aperçu d'un document en cours de création en branchant le document et en générant un fichier PDF et une réponse finale.
"""
    if state.get('statut_draft') != 'PREVIEW':
        return state
    texte, pdf_path = await generer_preview(state['document_draft'])
    state['pdf_path'] = pdf_path
    state['reponse_finale'] = texte
    return state

async def noeud_execution_draft(state: CopilotState) -> CopilotState:
    """
Générer un PDF final à partir d'un document de type OFFRE_PRIX ou vérifier si un document est déjà transformé.
"""
    if state.get('statut_draft') != 'CONFIRME':
        return state
    draft = state['document_draft']
    if draft.get('type_doc') == 'OFFRE_PRIX':
        pdf_final = await generer_pdf_offre_prix(draft)
        state['pdf_path'] = pdf_final
        state['reponse_finale'] = f'✅ Offre de prix générée avec succès !\n📎 PDF définitif : {pdf_final}'
        state['statut_draft'] = ''
        state['document_draft'] = {}
        return state
    type_doc = (draft.get('type_doc') or '').upper()
    source_piece = draft.get('num_of') if type_doc == 'BF' else draft.get('num_piece_source')
    if source_piece:
        try:
            type_dest_verif = 'FACTURE' if type_doc == 'FACTURE' else 'BF' if type_doc == 'BF' else None
            if type_dest_verif:
                raw = await mcp_pool.call('nl2sql', 'verifier_document_deja_transforme', {'num_piece_source': source_piece, 'type_destination': type_dest_verif})
                data = _parse_mcp_response(raw)
                if data.get('deja_transforme'):
                    state['reponse_finale'] = data.get('message') or f'⚠️  Le document **{source_piece}** a déjà été transformé.'
                    state['statut_draft'] = ''
                    state['document_draft'] = {}
                    return state
        except Exception as e:
            print(f'   ⚠️  [Vérif doublon draft] {e}')
    resultat = await executer_draft_confirme(draft, mcp_workflow_bl=_mcp_workflow_bl, mcp_workflow_of=_mcp_workflow_of, mcp_workflow_bf=_mcp_workflow_bf, mcp_workflow_bl_achat=_mcp_workflow_bl_achat, mcp_pool_transformer_document=_mcp_transformer_document, mcp_workflow_facture=_mcp_workflow_facture)
    if resultat.get('statut') not in _STATUTS_ACTIONS_V3_OK:
        state['reponse_finale'] = resultat.get('message', '❌ Erreur lors de la création.')
        state['statut_draft'] = ''
        state['document_draft'] = {}
        return state
    num_piece = resultat.get('DO_Piece', '')
    state['num_piece'] = num_piece
    pdf_final = await generer_pdf_final(draft, num_piece)
    state['pdf_path'] = pdf_final
    rapport = [resultat.get('message', ''), f'\n📎 PDF définitif : {pdf_final}']
    type_doc = draft.get('type_doc', '').upper()
    if type_doc == 'BL' and resultat.get('suggestion_facture'):
        sugg = resultat['suggestion_facture']
        state['suggestion_en_attente'] = {'type': 'DRAFT_FACTURE_DEPUIS_BL', 'description': f'Créer la facture pour le BL {num_piece}', 'params': sugg}
        rapport.append('\n💡 Tapez **ok** pour préparer le brouillon de la facture correspondante.')
    if type_doc == 'BL_ACHAT' and resultat.get('suggestion_facture_achat'):
        sugg = resultat['suggestion_facture_achat']
        state['suggestion_en_attente'] = {'type': 'DRAFT_FACTURE_ACHAT_DEPUIS_BL', 'description': f"Créer la facture d'achat pour le BL {sugg['num_br']}", 'params': sugg}
        rapport.append("\n💡 Tapez **ok** pour préparer la facture d'achat correspondante.")
    if type_doc == 'OF':
        rapport.append(f'\n💡 Pour finaliser, indiquez la quantité réellement produite : "crée le BF pour {num_piece}"')
    if type_doc == 'BF' and draft.get('num_of'):
        state['num_of_resolu'] = draft['num_of']
    state['reponse_finale'] = '\n'.join((r for r in rapport if r))
    state['statut_draft'] = ''
    state['document_draft'] = {}
    return state

async def noeud_classifier(state: CopilotState) -> CopilotState:
    """
Cette fonction est responsable de classer les intentions et actions d'un chatbot, puis d'enregistrer les corrections et confirmations éventuelles.
"""
    demande_actuelle = state['demande_brute']
    _prev_action = state.get('dernier_action_classifiee', '')
    _prev_question = state.get('derniere_question_classifiee', '')
    if _prev_action and _prev_question:
        correction = detecter_correction(_prev_question, _prev_action, demande_actuelle)
        if correction:
            enregistrer_signal_correction(_prev_question, _prev_action, demande_actuelle)
        else:
            enregistrer_signal_confirmation(_prev_question)
    result = await _noeud_classifier_impl(state)
    if result.get('intention') == 'ERP' and result.get('action'):
        origine = result.get('_origine_classification', 'INCONNUE')
        logger_decision(demande_actuelle, result['action'], origine, result.get('score_confiance', 0.0))
        result['dernier_action_classifiee'] = result['action']
        result['derniere_question_classifiee'] = demande_actuelle
    return result

async def _noeud_classifier_impl(state: CopilotState) -> CopilotState:
    """
Détermine et met à jour l'intention de l'instance de Copilot en fonction de l'état actuel.
"""
    demande_b = state.get('demande_brute', '').strip()
    if _VERBES_NOUVELLE_DEMANDE.match(demande_b):
        # Annuler toutes les actions en cours
        state['modification_en_cours'] = {}
        state['creation_article_en_cours'] = {}
        state['nomenclature_en_cours'] = {}
        state['modification_nomenclature_en_cours'] = {}
        state['attente_complements'] = False
        state['pending_document'] = {}
        if state.get('statut_confirmation') == 'ATTENTE':
            state['statut_confirmation'] = ''
            state['pending_action'] = {}
            
    if state.get('modification_en_cours'):
        state['intention'] = 'ERP'
        return state
    if state.get('creation_article_en_cours'):
        state['intention'] = 'ERP'
        return state
    if state.get('nomenclature_en_cours'):
        state['intention'] = 'ERP'
        return state
    if state.get('pending_action') and state.get('statut_confirmation') == 'ATTENTE':
        if est_confirmation_stricte(state['demande_brute']) or _est_oui(state['demande_brute']):
            state['statut_confirmation'] = 'CONFIRME'
            state.update(state['pending_action'])
            state['validation_ok'] = True
            state['intention'] = 'ERP'
            return state
        if est_annulation_stricte(state['demande_brute']) or _est_non(state['demande_brute']):
            state['statut_confirmation'] = ''
            state['pending_action'] = {}
            state['reponse_finale'] = '🛑 Action annulée.'
            state['intention'] = 'ERP'
            return state
        state['statut_confirmation'] = ''
        state['pending_action'] = {}
    statut_draft_bypass = state.get('statut_draft') or ''
    if state.get('document_draft') and (statut_draft_bypass in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE') or statut_draft_bypass.startswith('ATTENTE_PRIX')):
        demande_b = state['demande_brute']
        if statut_draft_bypass == 'PREVIEW':
            if _est_action_pdf(demande_b):
                action_bypass = state.get('dernier_action_classifiee') or 'GENERER_DOC'
                print(f'⏭️ [Classifier] Consultation PDF brouillon en PREVIEW → bypass classification ({action_bypass})')
                state['intention'] = 'ERP'
                state['action'] = action_bypass
                state['ambigue'] = False
                state['score_confiance'] = 1.0
                return state
            elif not (est_confirmation_stricte(demande_b) or est_annulation_stricte(demande_b) or _est_oui(demande_b) or _est_non(demande_b)):
                print(f'⏭️ [Classifier] Brouillon annulé implicitement par un nouveau message.')
                state['document_draft'] = {}
                state['statut_draft'] = ''
            else:
                action_bypass = state.get('dernier_action_classifiee') or 'GENERER_DOC'
                print(f'⏭️ [Classifier] Confirmation/annulation brouillon en PREVIEW → bypass classification ({action_bypass})')
                state['intention'] = 'ERP'
                state['action'] = action_bypass
                state['ambigue'] = False
                state['score_confiance'] = 1.0
                return state
        elif statut_draft_bypass == 'COLLECTE':
            pre_act = _pre_classifier(demande_b)
            cmd_verb = any((demande_b.lower().strip().startswith(v) for v in ['crée', 'cree', 'créer', 'creer', 'génère', 'genere', 'générer', 'exporte', 'exporter', 'liste', 'lister', 'affiche', 'afficher', 'combien', 'quel', 'quelle', 'quels', 'quelles', 'règle', 'regle', 'payer']))
            if pre_act and pre_act != state.get('dernier_action_classifiee') or (cmd_verb and pre_act is not None and (pre_act != state.get('dernier_action_classifiee'))):
                print(f"⏭️ [Classifier] Brouillon COLLECTE abandonné : nouvelle commande détectée ({pre_act or 'nouvelle demande'}) au lieu de '{state.get('dernier_action_classifiee')}'.")
                state['document_draft'] = {}
                state['statut_draft'] = ''
            else:
                action_bypass = state.get('dernier_action_classifiee') or 'GENERER_DOC'
                print(f'⏭️ [Classifier] Brouillon en {statut_draft_bypass} → bypass classification ({action_bypass})')
                state['intention'] = 'ERP'
                state['action'] = action_bypass
                state['ambigue'] = False
                state['score_confiance'] = 1.0
                return state
        else:
            action_bypass = state.get('dernier_action_classifiee') or 'GENERER_DOC'
            print(f'⏭️ [Classifier] Brouillon en {statut_draft_bypass} → bypass classification ({action_bypass})')
            state['intention'] = 'ERP'
            state['action'] = action_bypass
            state['ambigue'] = False
            state['score_confiance'] = 1.0
            return state
    state = await injecter_complement(state)
    if state.pop('_creation_annulee', False):
        state['attente_complements'] = False
        state['intention'] = 'ERP'
        state['action'] = state.get('dernier_action_classifiee') or 'CREER_CLIENT'
        state['ambigue'] = False
        return state
    if state.get('attente_complements'):
        manquants = verifier_document_incomplet(state)
        if not manquants:
            state['attente_complements'] = False
            type_pending = state.get('pending_document', {}).get('type_doc')
            if type_pending == 'DOCS_PERIODE_INFOS':
                pd = state['pending_document']
                state['date_debut'] = pd.get('date_debut', '')
                state['date_fin'] = pd.get('date_fin', '')
                state['action'] = 'DOCS_PERIODE'
                state['intention'] = 'ERP'
                state['ambigue'] = False
                state['pending_document'] = {}
                return state
            if type_pending == 'CLIENT_CREATION':
                pd = state['pending_document']
                state['nom_client_brut'] = pd.get('nom_client_brut', '')
                state['intitule'] = pd.get('intitule', '')
                state['code_client'] = await _generer_code_client(state['nom_client_brut'])
                state['ct_validite'] = pd.get('ct_validite', 'VALIDE')
                for _champ in ('adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site'):
                    state[_champ] = pd.get(_champ, '')
                state['action'] = 'CREER_CLIENT'
                state['intention'] = 'ERP'
                state['ambigue'] = False
                return state
            elif type_pending == 'FOURNISSEUR_CREATION':
                pd = state['pending_document']
                state['nom_client_brut'] = pd.get('nom_client_brut', '')
                state['intitule'] = pd.get('intitule', '')
                state['code_client'] = await _generer_code_fournisseur(state['nom_client_brut'])
                state['ct_validite'] = pd.get('ct_validite', 'VALIDE')
                for _champ in ('adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site'):
                    state[_champ] = pd.get(_champ, '')
                state['action'] = 'CREER_FOURNISSEUR'
                state['intention'] = 'ERP'
                state['ambigue'] = False
                return state
            elif type_pending == 'REGLEMENT_INFOS':
                pd = state['pending_document']
                state['num_piece'] = pd.get('num_piece', state.get('num_piece', ''))
                state['mode_paiement'] = pd.get('mode_paiement', 'Virement')
                state['numero_piece_paiement'] = pd.get('numero_piece_paiement', '')
                state['action'] = 'REGLEMENT'
                state['intention'] = 'ERP'
                state['ambigue'] = False
                return state
            state['action'] = 'GENERER_DOC'
            state['intention'] = 'ERP'
            state['ambigue'] = False
            return state
        questions = {'code_fournisseur': 'Quel fournisseur ?', 'code_client': 'Quel client ?', 'ref_article': 'Quelle référence article ?', 'quantite': 'Quelle quantité ?', 'prix_unitaire': 'Quel prix unitaire ?', 'nom_client_brut': 'Quel est le nom à créer ?', 'intitule': 'Quelle est la raison sociale ?', 'ct_validite': 'Quel statut pour ce client ? (VALIDE / SUSPECT / BLOQUE)', 'adresse': "Quelle est l'adresse postale ?", 'complement': "Complément d'adresse (si applicable) ?", 'code_postal': 'Quel est le code postal ?', 'ville': 'Quelle est la ville ?', 'pays': 'Quel est le pays ?', 'contact': 'Qui est le contact principal ?', 'telephone': 'Quel est le numéro de téléphone ?', 'email': "Quelle est l'adresse e-mail ?", 'site': 'Quel est le site web (si applicable) ?', 'mode_paiement': 'Quel mode de paiement ? (Virement / Cheque / Traite / Especes / CB)', 'numero_piece_paiement': 'Quel est le numéro du chèque / de la traite ?'}
        champ = manquants[0]
        state['reponse_finale'] = questions.get(champ, 'Merci de préciser cette information.')
        state['intention'] = 'ERP'
        state['ambigue'] = False
        return state
    print('\n🧠 [Orchestrateur] Classification de la demande...')
    question = state['demande_brute']
    t0 = time.perf_counter()
    action_preclass = _pre_classifier(question)
    if action_preclass:
        state['_origine_classification'] = 'REGEX'
    if action_preclass is None and ENABLE_SEMANTIC_CLASSIFIER:
        try:
            decision_sem = await ce.evaluer_semantique(question)
            if decision_sem.action is not None:
                print(f'   🧠 [Sémantique] {decision_sem.action} score={decision_sem.score:.3f} marge={decision_sem.marge:.3f} seuil_haut={decision_sem.seuil_haut:.2f} seuil_bas={decision_sem.seuil_bas:.2f}')
                _fam1 = decision_sem.famille
                _sem_top2_action = None
                if decision_sem.score2 > decision_sem.seuil_bas:
                    for _a, _ctr in sc._action_centroids.items():
                        if _a == decision_sem.action:
                            continue
                        _fam2 = sc.ACTION_TO_FAMILY.get(_a, '')
                        if _fam2 != _fam1:
                            _sem_top2_action = _a
                            break
                SEUIL_SEM_MIN = 0.8
                if decision_sem.statut == 'ACCEPTE' and decision_sem.score < SEUIL_SEM_MIN and (decision_sem.seuil_haut < SEUIL_SEM_MIN):
                    print(f'   🚫 [Sémantique] Score {decision_sem.score:.2f} sous le plancher absolu → rejeté')
                    decision_sem.statut = 'ZONE_GRISE'
                if decision_sem.statut == 'ACCEPTE':
                    _mots_domaine_client = ('client', 'clients', 'tiers', 'société', 'entreprise')
                    _action_est_client = decision_sem.action in ('LISTE_CLIENTS', 'TOP_CLIENTS', 'FICHE_CLIENT', 'STATUT_CLIENT', 'TOUTES_FACTURES_CLIENT')
                    q_lower = question.lower()
                    if decision_sem.action == 'LISTE_CLIENTS' and any((w in q_lower for w in _MOTS_QUALIFICATIFS_FILTRAGE)):
                        print(f'   🚫 [Sémantique] LISTE_CLIENTS rejeté : question contient un qualificatif de filtrage/tri')
                        decision_sem.statut = 'ZONE_GRISE'
                    elif _action_est_client and (not any((w in q_lower for w in _mots_domaine_client))):
                        print(f'   🚫 [Sémantique] Rejeté malgré score={decision_sem.score:.3f} : aucun mot-clé client dans la question')
                    else:
                        action_preclass = decision_sem.action
                        state['_decision_sem'] = decision_sem
                        state['score_confiance'] = decision_sem.score
                        state['_origine_classification'] = 'SEMANTIQUE'
                        print(f'   ✅ [Sémantique] Accepté (score={decision_sem.score:.3f} >= seuil={decision_sem.seuil_haut:.2f})')
                    from apprentissage.apprentissage_semi_auto import enregistrer_prediction
                    enregistrer_prediction(question, decision_sem.action, 'SEMANTIQUE', decision_sem.score, decision_sem.score2)
                elif decision_sem.statut == 'ZONE_GRISE':
                    state['_decision_sem'] = decision_sem
                    print(f'   🔶 [Sémantique] Zone grise ({decision_sem.seuil_bas:.2f} <= score={decision_sem.score:.3f} < seuil={decision_sem.seuil_haut:.2f})')
                else:
                    state['_decision_sem'] = decision_sem
                    print(f'   ❌ [Sémantique] Score trop bas ({decision_sem.score:.3f} < seuil_bas={decision_sem.seuil_bas:.2f})')
        except Exception as _sem_err:
            print(f'   ⚠️  [Sémantique] Erreur classification : {_sem_err}')
    if action_preclass:
        entites_ner = _ner_extraire_entites(question)
        for _champ in ('client', 'article'):
            _v = entites_ner.get(_champ, '')
            if _v.lower().strip() in _MOTS_GENERIQUES_NER:
                entites_ner.pop(_champ, None)
        _regex_code, _nom_regex = _extraire_code_ou_nom_depuis_texte(question)
        _ner_client = entites_ner.get('client', '')
        _extraction_explicite = bool(_regex_code or _ner_client or _nom_regex)
        if _regex_code:
            state['code_client'] = _regex_code
            state['nom_client_brut'] = ''
        elif _ner_client:
            state['nom_client_brut'] = _ner_client
            state['code_client'] = ''
        elif _nom_regex:
            state['nom_client_brut'] = _nom_regex
            state['code_client'] = ''
        else:
            _a_reference_contextuelle = bool(_REFS_CONTEXTUELLES.search(state['demande_brute']))
            if state.get('nom_client_brut') and _a_reference_contextuelle:
                pass
            elif not _a_reference_contextuelle:
                state['nom_client_brut'] = ''
                state['code_client'] = ''
        if state.get('nom_client_brut') and (not state.get('code_client')):
            code_trouve = await _rechercher_client_par_nom(state['nom_client_brut'])
            if code_trouve:
                state['code_client'] = code_trouve
            elif action_preclass == 'CREER_CLIENT':
                state['code_client'] = await _generer_code_client(state['nom_client_brut'])
                print(f"   🔧 [CREER_CLIENT] Nouveau client → code généré : '{state['code_client']}'")
            elif action_preclass == 'CREER_FOURNISSEUR':
                state['code_client'] = await _generer_code_fournisseur(state['nom_client_brut'])
                _nom_up = (state.get('nom_client_brut') or '').upper()
                if _nom_up and state.get('ref_article', '').upper() == _nom_up:
                    state['ref_article'] = ''
                print(f"   🔧 [CREER_FOURNISSEUR] Nouveau fournisseur → code généré : '{state['code_client']}'")
        _piece_tokens_q = [t.upper() for t in _RX_PIECE_DOC.findall(question)]

        def _est_sous_chaine_piece(cand: str) -> bool:
            """
Fonction qui vérifie si un candidat est contenu dans une sous-chaine de mots-clés d'une pièce.
"""
            return any((cand in pt for pt in _piece_tokens_q))
        _ref_article_trouvee = ''
        m_prix_de = re.search("(?:prix\\s+de|combien\\s+co[uû]te|combien\\s+vaut|quel\\s+est\\s+le\\s+prix\\s+(?:de(?:\\s+l['']article)?\\s+)?)(?:l[''\\u2019]article\\s+)?([A-Za-z][A-Za-z0-9\\-]{2,})", question, re.IGNORECASE)
        if m_prix_de:
            cand = m_prix_de.group(1).upper()
            if cand not in _EXCL_ARTICLE and (not cand.startswith('CLI')):
                _ref_article_trouvee = cand
                print(f"   🔎 [PreClass/PatchG] ref_article (prix/coût de) : '{cand}'")
        m_art_ctx = re.search("(?:article|stock\\s+de\\s+(?:l['\\u2019])?(?:article\\s+)?|r[eé]f(?:[eé]rence)?\\s+|produit)\\s+([A-Za-z][A-Za-z0-9\\-]{1,})", question, re.IGNORECASE)
        if not _ref_article_trouvee and m_art_ctx:
            cand = m_art_ctx.group(1).upper()
            if cand not in _EXCL_ARTICLE and (not cand.startswith('CLI')):
                _ref_article_trouvee = cand
                print(f"   🔎 [PreClass] ref_article (contexte article) : '{cand}'")
        if not _ref_article_trouvee:
            for mot in re.findall('\\b([A-Za-z][A-Za-z0-9\\-]{2,})\\b', question):
                mot_upper = mot.upper()
                has_digit = bool(re.search('\\d', mot_upper))
                has_dash_ref = '-' in mot_upper and all((len(p) >= 2 for p in mot_upper.split('-'))) and any((len(p) >= 3 for p in mot_upper.split('-'))) and (mot_upper not in _EXPRESSIONS_FR_EXCLUES)
                is_piece_ref = bool(_RX_PIECE_DOC.match(mot_upper)) or _est_sous_chaine_piece(mot_upper) or bool(re.match('^(?:BL|BC|BF|FA|OF|FF|AV|BR|AF)[0-9A-F]{4,}', mot_upper, re.IGNORECASE))
                if (has_digit or has_dash_ref) and mot_upper not in _EXCL_ARTICLE and (mot_upper not in _EXPRESSIONS_FR_EXCLUES) and (not mot_upper.startswith('CLI')) and (not is_piece_ref):
                    _ref_article_trouvee = mot_upper
                    print(f"   🔎 [PreClass] ref_article (ref ERP) : '{mot_upper}'")
                    break
        if not _ref_article_trouvee:
            m_art_qte = re.search('\\b\\d+(?:[.,]\\d+)?\\s*(?:pi[eè]ces?|unit[eé]s?)?\\s*(?:de\\s+)?([A-Za-z][A-Za-z0-9\\-]{2,})\\b', question, re.IGNORECASE)
            if m_art_qte:
                cand = m_art_qte.group(1).upper()
                if cand not in _EXCL_ARTICLE and (not cand.startswith('CLI')) and (cand != state.get('code_client', '').upper()) and (not _est_sous_chaine_piece(cand)):
                    _ref_article_trouvee = cand
                    print(f"   🔎 [PreClass] ref_article (après quantité) : '{cand}'")
        state['ref_article'] = _ref_article_trouvee
        state['ref_article'] = await _corriger_ref_article(state['ref_article'])
        if state.get('code_client') and state.get('ref_article') and (state['code_client'].upper() == state['ref_article'].upper()):
            print(f"   🧹 [Classifier] '{state['code_client']}' détecté comme code_client ET ref_article → code_client vidé")
            state['code_client'] = ''
        _art = state.get('ref_article', '')
        _piece = state.get('num_piece', '')
        if _art and (_art == _piece or _RX_PIECE_DOC.match(_art)):
            state['ref_article'] = ''
            print(f"   🧹 [PreClass] ref_article '{_art}' effacé (= numéro de pièce document)")
        if action_preclass in ('CREER_CLIENT', 'CREER_FOURNISSEUR'):
            nom_up = (state.get('nom_client_brut') or '').upper()
            if nom_up and state.get('ref_article', '').upper() == nom_up:
                state['ref_article'] = ''
                print(f"   🧹 [CREER_CLIENT] ref_article '{nom_up}' effacé (= nom client)")
        if action_preclass == 'AFFICHER_NOMENCLATURE':
            m_nom = re.search("nomenclature\\s+(?:de|du|pour|d['\\u2019])\\s+(?:l['\\u2019]article\\s+)?(.+?)(?:\\s*[?.,;!]|\\s*$)", question, re.IGNORECASE)
            if m_nom and m_nom.group(1).strip():
                state['ref_article'] = m_nom.group(1).strip().upper()
                state['ref_article'] = await _corriger_ref_article(state['ref_article'])
            if not state.get('ref_article'):
                state['ambigue'] = True
        m_q = re.search('(?:quantit[eé]s?\\s*[=:>]?\\s*|qte\\s*[=:]?\\s*|\\b)(\\d+(?:[.,]\\d+)?)\\s*(?:pièces?|pieces?|unités?|u\\.?\\b)?', question, re.IGNORECASE)
        if m_q:
            val_str = m_q.group(1)
            val = float(val_str.replace(',', '.'))
            _ressemble_annee = bool(re.match('^(19|20)\\d{2}$', val_str))
            _precede_par_en = bool(re.search(f'\\ben\\s+{re.escape(val_str)}\\b', question, re.IGNORECASE))
            _dans_date_iso = bool(re.search(f'\\b{re.escape(val_str)}-\\d{{2}}-\\d{{2}}\\b', question))
            if val > 0 and (not (_ressemble_annee and (_precede_par_en or _dans_date_iso))):
                state['quantite'] = val
        if action_preclass == 'GENERER_DOC' and (not state.get('type_doc')):
            q_lower = question.lower()
            if re.search("facture\\s*(?:d['\\u2019]|de\\s+)?achat|facture\\s+fournisseur", q_lower):
                state['type_doc'] = 'FA_ACHAT'
                m_four = re.search('fournisseur\\s+([A-Z0-9]+)', question, re.IGNORECASE)
                m_art = re.search('article\\s+([A-Z0-9]+)', question, re.IGNORECASE)
                m_qte = re.search('quantit[eé]s?\\s*[=:>]?\\s*(\\d+(?:[.,]\\d+)?)', question, re.IGNORECASE)
                m_prix = re.search('prix\\s*[=:>]?\\s*(\\d+(?:[.,]\\d+)?)', question, re.IGNORECASE)
                doc = state.get('pending_document', {})
                if m_four:
                    doc['code_fournisseur'] = m_four.group(1).upper()
                if m_art:
                    doc['ref_article'] = m_art.group(1).upper()
                if m_qte:
                    doc['quantite'] = float(m_qte.group(1).replace(',', '.'))
                if m_prix:
                    doc['prix_unitaire'] = float(m_prix.group(1).replace(',', '.'))
                doc['type_doc'] = 'FA_ACHAT'
                state['pending_document'] = doc
                if doc.get('code_fournisseur'):
                    state['code_client'] = doc['code_fournisseur']
                if doc.get('ref_article'):
                    state['ref_article'] = doc['ref_article']
                if doc.get('quantite'):
                    state['quantite'] = doc['quantite']
            elif re.search('bl\\s+achat|bon\\s+de\\s+r[eé]ception|r[eé]ception\\s+fournisseur', q_lower):
                state['type_doc'] = 'BL_ACHAT'
                m_four = re.search('fournisseur\\s+([A-Z0-9]+)', question, re.IGNORECASE)
                m_art = re.search('article\\s+([A-Z0-9]+)', question, re.IGNORECASE)
                m_qte = re.search('quantit[eé]s?\\s*[=:>]?\\s*(\\d+(?:[.,]\\d+)?)', question, re.IGNORECASE)
                m_prix = re.search('prix\\s*[=:>]?\\s*(\\d+(?:[.,]\\d+)?)', question, re.IGNORECASE)
                doc = state.get('pending_document', {})
                if m_four:
                    doc['code_fournisseur'] = m_four.group(1).upper()
                if m_art:
                    doc['ref_article'] = m_art.group(1).upper()
                if m_qte:
                    doc['quantite'] = float(m_qte.group(1).replace(',', '.'))
                if m_prix:
                    doc['prix_unitaire'] = float(m_prix.group(1).replace(',', '.'))
                doc['type_doc'] = 'BL_ACHAT'
                state['pending_document'] = doc
                if doc.get('code_fournisseur'):
                    state['code_client'] = doc['code_fournisseur']
                if doc.get('ref_article'):
                    state['ref_article'] = doc['ref_article']
                if doc.get('quantite'):
                    state['quantite'] = doc['quantite']
            elif re.search('\\bbf\\b|bon\\s+de\\s+fabrication', q_lower):
                state['type_doc'] = 'BF'
            elif re.search('\\bof\\b|ordre\\s+de\\s+fabrication', q_lower):
                state['type_doc'] = 'OF'
            elif re.search('\\bbc\\b|bon\\s+de\\s+commande', q_lower):
                state['type_doc'] = 'BC'
            elif re.search('facture|facturer', q_lower):
                state['type_doc'] = 'FACTURE'
            else:
                state['type_doc'] = 'BL'
        if action_preclass == 'TRANSFORMER_DOC':
            m_piece = _RX_PIECE_DOC.search(question)
            if m_piece:
                state['num_piece'] = m_piece.group(1).upper()
                print(f"   🔎 [Fix3] num_piece extrait : '{state['num_piece']}'")
            elif state.get('dernier_num_piece'):
                state['num_piece'] = state['dernier_num_piece']
                print(f"   🔗 [Fix3] num_piece hérité session : '{state['num_piece']}'")
            q_lower = question.lower()
            if re.search('\\bbf\\b|bon\\s+de\\s+fabrication', q_lower):
                state['type_doc'] = 'BF'
            elif re.search('facture|facturer', q_lower):
                if re.search('achat|fournisseur', q_lower):
                    state['type_doc'] = 'FA_ACHAT'
                else:
                    state['type_doc'] = 'FACTURE'
            elif re.search('\\bbl\\b|bon\\s+de\\s+livraison', q_lower):
                state['type_doc'] = 'BL'
            elif re.search('\\bbc\\b|bon\\s+de\\s+commande', q_lower):
                state['type_doc'] = 'BC'
            if not state.get('num_piece'):
                state['ambigue'] = True
        if action_preclass == 'DOCS_PERIODE':
            m_client_periode = re.search("(?:documents?|factures?|bls?)\\s+(?:du\\s+client\\s+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\\s\\-&']{1,40}?)\\s+entre", question, re.IGNORECASE)
            if m_client_periode:
                nom_candidat = m_client_periode.group(1).strip()
                if _est_nom_valide(nom_candidat):
                    code_trouve = await _rechercher_client_par_nom(nom_candidat)
                    state['nom_client_brut'] = nom_candidat
                    state['code_client'] = code_trouve or ''
                    if not code_trouve:
                        state['ambigue'] = True
            m_dates = re.search('(\\d{4}-\\d{2}-\\d{2})\\s+(?:et|au)\\s+(?:le\\s+)?(\\d{4}-\\d{2}-\\d{2})', question, re.IGNORECASE)
            if m_dates:
                state['date_debut'] = m_dates.group(1)
                state['date_fin'] = m_dates.group(2)
            if not state.get('date_debut') or not state.get('date_fin'):
                state['pending_document'] = {'type_doc': 'DOCS_PERIODE_INFOS', 'demande_originale': question}
                state['attente_complements'] = True
                state['reponse_finale'] = 'Sur quelle période souhaitez-vous ces documents ? (ex : 2026-08-01 et 2026-08-15)'
                state['ambigue'] = False
                return state
        if action_preclass == 'MODIFIER_STATUT':
            q_lower = question.lower()
            if re.search('d[eé]bloquer?|r[eé]activer?|valider?|activer?', q_lower):
                state['type_doc'] = 'VALIDE'
            else:
                state['type_doc'] = 'BLOQUE'
            _RX_CODE_TIERS = re.compile('\\b([A-Z]{2,8}\\d{0,})\\b', re.IGNORECASE)
            _EXCLUES_CODES = {'CLIENT', 'FOURNISSEUR', 'BLOQUER', 'DEBLOQUER', 'ACTIVER', 'STATUT', 'MODIFIER', 'CLIENTS', 'FOURNISSEURS', 'VALIDE', 'BLOQUE'}
            if re.search('fournisseur', q_lower):
                if not state.get('code_fournisseur'):
                    m = _RX_CODE_TIERS.search(question)
                    if m and m.group(1).upper() not in _EXCLUES_CODES:
                        state['code_fournisseur'] = m.group(1).upper()
            elif not state.get('code_client'):
                m = _RX_CODE_TIERS.search(question)
                if m and m.group(1).upper() not in _EXCLUES_CODES:
                    state['code_client'] = m.group(1).upper()
            if not state.get('code_client') and (not state.get('code_fournisseur')):
                nom_brut = state.get('nom_client_brut')
                if nom_brut:
                    code_trouve = await _rechercher_client_par_nom(nom_brut)
                    if code_trouve:
                        state['code_client'] = code_trouve
        if action_preclass == 'DOCS_PERIODE':
            m_dates = re.search('(\\d{4}-\\d{2}-\\d{2})\\s+(?:et|au)\\s+(?:le\\s+)?(\\d{4}-\\d{2}-\\d{2})', question, re.IGNORECASE)
            if m_dates:
                state['date_debut'] = m_dates.group(1)
                state['date_fin'] = m_dates.group(2)
            if not state.get('date_debut') or not state.get('date_fin'):
                state['pending_document'] = {'type_doc': 'DOCS_PERIODE_INFOS', 'demande_originale': question}
                state['attente_complements'] = True
                state['reponse_finale'] = 'Sur quelle période souhaitez-vous ces documents ? (ex : 2026-08-01 et 2026-08-15)'
                state['ambigue'] = False
                return state
        state['action'] = action_preclass
        state['score_confiance'] = 1.0
        state['ambigue'] = state.get('ambigue', False)
        state['intention'] = 'ERP'
        if action_preclass == 'FACTURES_NON_REGLEES':
            m_seuil = re.search('(?:plus\\s+de|sup[eé]rieur(?:es?)?\\s+[àa]|au[- ]del[àa]\\s+de)\\s+(\\d+)\\s+jours?', question, re.IGNORECASE)
            if m_seuil:
                state['seuil_jours_impaye'] = float(m_seuil.group(1))
                print(f"   🔎 [PreClass] seuil_jours_impaye : {state['seuil_jours_impaye']:g}")
                if action_preclass == 'GENERER_DOC':
                    type_d = (state.get('type_doc') or '').upper()
                    _a_client = bool(state.get('code_client') or state.get('nom_client_brut'))
                    if type_d not in {'OF', 'BF'} and (not _a_client):
                        state['ambigue'] = True
                    if not state.get('ref_article'):
                        state['ambigue'] = True
                    if state.get('quantite', 0.0) <= 0.0:
                        state['ambigue'] = True
        if action_preclass == 'MODIFIER_STATUT':
            q_lower = question.lower()
            if re.search('d[eé]bloquer?|r[eé]activer?|valider?|activer?', q_lower):
                state['type_doc'] = 'VALIDE'
            else:
                state['type_doc'] = 'BLOQUE'
            if not state.get('code_client'):
                state['ambigue'] = True
        if action_preclass == 'FACTURES_NON_REGLEES':
            m_seuil = re.search('(?:plus\\s+de|sup[eé]rieur\\s+[àa])\\s+(\\d+)\\s+jours?', question, re.IGNORECASE)
            if m_seuil:
                state['seuil_jours_impaye'] = int(m_seuil.group(1))
        if action_preclass == 'MODIFIER_ARTICLE':
            if not state.get('ref_article'):
                state['ambigue'] = True
        if action_preclass == 'CREER_CLIENT':
            for _cle in ('code_client', 'nom_client_brut', 'intitule', 'ct_validite', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site'):
                state.pop(_cle, None)
            nom_brut = state.get('nom_client_brut') or ''
            if not nom_brut:
                m_cli = re.search("(?:cr[eé][eé]?|nouveau|ajouter?)\\s+(?:le\\s+|un\\s+)?(?:client|tiers)\\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\\s\\-&'.]{1,60}?)(?:\\s*[?.,;!]|\\s*$)", question, re.IGNORECASE)
                if not m_cli:
                    m_cli = re.search("(?:client|tiers)\\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\\s\\-&'.]{1,60}?)(?:\\s*[?.,;!]|\\s*$)", question, re.IGNORECASE)
                if m_cli:
                    nom_extrait = m_cli.group(1).strip()
                    _invalides_cli = {'le', 'la', 'les', 'du', 'de', 'un', 'une', 'des', 'pour', 'avec', 'sur', 'qui', "n'existe", 'nexiste', 'pas', 'encore', 'dans', 'notre', 'base', 'donnees', 'données', 'de', 'données'}
                    if nom_extrait.lower() not in _invalides_cli and len(nom_extrait) >= 2 and _est_nom_valide(nom_extrait):
                        nom_brut = nom_extrait
                        print(f"   🔧 [CREER_CLIENT] Nom extrait: '{nom_extrait}'")
                if not nom_brut and state.get('ref_article'):
                    nom_brut = state['ref_article']
                    state['ref_article'] = ''
                    print(f"   🔧 [CREER_CLIENT] Nom depuis ref_article: '{nom_brut}'")
            if nom_brut and await _verifier_nom_tiers_existe_mcp(nom_brut, 1):
                state['reponse_finale'] = f"❌ Le fournisseur **'{nom_brut}'** existe déjà. Impossible de le recréer."
                state['pending_document'] = {}
                state['attente_complements'] = True
                state['ambigue'] = False
                state['code_client'] = ''
                state['nom_client_brut'] = ''
                return state
            pending = {'type_doc': 'CLIENT_CREATION'}
            if nom_brut:
                state['nom_client_brut'] = nom_brut
                pending['nom_client_brut'] = nom_brut
                pending['intitule'] = nom_brut
                pending['_champs_saisis'] = {'nom_client_brut', 'intitule'}
            state['pending_document'] = pending
            state['attente_complements'] = True
            state['ambigue'] = False
            state['code_client'] = ''
        if action_preclass == 'CREER_FOURNISSEUR':
            for _cle in ('code_client', 'nom_client_brut', 'intitule', 'ct_validite', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site'):
                state.pop(_cle, None)
            nom_brut = state.get('nom_client_brut') or ''
            if not nom_brut:
                m_fourn = re.search("(?:cr[eé][eé]?|nouveau|ajouter?)\\s+(?:le\\s+|un\\s+)?fournisseur\\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\\s\\-&'.]{1,60}?)(?:\\s*[?.,;!]|\\s*$)", question, re.IGNORECASE)
                if not m_fourn:
                    m_fourn = re.search("fournisseur\\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\\s\\-&'.]{1,60}?)(?:\\s*[?.,;!]|\\s*$)", question, re.IGNORECASE)
                if m_fourn:
                    nom_extrait = m_fourn.group(1).strip()
                    _invalides = {'le', 'la', 'les', 'du', 'de', 'un', 'une', 'des', 'pour', 'avec', 'sur'}
                    if nom_extrait.lower() not in _invalides and len(nom_extrait) >= 2 and _est_nom_valide(nom_extrait):
                        nom_brut = nom_extrait
                        if state.get('ref_article', '').upper() == nom_extrait.upper():
                            state['ref_article'] = ''
                        print(f"   🔧 [CREER_FOURNISSEUR] Nom extrait: '{nom_extrait}'")
                if not nom_brut and state.get('ref_article'):
                    nom_brut = state['ref_article']
                    state['ref_article'] = ''
                    print(f"   🔧 [CREER_FOURNISSEUR] Nom depuis ref_article: '{nom_brut}'")
            if nom_brut and await _verifier_nom_tiers_existe_mcp(nom_brut, 0):
                state['reponse_finale'] = f"❌ Le client **'{nom_brut}'** existe déjà. Impossible de le recréer."
                state['pending_document'] = {}
                state['attente_complements'] = True
                state['ambigue'] = False
                state['code_client'] = ''
                state['nom_client_brut'] = ''
                return state
            pending = {'type_doc': 'FOURNISSEUR_CREATION'}
            if nom_brut:
                state['nom_client_brut'] = nom_brut
                state['intitule'] = nom_brut
                pending['nom_client_brut'] = nom_brut
                pending['intitule'] = nom_brut
                pending['_champs_saisis'] = {'nom_client_brut', 'intitule'}
            state['pending_document'] = pending
            state['attente_complements'] = True
            state['ambigue'] = False
            state['code_client'] = ''
        if action_preclass == 'REGLEMENT':
            pieces_trouvees = re.findall(_RX_PIECE_HEX, question, re.IGNORECASE)
            if pieces_trouvees:
                state['num_piece'] = pieces_trouvees[-1].upper()
                state['code_client'] = ''
            m_mode = re.search('(?:par\\s+)(chèque|cheque|virement|espèces?|especes?|cb|carte|traite)', question, re.IGNORECASE)
            _MODE_NORM = {'chèque': 'Cheque', 'cheque': 'Cheque', 'virement': 'Virement', 'espèces': 'Especes', 'especes': 'Especes', 'cb': 'CB', 'carte': 'CB', 'traite': 'Traite'}
            mode_detecte = _MODE_NORM.get(m_mode.group(1).lower(), '') if m_mode else ''
            state['mode_paiement'] = mode_detecte
            m_num_paiement = re.search('(?:n[°o]\\s*|num[eé]ro\\s*)([A-Z0-9\\-]{3,})', question, re.IGNORECASE)
            numero_detecte = m_num_paiement.group(1) if m_num_paiement else ''
            if not state.get('num_piece'):
                state['ambigue'] = True
            elif not mode_detecte or (mode_detecte in ('Cheque', 'Traite') and (not numero_detecte)):
                state['pending_document'] = {'type_doc': 'REGLEMENT_INFOS', 'num_piece': state['num_piece'], 'mode_paiement': mode_detecte, 'numero_piece_paiement': numero_detecte}
                state['attente_complements'] = True
                state['ambigue'] = False
        if action_preclass == 'CREER_AVOIR' and (not state.get('num_piece')):
            state['ambigue'] = True
        elapsed = time.perf_counter() - t0
        print(f"   Action    : {action_preclass} {('[AMBIGUE]' if state.get('ambigue') else '')}\n   Confiance : 1.00\n   Client: {state.get('code_client') or state.get('nom_client_brut') or '—'} | Article: {state.get('ref_article') or '—'} | Qté: {state.get('quantite', 0)} | Pièce: {state.get('num_piece') or '—'}\n   ⏱️  {elapsed:.2f}s")
        return state
    entites_ner = _ner_extraire_entites(state['demande_brute'])
    for _champ in ('client', 'article'):
        _v = entites_ner.get(_champ, '')
        if _v.lower().strip() in _MOTS_GENERIQUES_NER:
            print(f"   ⚠️  [GLiNER-Fix] '{_champ}'='{_v}' ignoré")
            entites_ner.pop(_champ, None)
    if entites_ner.get('article', '').lower() in {'encours', 'crédit', 'solde', 'statut', 'fiche', 'info', 'rupture', 'stock', 'marge', 'rentabilité', 'prix', 'tarif', 'coût', 'cout'}:
        print(f"   🧹 [GLiNER] Faux positif article supprimé : '{entites_ner['article']}'")
        entites_ner.pop('article', None)
    if entites_ner:
        print(f'   🏷️  [GLiNER] Entités : {entites_ner}')
    prompt_unique = f'''Tu es un classificateur ERP Sage 100.\nMessage : "{state['demande_brute']}"\n\nÉTAPE 1 — Intention :\n- ERP        → gestion commerciale, clients, factures, stocks, commandes, CA, fabrication\n- AIDE       → l'utilisateur demande tes capacités\n- HORS_SUJET → hors ERP\n\nÉTAPE 2 — Action (si ERP) :\nLISTE_CLIENTS | TOP_CLIENTS | LISTE_ARTICLES | PALMARES_ARTICLES | CA_GLOBAL\nCLIENTS_BAISSE | FACTURES_NON_REGLEES | FACTURES_NON_REGLEES_FOURN | TOUTES_FACTURES_CLIENT | VERIFIER_STOCK\nSTATUT_CLIENT | FICHE_CLIENT | DOCS_PERIODE | RENTABILITE | SAISONNALITE | DSO | RFM | LIRE_ENCOURS_CLIENT\nOFFRE_PRIX_EXCEL | DECLARATION_EXCEL | BALANCE_AGEE_EXCEL | DASHBOARD_EXCEL\nCREER_CLIENT | MODIFIER_STATUT | MODIFIER_ARTICLE | GENERER_DOC | TRANSFORMER_DOC | CREER_AVOIR\nREGLEMENT | MOUVEMENT_STOCK | PROPOSITION_ACHAT | WORKFLOW_COMMANDE\nRECHERCHE_PROCEDURE | RECOMMANDATION | SEUIL_STOCK | LISTE_PROCEDURES\nNL2SQL_LIBRE | AMBIGUE\n\nRÈGLES IMPORTANTES :\n- "modifier/changer un article" ou "modifier <référence article>" → MODIFIER_ARTICLE (jamais MODIFIER_STATUT, qui concerne uniquement les clients ; jamais LISTE_ARTICLES, qui ne fait que consulter)\n- MODIFIER_STATUT concerne exclusivement bloquer/débloquer/réactiver un CLIENT\n- "encours d'un client précis" / "quel est l'encours de X" / "donne-moi l'encours du client Y" → LIRE_ENCOURS_CLIENT\n- "encours" générique sans client précis → NL2SQL_LIBRE\n- "clients inactifs" → NL2SQL_LIBRE\n- "clients bloqués" → NL2SQL_LIBRE\n- "liste BL client" → NL2SQL_LIBRE\n- "liste clients" générique → LISTE_CLIENTS\n- "conditions commerciales" → FICHE_CLIENT\n- "transformer OF en BF" → TRANSFORMER_DOC\n- "OF" seul → GENERER_DOC type_doc=OF\n- "BF" seul → GENERER_DOC type_doc=BF\n- "client X est-il bloqué/actif" → STATUT_CLIENT (jamais RFM)\n- Si client absent → écris INCONNU\n- "caractéristiques/fiche technique d'un article" → RECHERCHE_PROCEDURE\n- "réclamations/SAV sur un article" → RECHERCHE_PROCEDURE\n- "conditions négociées/historique client" → RECHERCHE_PROCEDURE (si pas dans F_COMPTET)\nFORMAT STRICT (une clé par ligne) :\nintention:VALEUR\naction:VALEUR\nconfiance:0.0-1.0\nclient:NOM_COMPLET_OU_CODE_OU_INCONNU\narticle:VALEUR_OU_INCONNU\nquantite:VALEUR_OU_INCONNU\npiece:VALEUR_OU_INCONNU\ntype_doc:VALEUR_OU_INCONNU\ndate_debut:VALEUR_OU_INCONNU\ndate_fin:VALEUR_OU_INCONNU'''
    if ENABLE_MEM0:
        r1, mem_contexte = await asyncio.gather(_invoke_llm(prompt_unique, use_smart=False), _mem0_rechercher(state['demande_brute']))
    else:
        r1 = await _invoke_llm(prompt_unique, use_smart=False)
        mem_contexte = ''
    state['mem0_contexte'] = mem_contexte
    lignes = r1.strip().split('\n')

    def _val(key: str, default: str='INCONNU') -> str:
        """
Récupère la valeur d'une clé dans une liste de lignes et renvoie une valeur par défaut si elle n'est pas trouvée.
"""
        for line in lignes:
            clean = line.strip().replace('*', '').replace('`', '')
            if clean.lower().startswith(f'{key}:'):
                v = clean.split(':', 1)[1].strip()
                if key == 'client':
                    return v if v else default
                return v.split()[0] if v.split() else default
        return default
    intention = _val('intention', 'ERP').strip().upper().split()[0]
    if intention not in ('ERP', 'AIDE', 'HORS_SUJET'):
        intention = 'ERP'
    state['intention'] = intention
    if state['intention'] != 'ERP':
        return state
    try:
        state['score_confiance'] = max(0.0, min(1.0, float(_val('confiance', '0.8'))))
    except ValueError:
        state['score_confiance'] = 0.8
    raw_action = _val('action', 'NL2SQL_LIBRE').upper().strip().split()[0]
    llm_client = _clean(_val('client'))
    llm_article = _clean(_val('article'))
    llm_piece = _clean(_val('piece'))
    llm_type = _clean(_val('type_doc'))
    decision = await _arbitrer_semantique_llm(question, state.get('_decision_sem'), raw_action)
    if decision is None:
        rag_action = await _verifier_rag(question)
        if rag_action:
            decision = rag_action
    if decision is None:
        decision = raw_action
    state['action'] = decision
    raw_action = decision
    db = state['demande_brute']
    n = db.lower()
    _regex_code, _nom_regex = _extraire_code_ou_nom_depuis_texte(db)
    _ner_client = entites_ner.get('client', '')
    _llm_client_clean = ''
    if llm_client and llm_client.upper() not in _LLM_PLACEHOLDERS:
        if _est_nom_valide(llm_client) or re.match('^[A-Z]{2,6}\\d{2,}$', llm_client, re.IGNORECASE):
            _llm_client_clean = llm_client
    _extraction_explicite = bool(_regex_code or _ner_client or _nom_regex or _llm_client_clean)
    if _regex_code:
        state['code_client'] = _regex_code
        state['nom_client_brut'] = ''
        print(f"   ✅ [Client] Code regex : '{_regex_code}'")
    elif _ner_client:
        state['nom_client_brut'] = _ner_client
        state['code_client'] = ''
        print(f"   ✅ [Client] NER : '{_ner_client}'")
    elif _nom_regex:
        state['nom_client_brut'] = _nom_regex
        state['code_client'] = ''
        print(f"   ✅ [Client] Nom regex : '{_nom_regex}'")
    elif _llm_client_clean:
        state['nom_client_brut'] = _llm_client_clean
        state['code_client'] = ''
        print(f"   ✅ [Client] LLM : '{_llm_client_clean}'")
    else:
        _a_reference_contextuelle = bool(_REFS_CONTEXTUELLES.search(state['demande_brute']))
        if state.get('nom_client_brut') and _a_reference_contextuelle:
            print(f"   🔗 [Client] Hérité session (référence contextuelle détectée) : '{state['nom_client_brut']}'")
        elif not _a_reference_contextuelle:
            state['nom_client_brut'] = ''
            state['code_client'] = ''
    if state.get('nom_client_brut') and (not state.get('code_client')):
        code_trouve = await _rechercher_client_par_nom(state['nom_client_brut'])
        if code_trouve:
            state['code_client'] = code_trouve
    if state.get('code_client') and state.get('ref_article') and (state['code_client'].upper() == state.get('ref_article', '').upper()):
        print('   ⚠️  [Anti-Hallucination] Client == Article → client vidé')
        state['code_client'] = ''
    _CODES_IGNORES = {'client', 'tiers', 'societe', 'société', 'entreprise', 'le', 'la', 'les', 'un', 'une', 'des', 'pour', 'avec'}
    if state.get('code_client', '').lower() in _CODES_IGNORES:
        state['code_client'] = ''
    _regex_article = ''
    m_prix_de2 = re.search("(?:prix\\s+de|combien\\s+co[uû]te|combien\\s+vaut|quel\\s+est\\s+le\\s+prix\\s+(?:de(?:\\s+l['']article)?\\s+)?)(?:l['\\u2019]article\\s+)?([A-Za-z][A-Za-z0-9\\-]{2,})", db, re.IGNORECASE)
    if m_prix_de2:
        cand = m_prix_de2.group(1).upper()
        if cand not in _EXCL_ARTICLE and (not cand.startswith('CLI')):
            _regex_article = cand
    m_art_ctx = re.search("(?:article|stock\\s+de\\s+(?:l['\\u2019])?(?:article\\s+)?|r[eé]f(?:[eé]rence)?\\s+|produit)\\s+([A-Za-z][A-Za-z0-9\\-]{1,})", db, re.IGNORECASE)
    if not _regex_article and m_art_ctx:
        cand = m_art_ctx.group(1).upper()
        if cand not in _EXCL_ARTICLE and (not cand.startswith('CLI')):
            _regex_article = cand
    if not _regex_article:
        _PRONOMS_INVERSION = {'TU', 'IL', 'ELLE', 'JE', 'NOUS', 'VOUS', 'ILS', 'ELLES', 'ON', 'MOI', 'TOI'}
        for mot in re.findall('\\b([A-Za-z][A-Za-z0-9\\-]{2,})\\b', db):
            mot_upper = mot.upper()
            has_digit = bool(re.search('\\d', mot_upper))
            _segs = mot_upper.split('-')
            has_dash_ref = '-' in mot_upper and all((len(p) >= 2 for p in _segs)) and any((len(p) >= 3 for p in _segs)) and (_segs[-1] not in _PRONOMS_INVERSION) and (mot_upper not in _EXPRESSIONS_FR_EXCLUES)
            if (has_digit or has_dash_ref) and mot_upper not in _EXCL_ARTICLE and (not mot_upper.startswith('CLI')) and (mot_upper != state.get('code_client', '').upper()):
                _regex_article = mot_upper
                break
    if not _regex_article:
        m_art_qte = re.search('\\b\\d+(?:[.,]\\d+)?\\s*(?:pi[eè]ces?|unit[eé]s?)?\\s*(?:de\\s+)?([A-Za-z][A-Za-z0-9\\-]{2,})\\b', db, re.IGNORECASE)
        if m_art_qte:
            cand = m_art_qte.group(1).upper()
            if cand not in _EXCL_ARTICLE and (not cand.startswith('CLI')) and (cand not in _EXPRESSIONS_FR_EXCLUES) and (cand != state.get('code_client', '').upper()):
                _regex_article = cand
    if _regex_article:
        print(f'   🔎 [Regex] ref_article : {_regex_article}')
    state['ref_article'] = entites_ner.get('article') or _regex_article or llm_article
    state['ref_article'] = await _corriger_ref_article(state['ref_article'])
    if state['ref_article'].upper() in _TYPES_DOC_INVALIDES_COMME_ARTICLE:
        state['ref_article'] = ''
    state['num_piece'] = entites_ner.get('piece') or _clean(llm_piece)
    state['type_doc'] = entites_ner.get('type_doc') or _clean(llm_type)
    state['date_debut'] = entites_ner.get('date_debut') or _clean(_val('date_debut'))
    state['date_fin'] = entites_ner.get('date_fin') or _clean(_val('date_fin'))
    state['mode_paiement'] = _val('mode_paiement', 'Virement')
    quantite_explicite = False
    qr = entites_ner.get('quantite') or _clean(_val('quantite'))
    if qr:
        try:
            state['quantite'] = float(re.sub('[^\\d.]', '', qr) or '0')
            if state['quantite'] > 0:
                quantite_explicite = True
        except ValueError:
            state['quantite'] = 0.0
    if not quantite_explicite:
        m_q = re.search('\\b(\\d+(?:[.,]\\d+)?)\\s*(?:pièces?|pieces?|unités?|u\\.?\\b)', db, re.IGNORECASE) or re.search('\\bde\\s+(\\d+(?:[.,]\\d+)?)\\b', db, re.IGNORECASE) or re.search('\\b(\\d+)\\s+(?:pièces?|pieces?|unités?)', db, re.IGNORECASE)
        if m_q:
            state['quantite'] = float(m_q.group(1).replace(',', '.'))
            quantite_explicite = True
            print(f"   🔎 [Regex] quantite : {state['quantite']}")
    if state['num_piece'] and (not re.match('^[A-Z]{1,4}\\d{3,}$', state['num_piece'])):
        if re.search('[A-Za-z]', state['num_piece']) and (not state['ref_article']) and (state['num_piece'].upper() not in _TYPES_DOC_INVALIDES_COMME_ARTICLE):
            state['ref_article'] = state['num_piece']
        state['num_piece'] = ''
    if not state['type_doc']:
        _TDM = {"facture\\s*(?:d['\\u2019]|de\\s+)?achat|facture\\s+fournisseur": 'FA_ACHAT', 'facture|facturer': 'FACTURE', 'bl achat|bon de r[eé]ception|r[eé]ception fournisseur': 'BL_ACHAT', 'bon de livraison|\\bbl\\b': 'BL', 'bon de commande|\\bbc\\b': 'BC', 'bon de fabrication|\\bbf\\b': 'BF', 'ordre de fabrication|\\bof\\b': 'OF'}
        for pattern, doc_type in _TDM.items():
            if re.search(pattern, n):
                state['type_doc'] = doc_type
                break
    if ENABLE_STRUCTURED_EXTRACTION and raw_action in ACTIONS_ECRITURE and (not state.get('code_client')) and (not state.get('nom_client_brut')) and (not state.get('ref_article')):
        try:
            from extraction.extraction_structuree import extraire_entites_llm
            entites_llm = await extraire_entites_llm(db, lambda p: _invoke_llm(p, use_smart=False))
            if entites_llm.get('client'):
                state['nom_client_brut'] = entites_llm['client']
                code_trouve = await _rechercher_client_par_nom(entites_llm['client'])
                if code_trouve:
                    state['code_client'] = code_trouve
            if entites_llm.get('article'):
                state['ref_article'] = entites_llm['article'].upper()
            if entites_llm.get('quantite') and (not quantite_explicite):
                state['quantite'] = entites_llm['quantite']
                quantite_explicite = True
            if entites_llm:
                print(f'   🧩 [Extraction structurée] Dernier recours LLM : {entites_llm}')
        except Exception as _extr_err:
            print(f'   ⚠️  [Extraction structurée] Échec : {_extr_err}')
    _MOTS_AIDE = {'aide', 'help', 'que sais-tu', 'que peux-tu', 'tes capacités', 'fonctionnalités'}
    if any((w in n for w in _MOTS_AIDE)) and (not any((w in n for w in ('client', 'facture', 'article', 'stock', 'liste', 'ca ', 'fournisseur')))):
        state['intention'] = 'AIDE'
        return state
    _RX_TRIG_LISTE = re.compile('\\b(liste|tous|toutes|affiche|montre|donne)\\b', re.IGNORECASE)
    _RX_TRIG_CLIENT = re.compile('\\bclients?\\b', re.IGNORECASE)
    _RX_TRIG_ARTICLE = re.compile('\\b(produit|article|r[ée]f[ée]rence|r[ée]f|catalogue)s?\\b', re.IGNORECASE)
    _RX_TRIG_CA = re.compile('\\bca\\b|\\bchiffre\\s+d.affaires?\\b', re.IGNORECASE)
    _RX_TRIG_TOP = re.compile('\\btop\\b', re.IGNORECASE)
    _RX_TRIG_STOCK = re.compile('\\bstock\\b', re.IGNORECASE)
    if re.search('transform', n) and re.search('\\bof\\b.{0,20}\\bbf\\b|\\bbl\\b.{0,20}facture|\\bbc\\b.{0,20}\\bbl\\b', n):
        raw_action = 'TRANSFORMER_DOC'
        m_piece = _RX_PIECE_DOC.search(db)
        if m_piece and (not state.get('num_piece')):
            state['num_piece'] = m_piece.group(1).upper()
    elif re.search('offre\\s+de\\s+prix|devis|tarif\\s+(?:de|pour|article)', n):
        raw_action = 'OFFRE_PRIX'
    elif re.search('\\bclient\\b.{0,25}\\best[\\s-]il\\s+(?:bloqu[eé]|actif|valide|suspect)', n):
        raw_action = 'STATUT_CLIENT'
    elif re.search('bl\\s+achat|bon\\s+de\\s+r[eé]ception|r[eé]ception\\s+fournisseur', n):
        raw_action = 'GENERER_DOC'
        state['type_doc'] = 'BL_ACHAT'
    elif re.search('\\bbl\\b', n) or 'bon de livraison' in n:
        raw_action = 'GENERER_DOC'
        state['type_doc'] = 'BL'
    elif re.search('\\bbc\\b', n) or 'bon de commande' in n:
        raw_action = 'GENERER_DOC'
        state['type_doc'] = 'BC'
    elif (re.search('\\bbf\\b', n) or 'bon de fabrication' in n) and 'livraison' not in n:
        raw_action = 'GENERER_DOC'
        state['type_doc'] = 'BF'
    elif re.search('\\bof\\b', n) or 'ordre de fabrication' in n:
        raw_action = 'GENERER_DOC'
        state['type_doc'] = 'OF'
    elif any((w in n for w in ('factur', 'facturer'))) and any((w in n for w in ('créer', 'créez', 'crée', 'générer', 'générez', 'génère', 'faire', 'fais', 'établir', 'émettre'))):
        raw_action = 'GENERER_DOC'
        if not state['type_doc']:
            state['type_doc'] = 'FACTURE'
    elif any((w in n for w in ('facture', 'factures'))) and any((w in n for w in ('impayé', 'impayés', 'non réglé', 'non reglé', 'non payé', 'en attente', 'souffrance'))):
        raw_action = 'FACTURES_NON_REGLEES'
        m_seuil = re.search('(?:plus\\s+de|sup[eé]rieur(?:es?)?\\s+[àa]|au[- ]del[àa]\\s+de)\\s+(\\d+)\\s+jours?', n, re.IGNORECASE)
        if m_seuil:
            state['seuil_jours_impaye'] = float(m_seuil.group(1))
    elif any((w in n for w in ('facture', 'factures'))) and any((w in n for w in ('client', 'tiers', 'pour', 'de', 'ses', 'son', 'sa', 'leur'))):
        raw_action = 'TOUTES_FACTURES_CLIENT'
    elif (any((w in n for w in ('facture', 'factures', 'document', 'documents'))) or re.search('\\bbl\\b', n)) and (any((w in n for w in ('période', 'periode', 'mois', 'année', 'annee', 'entre'))) or re.search('\\bdu\\b', n) or re.search('\\bau\\b', n)):
        raw_action = 'DOCS_PERIODE'
    elif _RX_TRIG_TOP.search(n) and _RX_TRIG_CLIENT.search(n):
        raw_action = 'TOP_CLIENTS'
    elif any((w in n for w in ('information', 'informations', 'info', 'renseign', 'fiche', 'détail', 'detail', 'profil', 'qui est', 'présente', 'dis moi', 'connais', 'connaître', 'tout sur', 'données sur', 'données du'))) and any((w in n for w in ('client', 'tiers', 'société', 'entreprise'))):
        raw_action = 'FICHE_CLIENT'
    elif any((w in n for w in ('statut', 'status', 'validité', 'validite'))) and any((w in n for w in ('client', 'tiers'))):
        raw_action = 'STATUT_CLIENT'
    elif 'encours' in n and 'client' in n:
        raw_action = 'LIRE_ENCOURS_CLIENT'
    elif 'client' in n and any((w in n for w in ('bloqu', 'solvab', 'risque'))):
        raw_action = 'RFM'
    elif _RX_TRIG_LISTE.search(n) and _RX_TRIG_CLIENT.search(n) and ('facture' not in n) and ('bl' not in n) and ('livraison' not in n) and ('article' not in n):
        raw_action = 'LISTE_CLIENTS'
    elif any((w in n for w in ('produit', 'article', 'référence', 'réf'))) and re.search('\\b\\d+\\s*(?:dt|€|eur|euros?)?\\b', n) and re.search('co[uû]tent|prix|tarif', n):
        raw_action = 'NL2SQL_LIBRE'
    elif any((w in n for w in ('produit', 'article', 'référence', 'réf'))) and re.search('\\b(mieux\\s+vendus?|meilleures?\\s+ventes?|palmar[eè]s|le\\s+plus\\s+vendu|les\\s+plus\\s+vendus)\\b', n):
        raw_action = 'PALMARES_ARTICLES'
    elif any((w in n for w in ('modifier', 'modifie', 'changer', 'change', 'actualiser', 'mettre à jour', 'mettre a jour'))) and any((w in n for w in ('produit', 'article', 'référence', 'réf'))):
        raw_action = 'MODIFIER_ARTICLE'
    elif _RX_TRIG_LISTE.search(n) and _RX_TRIG_ARTICLE.search(n) and (not _RX_ARTICLES_QUALIFIES.search(n)):
        raw_action = 'LISTE_ARTICLES'
    elif _RX_TRIG_STOCK.search(n) and any((w in n for w in ('rentre', 'sort', 'mouvement', 'ajust'))):
        raw_action = 'MOUVEMENT_STOCK'
    elif _RX_TRIG_STOCK.search(n) and (not re.search('stock.{0,20}(inf[ée]r|sup[ée]r|<|>|nul|z[ée]ro)', n)):
        raw_action = 'VERIFIER_STOCK'
    elif 'dashboard' in n or 'kpi' in n:
        raw_action = 'DASHBOARD_EXCEL'
    elif 'avoir' in n and (state['num_piece'] or re.search('\\b[A-Z]{2,4}\\d{3,}\\b', db)):
        raw_action = 'CREER_AVOIR'
    elif any((w in n for w in ('regle', 'payer', 'solder', 'règlement', 'régler'))):
        raw_action = 'REGLEMENT'
    elif 'commande' in n and any((w in n for w in ('créer', 'passer', 'enregistr', 'nouveau'))):
        raw_action = 'WORKFLOW_COMMANDE'
    elif any((w in n for w in ('ca ', "chiffre d'affaires", 'chiffre d affaires'))) and 'client' not in n:
        if _RX_CA_AVEC_PERIODE.search(n):
            raw_action = 'NL2SQL_LIBRE'
        else:
            raw_action = 'CA_GLOBAL'
    elif any((w in n for w in ('dso', 'délai', 'delai', 'retard de paiement'))):
        raw_action = 'DSO'
    elif any((w in n for w in ('rentabilit', 'marge', 'profit'))):
        raw_action = 'RENTABILITE'
    elif re.search('clas(?:se|ser|s[eé]s?)\\s+les\\s+clients?', n):
        raw_action = 'NL2SQL_LIBRE'
    elif any((w in n for w in ('saison', 'mensuel', 'tendance', 'évolution', 'evolution'))):
        raw_action = 'SAISONNALITE'
    toutes = ACTIONS_LECTURE | ACTIONS_NL2SQL | ACTIONS_EXPORT | ACTIONS_ECRITURE | ACTIONS_WORKFLOW | ACTIONS_KB | ACTIONS_HUB | {'AMBIGUE'}
    if not raw_action or raw_action not in toutes:
        print(f"   ⚠️  [Classifier] '{raw_action}' invalide → NL2SQL_LIBRE")
        raw_action = 'NL2SQL_LIBRE'
        state['_origine_classification'] = 'FALLBACK'
    state['action'] = raw_action
    if state['type_doc']:
        hub_doc = await _hub_resoudre_type_doc(state['type_doc'])
        state['type_doc_code'] = hub_doc.get('DO_Type', 0)
    if state['action'] == 'GENERER_DOC':
        if not state['ref_article'] and state['dernier_ref_article']:
            state['ref_article'] = state['dernier_ref_article']
        if not quantite_explicite and state['dernier_quantite'] > 0:
            state['quantite'] = state['dernier_quantite']
        if state['type_doc'] == 'BF' and (not state['num_piece']):
            m_of = re.search('(?:ordre de fabrication|\\bof\\b)\\s*(?:n[°o]?\\s*)?0*(\\d{3,})', n)
            if m_of:
                state['num_piece'] = f'OF{int(m_of.group(1)):05d}'
            elif re.match('^OF\\d+$', state.get('dernier_num_piece', ''), re.IGNORECASE):
                state['num_piece'] = state['dernier_num_piece']
                print(f"   🔗 [BF] Lié au dernier OF : {state['num_piece']}")
    if state['type_doc'] in TYPES_DOC_FABRICATION:
        ca = state.get('code_client', '')
        if not ca:
            state['code_client'] = 'PROD-INT'
        elif ca.upper() == state.get('ref_article', '').upper():
            state['code_client'] = 'PROD-INT'
        elif not re.match('^([A-Z]{2,6}\\d+|PROD)', ca, re.IGNORECASE):
            state['code_client'] = 'PROD-INT'
    if state['action'] in ('TRANSFORMER_DOC', 'CREER_AVOIR', 'REGLEMENT') and (not state['num_piece']):
        ref_det = any((m in n for m in MOTS_REFERENCE_DOCUMENT))
        if state['dernier_num_piece'] and ref_det:
            state['num_piece'] = state['dernier_num_piece']
            state['type_doc'] = state['type_doc'] or state['dernier_type_doc']
            state['code_client'] = state['code_client'] or state['dernier_code_client']
            state['ref_article'] = state['ref_article'] or state['dernier_ref_article']
    _a_client = bool(state.get('code_client') or state.get('nom_client_brut'))
    if state['action'] in ('VERIFIER_STOCK', 'FICHE_CLIENT', 'STATUT_CLIENT') and (not (_a_client or state['ref_article'])):
        state['ambigue'] = True
    if state['action'] in ('TOUTES_FACTURES_CLIENT', 'FICHE_CLIENT', 'STATUT_CLIENT', 'LIRE_ENCOURS_CLIENT') and (not _a_client):
        state['ambigue'] = True
    if state['action'] == 'GENERER_DOC':
        type_d = (state['type_doc'] or '').upper()
        if type_d not in TYPES_DOC_FABRICATION and (not _a_client):
            state['ambigue'] = True
        if not state['ref_article']:
            state['ambigue'] = True
        if state['quantite'] <= 0.0:
            state['ambigue'] = True
    if state['action'] in ('TRANSFORMER_DOC', 'CREER_AVOIR', 'REGLEMENT') and (not state['num_piece']):
        state['ambigue'] = True
    if state['action'] == 'DOCS_PERIODE' and (not (state.get('date_debut') and state.get('date_fin'))):
        state['ambigue'] = True
    if state['action'] == 'CREER_CLIENT' and (not (state.get('nom_client_brut') and state.get('code_client'))):
        state['ambigue'] = True
    if state['score_confiance'] < SEUIL_CONFIANCE and (not state['ambigue']):
        state['ambigue'] = True
    elapsed = time.perf_counter() - t0
    print(f"   Action    : {state['action']} {('[AMBIGUE]' if state['ambigue'] else '')}\n   Confiance : {state['score_confiance']:.2f}\n   Client: {state.get('code_client') or state.get('nom_client_brut') or '—'} | Article: {state['ref_article'] or '—'} | Qté: {state['quantite']} | Pièce: {state['num_piece'] or '—'}\n   ⏱️  {elapsed:.2f}s")
    return state

async def _generer_code_client(nom: str) -> str:
    """
Générer un code client unique en fonction du nom d'un client.
"""
    try:
        raw = await mcp_pool.call('actions', 'generer_prochain_code', {'prefixe': 'CLI'})
        data = _parse_mcp_response(raw)
        if data.get('statut') == 'OK' and data.get('code'):
            return data['code']
    except Exception as e:
        print(f'   ⚠️  [_generer_code_client] MCP indisponible ({e}) → repli local')
    import unicodedata
    nom_clean = unicodedata.normalize('NFD', nom or '')
    nom_clean = ''.join((c for c in nom_clean if unicodedata.category(c) != 'Mn'))
    nom_clean = re.sub('[^A-Za-z0-9]', '', nom_clean).upper()
    return f'CLI{nom_clean[:5]}' if nom_clean else 'CLI001'

async def _generer_code_fournisseur(nom: str) -> str:
    """
Générer un code fournisseur à partir d'un nom.
"""
    try:
        raw = await mcp_pool.call('actions', 'generer_prochain_code', {'prefixe': 'FOUR'})
        data = _parse_mcp_response(raw)
        if data.get('statut') == 'OK' and data.get('code'):
            return data['code']
    except Exception as e:
        print(f'   ⚠️  [_generer_code_fournisseur] MCP indisponible ({e}) → repli local')
    import unicodedata
    nom_clean = unicodedata.normalize('NFD', nom or '')
    nom_clean = ''.join((c for c in nom_clean if unicodedata.category(c) != 'Mn'))
    nom_clean = re.sub('[^A-Za-z0-9]', '', nom_clean).upper()
    return f'FOUR{nom_clean[:4]}' if nom_clean else 'FOUR001'
_CHAMPS_CONFIRMATION: dict[str, list[str]] = {'GENERER_DOC': ['client', 'article', 'quantite'], 'CREER_CLIENT': ['client_nom'], 'CREER_FOURNISSEUR': ['client_nom'], 'MODIFIER_STATUT': ['client'], 'MODIFIER_ARTICLE': ['article'], 'TRANSFORMER_DOC': ['piece', 'type_doc'], 'CREER_AVOIR': ['piece'], 'REGLEMENT': ['piece', 'mode_paiement'], 'MOUVEMENT_STOCK': ['article', 'quantite'], 'PROPOSITION_ACHAT': ['article', 'quantite']}

def _construire_detail_confirmation(state: 'CopilotState') -> str:
    """
Construit le détail de confirmation d'une commande en fonction de l'état et de l'action réalisée.
"""
    act = state.get('action', '')
    champs = _CHAMPS_CONFIRMATION.get(act, ['client', 'article', 'quantite'])
    parts: list[str] = []
    if 'client' in champs or 'client_nom' in champs:
        if act == 'MODIFIER_STATUT' and state.get('code_fournisseur'):
            code = state.get('code_fournisseur', '')
            nom = state.get('nom_client_brut', '')
            txt = f'Fournisseur: {code}' if code else 'Fournisseur: —'
            if nom:
                txt += f' ({nom})'
            parts.append(txt)
        elif act == 'CREER_FOURNISSEUR':
            code = state.get('code_client', '')
            nom = state.get('nom_client_brut', '')
            txt = f'Fournisseur: {code}' if code else 'Fournisseur: —'
            if nom:
                txt += f' ({nom})'
            parts.append(txt)
        else:
            code = state.get('code_client', '')
            nom = state.get('nom_client_brut', '')
            txt = f'Client: {code}' if code else 'Client: —'
            if nom:
                txt += f' ({nom})'
            parts.append(txt)
    if 'article' in champs:
        parts.append(f"Article: {state.get('ref_article') or '—'}")
    if 'quantite' in champs:
        qte = state.get('quantite')
        parts.append(f"Qté: {(qte if qte else '—')}")
    if 'piece' in champs:
        parts.append(f"Pièce: {state.get('num_piece') or '—'}")
    if 'type_doc' in champs:
        parts.append(f"Type: {state.get('type_doc') or '—'}")
    if 'mode_paiement' in champs:
        mode = state.get('mode_paiement') or '—'
        parts.append(f'Mode: {mode}')
        if state.get('numero_piece_paiement'):
            parts.append(f"N° pièce: {state['numero_piece_paiement']}")
    detail = ' | ' + ' | '.join(parts) if parts else ''
    if act in ('CREER_CLIENT', 'CREER_FOURNISSEUR'):
        if state.get('intitule'):
            if state.get('nom_client_brut') and state.get('intitule') != state.get('nom_client_brut'):
                detail += f" | Raison sociale: {state['intitule']}"
            elif not state.get('nom_client_brut'):
                detail += f" | Raison sociale: {state['intitule']}"
        extras = []
        for key, label in [('adresse', 'Adresse'), ('complement', 'Complément'), ('code_postal', 'Code postal'), ('ville', 'Ville'), ('pays', 'Pays'), ('contact', 'Contact'), ('telephone', 'Téléphone'), ('email', 'Email'), ('site', 'Site')]:
            value = state.get(key, '')
            extras.append(f"{label}: {value or '—'}")
        detail += ' | ' + ' | '.join(extras)
    if act == 'CREER_CLIENT' and 'ct_validite' in champs:
        detail += f" | Statut: {state.get('ct_validite', 'VALIDE')}"
    return detail

async def noeud_planner(state: CopilotState) -> CopilotState:
    """
Cette fonction génère un plan pour la planification d'opérations.
"""
    return await _noeud_planner(state, ACTIONS_LECTURE, ACTIONS_EXPORT, ACTIONS_KB, ACTIONS_NL2SQL, _invoke_llm, PLANNER_TIMEOUT)

async def noeud_hors_sujet(state: CopilotState) -> CopilotState:
    """
Rôle de la fonction : Traiter un état de Copilot en vérifiant si un élément est hors sujet.
"""
    return await _noeud_hors_sujet(state, _invoke_llm, CAPACITES_SYSTEME)

async def noeud_aide(state: CopilotState) -> CopilotState:
    """
Fonction qui effectue un appel à l'API LLAMA pour obtenir l'aide.
"""
    return await _noeud_aide(state, _invoke_llm, CAPACITES_SYSTEME)

async def noeud_clarification(state: CopilotState) -> CopilotState:
    """
Fonction qui réalise la clarification d'un état de l'assistant, en l'envoyant à un modèle de langage.
"""
    return await _noeud_clarification(state, _invoke_llm)

async def noeud_confirmation(state: CopilotState) -> CopilotState:
    """
Procédure asynchrone pour valider la confirmation d'un noeud de fabrication.
"""
    return await _noeud_confirmation(state, TYPES_DOC_FABRICATION, _hub_valider_demande, _construire_detail_confirmation)

async def noeud_lecture(state: CopilotState) -> CopilotState:
    """
Lecture d'un noeud dans l'état actuel du Copilot.
"""
    return await _noeud_lecture(state, _rechercher_client_par_nom, _safe_str)

async def noeud_nl2sql_libre(state: CopilotState) -> CopilotState:
    """
Transforme un modèle NL2SQL en une requête SQL.
"""
    return await _noeud_nl2sql_libre(state, ENABLE_VANNA, _vanna_client, _vanna_generer_sql, _vanna_entrainer_schema, _safe_str)

async def noeud_ecriture(state: CopilotState) -> CopilotState:
    """
Fonction utilisée pour traiter les données de réponse d'un appel à l'API de l'émetteur MCP.
"""
    return await _noeud_ecriture(state, _STATUTS_ERREUR_MCP, _mcp_workflow_bl_achat, _mcp_workflow_bl, _mcp_workflow_of, _mcp_workflow_bf, _parse_mcp_response, _safe_str)

async def noeud_workflow(state: CopilotState) -> CopilotState:
    """
Asyncronise l'exécution du workflow en réponse à l'état actuel.
"""
    return await _noeud_workflow(state, _hub_contexte_client, _mcp_workflow_bl, _mcp_workflow_of, _mcp_workflow_bf, _parse_mcp_response, _input, _safe_str)

async def noeud_synthese(state: CopilotState) -> CopilotState:
    """
Fonction utilisée pour la synthèse d'un état dans un arbre de décision, en utilisant différentes configurations et paramètres.
"""
    return await _noeud_synthese(state, _FORMATEURS_JSON, ACTIONS_KB, ACTIONS_EXPORT, ENABLE_LLM_SYNTHESE, SYNTHESE_TIMEOUT, ENABLE_MEM0, _mem0_sauvegarder, _invoke_llm, _formater_reponse_directe, _safe_str)

async def _executer_suggestion(suggestion: dict, contexte_session: dict) -> str:
    """
Fonction asynchrone pour exécuter une suggestion.
"""
    return await __executer_suggestion(suggestion, contexte_session, _STATUTS_ERREUR_MCP, _parse_mcp_response, _mcp_workflow_bf, _mcp_workflow_of, _mcp_workflow_bl, generer_preview, _safe_str)

async def noeud_kb(state: CopilotState) -> CopilotState:
    """
Cette fonction est un wrapper asynchrone autour de la fonction `_noeud_kb` qui traite un noeud du graphe KB.
"""
    return await _noeud_kb(state, _safe_str)

async def noeud_modification(state: CopilotState) -> CopilotState:
    """
Fonction pour effectuer une modification sur un noeud et retourner le nouvel état du système.
"""
    return await _noeud_modification(state, _parse_mcp_response)

async def noeud_modification_confirmation(state: CopilotState) -> CopilotState:
    """
Détermine si la modification du noeud nécessite de la confirmation.
"""
    return await _noeud_modification_confirmation(state, _parse_mcp_response)

def _construire_graphe() -> object:
    """
Crée un graphe de navigation entre différents états d'un système.
"""
    g = StateGraph(CopilotState)
    g.add_node('classifier', noeud_classifier)
    g.add_node('planner', noeud_planner)
    g.add_node('hors_sujet', noeud_hors_sujet)
    g.add_node('aide', noeud_aide)
    g.add_node('clarification', noeud_clarification)
    g.add_node('lecture', noeud_lecture)
    g.add_node('nl2sql', noeud_nl2sql_libre)
    g.add_node('confirmation', noeud_confirmation)
    g.add_node('ecriture', noeud_ecriture)
    g.add_node('workflow', noeud_workflow)
    g.add_node('kb', noeud_kb)
    g.add_node('synthese', noeud_synthese)
    g.add_node('modification', noeud_modification)
    g.add_node('modification_confirmation', noeud_modification_confirmation)
    g.add_node('creation_article', _noeud_creation_article)
    g.add_node('nomenclature', _noeud_nomenclature)
    g.add_node('modification_nomenclature', _noeud_modification_nomenclature)
    g.add_node('complements', noeud_complements)
    g.add_node('collecte_draft', noeud_collecte_draft)
    g.add_node('preview_draft', noeud_preview_draft)
    g.add_node('execution_draft', noeud_execution_draft)
    g.add_edge(START, 'classifier')

    def _router(state: CopilotState) -> str:
        """
Routage de l'état de l'application en fonction de la situation actuelle.
"""
        if state.get('modification_en_cours'):
            return 'modification_confirmation'
        if state.get('creation_article_en_cours'):
            return 'creation_article'
        if state.get('nomenclature_en_cours'):
            return 'nomenclature'
        if state.get('modification_nomenclature_en_cours'):
            return 'modification_nomenclature'
        intention = state.get('intention', 'ERP')
        if intention == 'HORS_SUJET':
            return 'hors_sujet'
        if intention == 'AIDE':
            return 'aide'
        if state.get('attente_complements'):
            return 'complements'
        act = state.get('action', '')
        if act == 'GENERER_DOC' or act == 'OFFRE_PRIX':
            return 'collecte_draft'
        if state.get('ambigue'):
            return 'clarification'
        if act in ACTIONS_LECTURE | ACTIONS_EXPORT:
            return 'lecture'
        if act in ACTIONS_NL2SQL:
            return 'nl2sql'
        if act == 'TRANSFORMER_DOC':
            return 'collecte_draft'
        if act == 'CREER_ARTICLE':
            return 'creation_article'
        if act == 'CREER_NOMENCLATURE':
            return 'nomenclature'
        if act == 'MODIFIER_NOMENCLATURE':
            return 'modification_nomenclature'
        if act in ('MODIFIER_ENTITE', 'MODIFIER_CLIENT', 'MODIFIER_FOURNISSEUR', 'MODIFIER_ARTICLE'):
            return 'modification'
        if act in ACTIONS_ECRITURE | ACTIONS_WORKFLOW:
            return 'confirmation'
        if act in ACTIONS_KB:
            return 'kb'
        return 'nl2sql'
    g.add_conditional_edges('classifier', _router, {'hors_sujet': 'hors_sujet', 'aide': 'aide', 'clarification': 'clarification', 'lecture': 'lecture', 'nl2sql': 'nl2sql', 'collecte_draft': 'collecte_draft', 'confirmation': 'confirmation', 'kb': 'kb', 'complements': 'complements', 'modification': 'modification', 'modification_confirmation': 'modification_confirmation', 'creation_article': 'creation_article', 'nomenclature': 'nomenclature', 'modification_nomenclature': 'modification_nomenclature'})

    def _router_collecte(state: CopilotState) -> str:
        """
Cette fonction détermine le statut de la synthèse en fonction de l'état de collecte.
"""
        statut = state.get('statut_draft', '')
        if statut == 'COLLECTE':
            return 'synthese'
        if statut == 'ANNULE':
            return 'synthese'
        if statut == 'PREVIEW':
            return 'preview_draft'
        if statut == 'CONFIRME':
            return 'execution_draft'
        return 'synthese'
    g.add_conditional_edges('collecte_draft', _router_collecte, {'synthese': 'synthese', 'preview_draft': 'preview_draft', 'execution_draft': 'execution_draft'})
    g.add_edge('preview_draft', 'synthese')
    g.add_edge('execution_draft', 'synthese')

    def _router_confirmation(state: CopilotState) -> str:
        """
Cette fonction détermine le type de tâche à effectuer en fonction de l'état actuel du copilote.
"""
        if not state.get('validation_ok', False):
            return 'synthese'
        act = state.get('action', '')
        if act == 'WORKFLOW_COMMANDE':
            return 'workflow'
        return 'ecriture'
    g.add_conditional_edges('confirmation', _router_confirmation, {'synthese': 'synthese', 'workflow': 'workflow', 'ecriture': 'ecriture'})

    def _router_ecriture(state: CopilotState) -> str:
        """
Permet de déterminer si la synthèse de l'écriture doit prendre en compte les compléments ou non.
"""
        if state.get('attente_complements'):
            return 'complements'
        return 'synthese'
    g.add_conditional_edges('ecriture', _router_ecriture, {'complements': 'complements', 'synthese': 'synthese'})
    g.add_edge('complements', 'synthese')
    for noeud in ('hors_sujet', 'aide', 'clarification', 'lecture', 'nl2sql', 'workflow', 'kb'):
        g.add_edge(noeud, 'synthese')
    g.add_edge('modification', 'synthese')
    g.add_edge('modification_confirmation', 'synthese')
    g.add_edge('creation_article', 'synthese')
    g.add_edge('nomenclature', 'synthese')
    g.add_edge('modification_nomenclature', 'synthese')
    g.add_edge('synthese', END)
    return g.compile()

async def traiter_commande_speciale(demande: str) -> str | None:
    """
Rétourne la description de l'exception levée par la fonction si elle ne fonctionne pas correctement.
"""
    if demande.strip().lower() == 'vanna_retrain':
        if _vanna_client is None:
            return '⚠️  [Vanna] Client non initialisé — relancez le serveur avec ENABLE_VANNA=true.'
        global _vanna_retrain_lock
        if _vanna_retrain_lock is None:
            _vanna_retrain_lock = asyncio.Lock()
        if _vanna_retrain_lock.locked():
            return '⏳ [Vanna] Un ré-entraînement est déjà en cours. Réessayez dans quelques secondes.'
        async with _vanna_retrain_lock:
            global _vanna_train_count
            try:
                training_data = await asyncio.to_thread(lambda: _vanna_client.get_training_data())
                if training_data is not None and (not training_data.empty):
                    for item in training_data.to_dict('records'):
                        await asyncio.to_thread(lambda item=item: _vanna_client.remove_training_data(item.get('id')))
            except Exception as e:
                print(f'⚠️  [Vanna] Purge échouée : {e}')
            _vanna_train_count = 0
            await asyncio.to_thread(lambda: _vanna_entrainer_schema(_vanna_client))
            return '✅ Vanna ré-entraîné proprement.'
    return None

async def main():
    """
Cette fonction effectue l'initialisation des composants du programme, notamment le chargement parallèle des composants.
"""
    vanna_status = 'ON ✨' if ENABLE_VANNA else 'OFF'
    gliner_status = 'ON' if ENABLE_GLINER else 'OFF'
    mem0_status = 'ON' if ENABLE_MEM0 else 'OFF'
    fallback_status = f'✅ {FALLBACK_MODEL}' if FALLBACK_KEY else '❌ non configuré'
    print(f"\n═════════════════════════════════════════════════════════════════\n🤖  COPILOT ERP SAGE 100 — v9.6 (patchée)\n    Fast : {MODELE_FAST}  (timeout {OLLAMA_TIMEOUT_FAST}s)\n    Smart: {MODELE_SMART} (timeout {OLLAMA_TIMEOUT_SMART}s)\n    Fallback : {fallback_status}\n    GLiNER: {gliner_status} | Vanna: {vanna_status} | Mem0: {mem0_status}\n    ✅ FIX1-7 (v9.3) + PATCH D/E/F/G/H/H2/#4/J/K (v9.4) + PATCH L/M/N/O/P (v9.6)\n    (tapez 'aide', 'cache', 'warmup', 'reset', 'quitter')\n═════════════════════════════════════════════════════════════════\n")
    print('⏳ [Init] Chargement parallèle des composants...')
    init_tasks = [mcp_pool.init(), _warmup_ollama()]
    if ENABLE_SEMANTIC_CLASSIFIER:
        init_tasks.append(sc.warmup_semantic_classifier())
    init_tasks.append(asyncio.to_thread(lambda: __import__('llm_anonymizer').preload()))
    if ENABLE_VANNA:
        init_tasks.append(_get_vanna_async())
    if ENABLE_GLINER:
        init_tasks.append(_get_gliner_async())
    if ENABLE_MEM0:
        init_tasks.append(_get_mem0_async())
    await asyncio.gather(*init_tasks, return_exceptions=True)
    print('✅ [Init] Prêt.\n')
    graphe = _construire_graphe()
    contexte_session: dict = {'dernier_code_client': '', 'dernier_ref_article': '', 'dernier_quantite': 0.0, 'dernier_nom_client': '', 'dernier_document': {}, 'dernier_num_piece': '', 'dernier_type_doc': '', 'suggestion_en_attente': {}, 'document_draft': {}, 'statut_draft': '', 'alertes_persistantes': [], 'pending_action': {}, 'statut_confirmation': '', 'modification_en_cours': {}, 'creation_article_en_cours': {}, 'nomenclature_en_cours': {}}
    demande_precedente = ''
    while True:
        try:
            demande = await _input('\n👤 Votre demande : ')
            demande = demande.strip()
            if not demande:
                continue
            if demande.lower() == 'quitter':
                print('👋 Au revoir !')
                break
            if demande.lower() == 'reset':
                contexte_session = {'dernier_code_client': '', 'dernier_ref_article': '', 'dernier_quantite': 0.0, 'dernier_nom_client': '', 'dernier_document': {}, 'suggestion_en_attente': {}, 'dernier_num_piece': '', 'dernier_type_doc': '', 'document_draft': {}, 'statut_draft': '', 'alertes_persistantes': [], 'pending_action': {}, 'statut_confirmation': '', 'modification_en_cours': {}, 'creation_article_en_cours': {}, 'nomenclature_en_cours': {}}
                demande_precedente = ''
                print('🔄 Session réinitialisée.')
                continue
            if demande.lower() == 'cache':
                await response_cache.invalidate_writes()
                print('🗑️  Cache vidé.')
                continue
            if demande.lower() == 'warmup':
                await _warmup_ollama()
                continue
            if demande.lower() == 'session':
                print(f'📋 Session : {json.dumps(contexte_session, ensure_ascii=False, indent=2)}')
                continue
            if demande.lower() == 'aide':
                print(f'\n{CAPACITES_SYSTEME}\n')
                continue
            if demande.lower() == 'vanna_retrain':
                reponse = await traiter_commande_speciale('vanna_retrain')
                print(reponse)
                continue
            statut_conf_session = contexte_session.get('statut_confirmation')
            statut_draft_session = contexte_session.get('statut_draft')
            if statut_conf_session == 'ATTENTE' and (_est_oui(demande) or _est_non(demande)):
                pending = contexte_session.get('pending_action', {})
                if _est_non(demande):
                    contexte_session['pending_action'] = {}
                    contexte_session['statut_confirmation'] = ''
                    print(f"\n{'─' * 65}\n📡 COPILOT ERP :\n{'─' * 65}")
                    print('🛑 Action annulée.')
                    print(f"{'─' * 65}\n")
                    continue
                etat = _etat_initial('', contexte_session)
                etat.update(pending)
                if not etat.get('pending_document'):
                    etat['pending_document'] = contexte_session.get('pending_document', {})
                etat['action'] = pending.get('action', '')
                etat['intention'] = 'ERP'
                etat['statut_confirmation'] = 'CONFIRME'
                etat['validation_ok'] = True
                try:
                    if etat['action'] in ACTIONS_WORKFLOW:
                        final_state = await noeud_workflow(etat)
                    else:
                        final_state = await noeud_ecriture(etat)
                    if final_state.get('attente_complements'):
                        final_state = await noeud_complements(final_state)
                    final_state = await noeud_synthese(final_state)
                except Exception as e:
                    final_state = {**etat, 'reponse_finale': f'❌ Erreur système : {_safe_str(e)}'}
                contexte_session['pending_action'] = {}
                contexte_session['statut_confirmation'] = ''
                contexte_session['pending_document'] = {}
                for _champ in ('intitule', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site', 'ct_validite'):
                    contexte_session[_champ] = ''
                if final_state.get('code_client'):
                    contexte_session['dernier_code_client'] = final_state['code_client']
                if final_state.get('ref_article'):
                    contexte_session['dernier_ref_article'] = final_state['ref_article']
                if final_state.get('quantite', 0) > 0:
                    contexte_session['dernier_quantite'] = final_state['quantite']
                if final_state.get('nom_client_brut'):
                    contexte_session['dernier_nom_client'] = final_state['nom_client_brut']
                for _champ in ('intitule', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site', 'ct_validite', 'code_fournisseur'):
                    _val = final_state.get(_champ)
                    if _val not in (None, '', 0.0):
                        contexte_session[_champ] = _val
                doc_extrait = _extraire_dernier_document(final_state)
                if doc_extrait and doc_extrait.get('type_doc', '') not in ('OF', 'BF'):
                    contexte_session['dernier_document'] = doc_extrait
                    if doc_extrait.get('num_piece'):
                        contexte_session['dernier_num_piece'] = doc_extrait['num_piece']
                        contexte_session['dernier_type_doc'] = doc_extrait.get('type_doc', '')
                sugg_nouvelle = final_state.get('suggestion_en_attente', {})
                contexte_session['suggestion_en_attente'] = sugg_nouvelle
                if final_state.get('attente_complements'):
                    contexte_session['attente_complements'] = True
                    contexte_session['pending_document'] = final_state.get('pending_document', {})
                elif final_state.get('statut_confirmation') == 'ATTENTE':
                    contexte_session['attente_complements'] = False
                else:
                    contexte_session['attente_complements'] = False
                    contexte_session['pending_document'] = {}
                reponse = final_state.get('reponse_finale', '⚠️  Aucune réponse.')
                print(f"\n{'─' * 65}\n📡 COPILOT ERP :\n{'─' * 65}")
                print(reponse)
                if sugg_nouvelle:
                    desc = sugg_nouvelle.get('description', '')
                    print(f'\n💡 Suggestion : {desc}\n   Tapez **ok** pour confirmer ou **non** pour annuler.')
                alertes_txt = formater_alertes_persistantes(contexte_session)
                if alertes_txt:
                    print(alertes_txt)
                print(f"{'─' * 65}\n")
                continue
            elif statut_draft_session in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE') or statut_draft_session.startswith('ATTENTE_PRIX'):
                sugg = {}
            else:
                sugg = contexte_session.get('suggestion_en_attente', {})
                if sugg:
                    piece_ref = sugg.get('params', {}).get('num_br') or sugg.get('params', {}).get('num_bl') or sugg.get('params', {}).get('num_of') or ''
                    demande_norm = demande.strip().lower()
                    desc_norm = sugg.get('description', '').strip().lower()
                    est_confirmation_sugg = _est_oui(demande) or demande_norm == desc_norm or (piece_ref and piece_ref.lower() in demande_norm)
                    if est_confirmation_sugg:
                        reponse_sugg = await _executer_suggestion(sugg, contexte_session)
                        print(f"\n{'─' * 65}\n📡 COPILOT ERP :\n{'─' * 65}")
                        print(reponse_sugg)
                        print(f"{'─' * 65}\n")
                        continue
                    elif _est_non(demande):
                        contexte_session['suggestion_en_attente'] = {}
                        print('🛑 Suggestion annulée.')
                        continue
                    else:
                        contexte_session['suggestion_en_attente'] = {}
            demande_resolue = _resoudre_references(demande, contexte_session.get('dernier_document', {}))
            if demande_precedente and (_est_oui(demande_resolue) or _est_non(demande_resolue)):
                demande_precedente = ''
                print(f"\n{'─' * 65}\n📡 COPILOT ERP :\n{'─' * 65}")
                if _est_non(demande_resolue):
                    print("🛑 D'accord, dites-moi si vous avez besoin d'autre chose.")
                else:
                    print('👍 Très bien, comment puis-je vous aider ensuite ?')
                print(f"{'─' * 65}\n")
                continue
            sous_pieces = _decouper_reglement_multiple(demande_resolue)
            if sous_pieces:
                sous_demandes = [{'demande': d, 'sequentiel': False, 'index': i} for i, d in enumerate(sous_pieces)]
            else:
                sous_demandes = await decouper_demande_composite(demande_resolue)
            reponses_multi = []
            for sous_d in sous_demandes:
                demande_courante = sous_d['demande']
                if demande_precedente and (not sugg):
                    demande_courante = _fusionner_demandes(demande_precedente, demande_courante)
                etat = _etat_initial(demande_courante, contexte_session)
                if contexte_session.get('attente_complements'):
                    etat['attente_complements'] = True
                    etat['pending_document'] = contexte_session.get('pending_document', {})
                if contexte_session.get('document_draft'):
                    etat['document_draft'] = contexte_session['document_draft']
                    etat['statut_draft'] = contexte_session.get('statut_draft', '')
                if contexte_session.get('pending_action') and contexte_session.get('statut_confirmation') == 'ATTENTE':
                    etat['pending_action'] = contexte_session['pending_action']
                    etat['statut_confirmation'] = 'ATTENTE'
                try:
                    final_state = await graphe.ainvoke(etat)
                    reponse = final_state.get('reponse_finale', '⚠️  Aucune réponse.')
                    contexte_session['document_draft'] = final_state.get('document_draft', {})
                    contexte_session['statut_draft'] = final_state.get('statut_draft', '')
                    if final_state.get('statut_confirmation') == 'ATTENTE':
                        contexte_session['pending_action'] = final_state.get('pending_action', {})
                        contexte_session['statut_confirmation'] = 'ATTENTE'
                    else:
                        contexte_session['pending_action'] = {}
                        contexte_session['statut_confirmation'] = ''
                        contexte_session['pending_document'] = {}
                        for _champ in ('intitule', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site', 'ct_validite'):
                            contexte_session[_champ] = ''
                    contexte_session['modification_en_cours'] = final_state.get('modification_en_cours', {})
                    contexte_session['creation_article_en_cours'] = final_state.get('creation_article_en_cours', {})
                    contexte_session['nomenclature_en_cours'] = final_state.get('nomenclature_en_cours', {})
                    if final_state.get('action') == 'GENERER_DOC' and final_state.get('statut_draft') == '':
                        type_doc_genere = (final_state.get('type_doc') or '').upper()
                        if type_doc_genere == 'OF' and final_state.get('num_piece'):
                            ajouter_alerte_bf_requis(contexte_session, num_of=final_state['num_piece'], ref_article=final_state.get('ref_article', ''), qte_prevue=final_state.get('quantite', 0.0))
                        elif type_doc_genere == 'BF':
                            num_of_lie = final_state.get('num_of_resolu', '') or contexte_session.get('dernier_num_piece', '')
                            if num_of_lie:
                                resoudre_alerte_bf(contexte_session, num_of_lie)
                    if final_state.get('code_client'):
                        contexte_session['dernier_code_client'] = final_state['code_client']
                except Exception as e:
                    final_state = {**etat, 'reponse_finale': f'❌ Erreur système : {_safe_str(e)}'}
                    contexte_session['pending_action'] = {}
                    contexte_session['statut_confirmation'] = ''
                reponse = final_state.get('reponse_finale', '⚠️  Aucune réponse.')
                if final_state.get('code_client'):
                    contexte_session['dernier_code_client'] = final_state['code_client']
                if final_state.get('ref_article'):
                    contexte_session['dernier_ref_article'] = final_state['ref_article']
                if final_state.get('quantite', 0) > 0:
                    contexte_session['dernier_quantite'] = final_state['quantite']
                if final_state.get('nom_client_brut'):
                    contexte_session['dernier_nom_client'] = final_state['nom_client_brut']
                for _champ in ('intitule', 'adresse', 'complement', 'code_postal', 'ville', 'pays', 'contact', 'telephone', 'email', 'site', 'ct_validite', 'code_fournisseur'):
                    _val = final_state.get(_champ)
                    if _val not in (None, '', 0.0):
                        contexte_session[_champ] = _val
                doc_extrait = _extraire_dernier_document(final_state)
                if doc_extrait and doc_extrait.get('type_doc', '') not in ('OF', 'BF'):
                    contexte_session['dernier_document'] = doc_extrait
                elif not doc_extrait:
                    if final_state.get('action') not in ('GENERER_DOC',):
                        contexte_session['dernier_document'] = {}
                if doc_extrait:
                    num_p = doc_extrait.get('num_piece', '')
                    type_p = doc_extrait.get('type_doc', '')
                    if num_p:
                        contexte_session['dernier_num_piece'] = num_p
                        contexte_session['dernier_type_doc'] = type_p
                if not final_state.get('ambigue'):
                    contexte_session['dernier_quantite'] = 0.0
                sugg_nouvelle = final_state.get('suggestion_en_attente', {})
                contexte_session['suggestion_en_attente'] = sugg_nouvelle
                if final_state.get('attente_complements'):
                    contexte_session['attente_complements'] = True
                    contexte_session['pending_document'] = final_state.get('pending_document', {})
                elif final_state.get('statut_confirmation') == 'ATTENTE':
                    contexte_session['attente_complements'] = False
                else:
                    contexte_session['attente_complements'] = False
                    contexte_session['pending_document'] = {}
                contexte_session['modification_en_cours'] = final_state.get('modification_en_cours', {})
                contexte_session['creation_article_en_cours'] = final_state.get('creation_article_en_cours', {})
                contexte_session['nomenclature_en_cours'] = final_state.get('nomenclature_en_cours', {})
                if final_state.get('ambigue'):
                    demande_precedente = demande_courante
                else:
                    demande_precedente = ''
                reponses_multi.append(reponse)
                if sugg_nouvelle:
                    desc = sugg_nouvelle.get('description', '')
                    reponses_multi.append(f'\n💡 Suggestion : {desc}\n   Tapez **ok** pour confirmer ou **non** pour annuler.')
            print(f"\n{'─' * 65}")
            print(f'📡 COPILOT ERP :')
            print(f"{'─' * 65}")
            print('\n\n'.join(reponses_multi))
            alertes_txt = formater_alertes_persistantes(contexte_session)
            if alertes_txt:
                print(alertes_txt)
            print(f"{'─' * 65}\n")
        except KeyboardInterrupt:
            print('\n👋 Au revoir !')
            break
        except Exception as e:
            print(f'\n❌ Erreur inattendue : {_safe_str(e)}')
            print(f'   Détails : {tb.format_exc()[-500:]}')
if __name__ == '__main__':
    asyncio.run(main())