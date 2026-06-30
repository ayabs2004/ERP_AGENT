"""
orchestrateur_general.py — Le Maître Orchestrateur Multi-Agents v9.3
================================================================================
v9.3 — 7 corrections appliquées :
  FIX 1 : Extraction article insensible à la casse ([A-Za-z] + .upper())
  FIX 2 : Patterns TRANSFORMER_DOC prioritaires dans _PATTERNS_PRECLASS
  FIX 3 : Extraction num_piece + type_doc dans pre-class TRANSFORMER_DOC
  FIX 4 : Patterns NL2SQL_LIBRE pour BL/BC/BF/OF par client + filtres analytiques
  FIX 5 : _est_nom_valide rejette chiffres + mots parasites supplémentaires
  FIX 6 : _rechercher_client_par_nom len > 3 + sortie rapide si liste vide
  FIX 7 : noeud_synthese bloc NL2SQL_LIBRE dédié + SYNTHESE_TIMEOUT 120s
"""

import asyncio
import re
import json
import os
import traceback as tb
import time
import sys
import io
import warnings
import shelve
import hashlib
import itertools

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

os.environ["PYTHONUTF8"]                          = "1"
os.environ["PYTHONIOENCODING"]                    = "utf-8"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"]     = "1"
os.environ["HF_HUB_DISABLE_EXPERIMENTAL_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"]        = "1"
os.environ["TRANSFORMERS_VERBOSITY"]               = "error"
os.environ["CUDA_LAUNCH_BLOCKING"]                 = "1"


def _fix_encoding():
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
            sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
        else:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", line_buffering=True)
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    except Exception:
        pass


_fix_encoding()

from dotenv import load_dotenv
load_dotenv()

from pathlib import Path as _Path
_db_path = _Path(__file__).parent / "entreprise_mock.db"
if not _db_path.exists() or _db_path.stat().st_size < 1000:
    from init_db_complet import init_database_complete as _init_db
    print("🗄️  [DB] Initialisation automatique...")
    _init_db()
    print("✅ [DB] Base initialisée.")

from typing import TypedDict, Optional
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END
from mcp_pool import pool as mcp_pool
from response_cache import cache as response_cache

# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────
MODELE_FAST  = os.getenv("LLM_FAST",  "qwen2.5:3b")
MODELE_SMART = os.getenv("LLM_SMART", "qwen2.5:7b")

FALLBACK_URL   = os.getenv("LLM_FALLBACK_URL",   "https://api.groq.com/openai/v1")
FALLBACK_KEY   = (os.getenv("LLM_FALLBACK_KEY", "") or "").strip()
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "llama-3.3-70b-versatile")

OLLAMA_TIMEOUT_FAST   = float(os.getenv("OLLAMA_TIMEOUT_FAST",   "120"))
OLLAMA_TIMEOUT_SMART  = float(os.getenv("OLLAMA_TIMEOUT_SMART",  "300"))
OLLAMA_WARMUP_TIMEOUT = float(os.getenv("OLLAMA_WARMUP_TIMEOUT", "300"))

if os.getenv("OLLAMA_TIMEOUT"):
    _t = float(os.getenv("OLLAMA_TIMEOUT"))
    OLLAMA_TIMEOUT_FAST  = _t
    OLLAMA_TIMEOUT_SMART = max(_t, 60.0)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

SEUIL_CONFIANCE            = float(os.getenv("SEUIL_CONFIANCE", "0.60"))
ENABLE_HALLUCINATION_CHECK = os.getenv("ENABLE_HALLUCINATION_CHECK", "false").lower() == "true"
ENABLE_VANNA               = os.getenv("ENABLE_VANNA",  "false").lower() == "true"
ENABLE_MEM0                = os.getenv("ENABLE_MEM0",   "false").lower() == "true"
ENABLE_GLINER              = os.getenv("ENABLE_GLINER", "false").lower() == "true"
PLANNER_TIMEOUT            = float(os.getenv("PLANNER_TIMEOUT", "60"))

ENABLE_LLM_SYNTHESE = os.getenv("ENABLE_LLM_SYNTHESE", "true").lower() == "true"
# FIX 7 : timeout synthèse porté à 120s (était 45s — trop court pour qwen2.5:7b)
SYNTHESE_TIMEOUT       = max(120.0, float(os.getenv("SYNTHESE_TIMEOUT", "120")))  # PATCH C-1 : plancher 120s
VANNA_GENERATE_TIMEOUT = float(os.getenv("VANNA_GENERATE_TIMEOUT", "300"))  # réduit : si pas trouvé en 20s → fallback immédiat

_LLM_MAX_CONCURRENT = int(os.getenv("LLM_MAX_CONCURRENT", "2"))
_llm_semaphore: asyncio.Semaphore | None = None

_DISK_CACHE_PATH = os.getenv("DISK_CACHE_PATH", "./disk_cache_sage")
_DISK_CACHE_TTL  = float(os.getenv("DISK_CACHE_TTL", "600"))
_disk_cache_lock = None

# ─────────────────────────────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────────────────────────────
llm_fast  = ChatOllama(model=MODELE_FAST,  temperature=0)
llm_smart = ChatOllama(model=MODELE_SMART, temperature=0)

_ollama_warmed_up: dict[str, bool] = {"fast": False, "smart": False}


def _safe_str(obj) -> str:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj).encode("utf-8", errors="replace").decode("utf-8")


async def _input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


# ─────────────────────────────────────────────────────────────────────
# HELPERS OUI/NON
# ─────────────────────────────────────────────────────────────────────
_MOTS_OUI = {
    "o", "ok", "oui", "yes", "y", "ouais", "bien sûr",
    "vas-y", "vas y", "go", "allez", "parfait", "super",
    "d'accord", "daccord", "volontiers", "affirmatif",
    "faites", "faites-le", "lance", "crée", "créer",
    "génère", "genere", "fais-le", "fais le",
}
_MOTS_NON = {
    "n", "non", "no", "nope", "pas", "annuler", "annule",
    "stop", "arrête", "arrete", "laisse tomber", "laisse",
    "pas maintenant", "plus tard", "skip", "ignore",
}


def _est_oui(texte: str) -> bool:
    return texte.lower().strip().rstrip("!.") in _MOTS_OUI


def _est_non(texte: str) -> bool:
    t = texte.lower().strip().rstrip("!.")
    return t in _MOTS_NON or any(m in t for m in _MOTS_NON)


# ─────────────────────────────────────────────────────────────────────
# CACHE DISQUE
# ─────────────────────────────────────────────────────────────────────
def _disk_cache_key(action: str, **kwargs) -> str:
    raw = action + ":" + json.dumps(kwargs, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


async def _disk_cache_get(action: str, **kwargs) -> str | None:
    global _disk_cache_lock
    if _disk_cache_lock is None:
        _disk_cache_lock = asyncio.Lock()
    key = _disk_cache_key(action, **kwargs)
    try:
        async with _disk_cache_lock:
            def _read():
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
    global _disk_cache_lock
    if _disk_cache_lock is None:
        _disk_cache_lock = asyncio.Lock()
    key = _disk_cache_key(action, **kwargs)
    try:
        async with _disk_cache_lock:
            def _write():
                with shelve.open(_DISK_CACHE_PATH) as db:
                    db[key] = (value, time.monotonic())
            await asyncio.to_thread(_write)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# WARMUP OLLAMA
# ─────────────────────────────────────────────────────────────────────
async def _warmup_ollama():
    print("🔥 [Ollama] Préchauffage des modèles en mémoire...")

    async def _warm_one(key: str, llm_instance: ChatOllama, nom: str):
        print(f"   ⏳ [{nom}] Chargement en cours...")
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(
                llm_instance.ainvoke("ok"), timeout=OLLAMA_WARMUP_TIMEOUT
            )
            _ollama_warmed_up[key] = True
            print(f"   ✅ [{nom}] Prêt en {time.perf_counter() - t0:.1f}s")
        except asyncio.TimeoutError:
            print(f"   ⚠️  [{nom}] Warmup timeout après {time.perf_counter() - t0:.1f}s")
        except Exception as e:
            err = _safe_str(e)
            if any(k in err.lower() for k in ("refused", "connect", "unreachable")):
                print(f"   ❌ [{nom}] Ollama inaccessible → lancez : ollama serve")
            else:
                print(f"   ⚠️  [{nom}] Warmup échoué : {err}")

    await asyncio.gather(
        _warm_one("fast",  llm_fast,  MODELE_FAST),
        _warm_one("smart", llm_smart, MODELE_SMART),
    )
    nb_ok = sum(_ollama_warmed_up.values())
    if nb_ok == 2:
        print(f"\n🔥 [Ollama] ✅ Les 2 modèles sont prêts.\n")
    elif nb_ok == 1:
        ok = MODELE_FAST if _ollama_warmed_up["fast"] else MODELE_SMART
        print(f"\n🔥 [Ollama] ⚠️  1/2 modèle prêt ({ok}).\n")
    else:
        print(f"\n🔥 [Ollama] ❌ Aucun modèle chargé. Vérifiez : ollama serve\n")


# ─────────────────────────────────────────────────────────────────────
# INVOKE LLM
# ─────────────────────────────────────────────────────────────────────
async def _invoke_llm(
    prompt: str,
    use_smart: bool = False,
    timeout_override: float | None = None,
) -> str:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(_LLM_MAX_CONCURRENT)

    model   = llm_smart if use_smart else llm_fast
    key     = "smart"   if use_smart else "fast"
    nom     = MODELE_SMART if use_smart else MODELE_FAST
    timeout = timeout_override or (
        OLLAMA_TIMEOUT_SMART if use_smart else OLLAMA_TIMEOUT_FAST
    )
    prompt_u = prompt.encode("utf-8", errors="replace").decode("utf-8")

    async with _llm_semaphore:
        try:
            r = await asyncio.wait_for(model.ainvoke(prompt_u), timeout=timeout)
            _ollama_warmed_up[key] = True
            content = r.content
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            return content.strip()
        except asyncio.TimeoutError:
            print(f"   ⚠️  [{nom}] Timeout ({timeout}s).")
            if FALLBACK_KEY:
                return await _invoke_fallback(prompt_u)
            return (
                f"⚠️  Le modèle [{nom}] ne répond pas (timeout {timeout}s). "
                f"Veuillez réessayer ou configurer LLM_FALLBACK_KEY."
            )
        except Exception as e:
            err = _safe_str(e)
            if any(k in err.lower() for k in ("refused", "connect", "unreachable")):
                print(f"   ❌ [{nom}] Ollama inaccessible.")
                if FALLBACK_KEY:
                    return await _invoke_fallback(prompt_u)
                return "❌ Ollama inaccessible. Lancez `ollama serve`."
            if FALLBACK_KEY:
                return await _invoke_fallback(prompt_u)
            raise


async def _invoke_fallback(prompt_utf8: str) -> str:
    print(f"   🔄 Fallback → {FALLBACK_MODEL}")
    try:
        from langchain_openai import ChatOpenAI
        fb = ChatOpenAI(
            model=FALLBACK_MODEL, temperature=0,
            api_key=FALLBACK_KEY, base_url=FALLBACK_URL,
        )
        r = await fb.ainvoke(prompt_utf8)
        content = r.content
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return content.strip()
    except Exception as e2:
        return f"⚠️  Service IA temporairement indisponible. ({_safe_str(e2)})"


# ─────────────────────────────────────────────────────────────────────
# GLiNER
# ─────────────────────────────────────────────────────────────────────
_gliner_model:      object | None = None
_gliner_load_tried: bool          = False
_gliner_lock:       asyncio.Lock | None = None


def _get_gliner_sync() -> object | None:
    global _gliner_model, _gliner_load_tried
    if not ENABLE_GLINER:
        return None
    if _gliner_load_tried:
        return _gliner_model
    _gliner_load_tried = True
    try:
        from gliner import GLiNER
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


async def _get_gliner_async() -> object | None:
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


# ─────────────────────────────────────────────────────────────────────
# MEM0
# ─────────────────────────────────────────────────────────────────────
_mem0_client:     object | None = None
_mem0_load_tried: bool          = False
_mem0_lock:       asyncio.Lock | None = None

MEM0_USER_ID     = os.getenv("MEM0_USER_ID",     "erp_copilot_user")
MEM0_EMBED_MODEL = os.getenv("MEM0_EMBED_MODEL", "nomic-embed-text")
MEM0_EMBED_DIMS  = int(os.getenv("MEM0_EMBED_DIMS", "768"))
MEM0_DB_PATH     = os.getenv("MEM0_DB_PATH",     "./mem0_qdrant_db")


def _get_mem0_sync() -> object | None:
    global _mem0_client, _mem0_load_tried
    if not ENABLE_MEM0:
        return None
    if _mem0_load_tried:
        return _mem0_client
    _mem0_load_tried = True
    try:
        import logging
        logging.getLogger("mem0").setLevel(logging.ERROR)
        logging.getLogger("httpx").setLevel(logging.ERROR)
        logging.getLogger("qdrant_client").setLevel(logging.ERROR)
        from mem0 import Memory
        print("   ⏳ [Mem0] Initialisation...")
        t0 = time.perf_counter()
        _mem0_client = Memory.from_config({
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name":      "erp_copilot_memories",
                    "path":                 MEM0_DB_PATH,
                    "embedding_model_dims": MEM0_EMBED_DIMS,
                },
            },
            "llm": {
                "provider": "ollama",
                "config": {
                    "model":           MODELE_FAST,
                    "temperature":     0,
                    "ollama_base_url": OLLAMA_BASE_URL,
                },
            },
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model":           MEM0_EMBED_MODEL,
                    "ollama_base_url": OLLAMA_BASE_URL,
                },
            },
        })
        print(f"   ✅ [Mem0] Prêt en {time.perf_counter() - t0:.1f}s")
    except ImportError:
        print("   ⚠️  [Mem0] pip install mem0ai")
        _mem0_client = None
    except Exception as e:
        print(f"   ⚠️  [Mem0] {_safe_str(e)}")
        _mem0_client = None
    return _mem0_client


async def _get_mem0_async() -> object | None:
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
    mem = _get_mem0_sync()
    if mem is None:
        return ""
    try:
        results = mem.search(
            requete, filters={"user_id": MEM0_USER_ID}, limit=3
        )
        items = (
            results.get("results", results)
            if isinstance(results, dict) else results
        )
        return "\n".join(
            r["memory"] for r in items
            if isinstance(r, dict) and "memory" in r
        )
    except Exception:
        return ""


async def _mem0_rechercher(requete: str) -> str:
    return await asyncio.to_thread(_mem0_rechercher_sync, requete)


def _mem0_sauvegarder(message: str, reponse: str):
    mem = _get_mem0_sync()
    if mem is None:
        return
    try:
        mem.add(
            [{"role": "user", "content": message},
             {"role": "assistant", "content": reponse}],
            user_id=MEM0_USER_ID,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# VANNA
# ─────────────────────────────────────────────────────────────────────
_vanna_client       = None
_vanna_load_tried   = False
_vanna_lock: asyncio.Lock | None = None


def _get_vanna_sync():
    global _vanna_client, _vanna_load_tried
    if not ENABLE_VANNA:
        return None
    if _vanna_load_tried:
        return _vanna_client
    _vanna_load_tried = True
    try:
        from vanna.ollama import Ollama
        from vanna.chromadb import ChromaDB_VectorStore

        class VannaERP(ChromaDB_VectorStore, Ollama):
            def __init__(self, config=None):
                ChromaDB_VectorStore.__init__(self, config=config)
                Ollama.__init__(self, config=config)

        vn = VannaERP(config={
            "model":       MODELE_SMART,
            "ollama_host": OLLAMA_BASE_URL,
            "path":        "./vanna_erp_db",
        })
        _vanna_entrainer_schema(vn)
        _vanna_client = vn
        print("✅ [Vanna] Initialisé et entraîné sur le schéma Sage 100.")
    except ImportError:
        print("⚠️  [Vanna] pip install vanna chromadb")
        _vanna_client = None
    except Exception as e:
        print(f"⚠️  [Vanna] {_safe_str(e)}")
        _vanna_client = None
    return _vanna_client


async def _get_vanna_async():
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
        print("   ⏳ [Vanna] Chargement ChromaDB + modèle...")
        t0 = time.perf_counter()
        result = await asyncio.to_thread(_get_vanna_sync)
        if result:
            print(f"   ✅ [Vanna] Prêt en {time.perf_counter() - t0:.1f}s")
        return result


def _vanna_entrainer_schema(vn):
    # Entraînement DDL table par table (évite duplication dans ChromaDB)
    tables_ddl = [
        """CREATE TABLE F_COMPTET (
            CT_Num       TEXT PRIMARY KEY,
            CT_Intitule  TEXT,
            CT_Type      INTEGER,   -- 0=client, 1=fournisseur, 2=interne
            CT_Validite  TEXT,      -- 'VALIDE' | 'BLOQUE' | 'SUSPECT'
            CT_EncoursMax REAL,
            CT_Encours   REAL DEFAULT 0.0
        )""",
        """CREATE TABLE F_ARTICLE (
            AR_Ref     TEXT PRIMARY KEY,
            AR_Design  TEXT,
            AR_PrixAch REAL,
            AR_PrixVen REAL,
            AR_Type    INTEGER       -- 0=produit fini, 1=composant
        )""",
        """CREATE TABLE F_ARTSTOCK (
            AR_Ref        TEXT PRIMARY KEY,
            AS_QteSto     REAL,      -- stock disponible
            AS_QteCom     REAL,      -- en commande
            AS_QteAchaCom REAL
        )""",
        """CREATE TABLE F_NOMENCLAT (
            NO_RefPF TEXT,           -- référence produit fini
            NO_RefMP TEXT,           -- référence composant
            NO_Qte   REAL,
            PRIMARY KEY (NO_RefPF, NO_RefMP)
        )""",
        """CREATE TABLE F_DOCENTETE (
            DO_Piece   TEXT PRIMARY KEY,
            DO_Domaine INTEGER,       -- 0=vente, 1=achat, 2=fabrication
            DO_Type    INTEGER,       -- 2=BL, 3=FA, 6=BC, 1=OF, 4=BF, 9=AV
            DO_Date    TEXT,
            DO_Ref     TEXT,          -- référence libre / statut règlement
            CT_Num     TEXT           -- code tiers (client/fournisseur)
        )""",
        """CREATE TABLE F_DOCLIGNE (
            DL_Ligne        INTEGER PRIMARY KEY AUTOINCREMENT,
            DO_Piece        TEXT,
            AR_Ref          TEXT,
            DL_Qte          REAL,
            DL_PrixUnitaire REAL
        )""",
        """CREATE TABLE reglements (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            DO_Piece       TEXT,      -- numéro de facture réglée
            mode_paiement  TEXT,
            montant        REAL,
            date_reglement TEXT
        )""",
        """CREATE TABLE mouvements_stock (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            AR_Ref         TEXT,
            type_mouvement TEXT,      -- 'ENTREE' | 'SORTIE'
            qte            REAL,
            motif          TEXT,
            date_mouvement TEXT
        )""",
    ]
    for ddl in tables_ddl:
        vn.train(ddl=ddl)

    # Une seule documentation consolidée (évite les 3 blocs identiques)
    vn.train(documentation="""
        RÈGLES SAGE 100 COMPLÈTES :
        - DO_Type=3 AND DO_Domaine=0 → factures de vente
        - DO_Type=2 AND DO_Domaine=0 → bons de livraison (BL)
        - DO_Type=6 AND DO_Domaine=0 → bons de commande client
        - DO_Type=1 AND DO_Domaine=2 → ordres de fabrication (OF)
        - DO_Type=4 AND DO_Domaine=2 → bons de fabrication (BF)
        - DO_Type=3 AND DO_Domaine=1 → factures fournisseur (achat)
        - DO_Type=2 AND DO_Domaine=1 → bons de réception fournisseur
        - DO_Type=6 AND DO_Domaine=1 → bons de commande fournisseur
        - CT_Type=0 → clients uniquement (pas fournisseurs)
        - CT_Type=1 → fournisseurs uniquement (PAS clients)
        - CT_Type=2 → tiers internes (PROD-INT)
        - CT_Validite='BLOQUE' → client bloqué (PAS CT_Sommeil)
        - CT_Validite='SUSPECT' → client à risque
        - CT_Validite='VALIDE' → client actif
        - Le montant d'une facture = SUM(DL_Qte * DL_PrixUnitaire) depuis F_DOCLIGNE (PAS DO_TotalHT)
        - Le tiers d'un document = CT_Num dans F_DOCENTETE (PAS DO_Tiers)
        - Une facture est réglée si son DO_Piece est dans la table reglements
        - Le stock disponible = AS_QteSto dans F_ARTSTOCK
        - Stock net = AS_QteSto - AS_QteCom
        - La nomenclature d'un produit = F_NOMENCLAT (NO_RefPF=produit fini, NO_RefMP=composant)
    """)

    exemples = [
        ("liste tous les articles du catalogue",
         "SELECT a.AR_Ref, a.AR_Design, a.AR_PrixVen, COALESCE(s.AS_QteSto,0) AS stock FROM F_ARTICLE a LEFT JOIN F_ARTSTOCK s ON a.AR_Ref=s.AR_Ref ORDER BY a.AR_Ref"),
        ("top 5 clients par chiffre d affaires",
         "SELECT e.CT_Num, c.CT_Intitule, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS ca_total FROM F_DOCENTETE e JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece LEFT JOIN F_COMPTET c ON e.CT_Num=c.CT_Num WHERE e.DO_Type=3 AND e.DO_Domaine=0 GROUP BY e.CT_Num ORDER BY ca_total DESC LIMIT 5"),
        ("factures impayees non reglees",
         "SELECT e.DO_Piece, e.CT_Num, c.CT_Intitule, e.DO_Date, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS montant_ht FROM F_DOCENTETE e LEFT JOIN F_COMPTET c ON e.CT_Num=c.CT_Num LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece WHERE e.DO_Type=3 AND e.DO_Domaine=0 AND e.DO_Piece NOT IN (SELECT DO_Piece FROM reglements) GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),
        ("articles en rupture de stock",
         "SELECT a.AR_Ref, a.AR_Design, COALESCE(s.AS_QteSto,0) AS stock FROM F_ARTICLE a LEFT JOIN F_ARTSTOCK s ON a.AR_Ref=s.AR_Ref WHERE COALESCE(s.AS_QteSto,0)<=0 ORDER BY a.AR_Ref"),
        ("chiffre d affaires global total",
         "SELECT COUNT(DISTINCT e.DO_Piece) AS nb_factures, COUNT(DISTINCT e.CT_Num) AS nb_clients, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS ca_ht FROM F_DOCENTETE e JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece WHERE e.DO_Type=3 AND e.DO_Domaine=0"),
        ("clients bloqués",
         "SELECT CT_Num, CT_Intitule, CT_Encours FROM F_COMPTET WHERE CT_Type=0 AND UPPER(CT_Validite)='BLOQUE' ORDER BY CT_Intitule"),
        ("stock de l article ECRAN4K",
         "SELECT a.AR_Ref, a.AR_Design, COALESCE(s.AS_QteSto,0) AS stock, COALESCE(s.AS_QteCom,0) AS en_commande FROM F_ARTICLE a LEFT JOIN F_ARTSTOCK s ON a.AR_Ref=s.AR_Ref WHERE UPPER(a.AR_Ref)='ECRAN4K'"),
        ("factures du client CLI001",
         "SELECT e.DO_Piece, e.DO_Date, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS montant_ht, CASE WHEN r.DO_Piece IS NOT NULL THEN 'RÉGLÉE' ELSE 'EN ATTENTE' END AS statut FROM F_DOCENTETE e LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece LEFT JOIN reglements r ON e.DO_Piece=r.DO_Piece WHERE e.DO_Type=3 AND e.CT_Num='CLI001' GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),
        ("CA mensuel par mois",
         "SELECT STRFTIME('%Y-%m',e.DO_Date) AS mois, COUNT(DISTINCT e.DO_Piece) AS nb_factures, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS ca_ht FROM F_DOCENTETE e JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece WHERE e.DO_Type=3 AND e.DO_Domaine=0 GROUP BY mois ORDER BY mois DESC LIMIT 12"),
        ("marge brute par article rentabilite",
         "SELECT l.AR_Ref, a.AR_Design, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS ca_vente, SUM(l.DL_Qte*a.AR_PrixAch) AS cout_achat, SUM(l.DL_Qte*l.DL_PrixUnitaire)-SUM(l.DL_Qte*a.AR_PrixAch) AS marge_brute FROM F_DOCLIGNE l JOIN F_DOCENTETE e ON l.DO_Piece=e.DO_Piece LEFT JOIN F_ARTICLE a ON l.AR_Ref=a.AR_Ref WHERE e.DO_Type=3 AND e.DO_Domaine=0 GROUP BY l.AR_Ref ORDER BY marge_brute DESC"),
        ("encours client CLI002",
         "SELECT c.CT_Num, c.CT_Intitule, COALESCE(c.CT_EncoursMax,0) AS encours_autorise, COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS encours_utilise FROM F_COMPTET c LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.CT_Num AND e.DO_Type=3 AND e.DO_Piece NOT IN (SELECT DO_Piece FROM reglements) LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece WHERE c.CT_Num='CLI002' GROUP BY c.CT_Num"),
        ("clients inactifs depuis 6 mois",
         "SELECT c.CT_Num, c.CT_Intitule, MAX(e.DO_Date) AS derniere_commande FROM F_COMPTET c LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.CT_Num AND e.DO_Type=3 WHERE c.CT_Type=0 GROUP BY c.CT_Num HAVING derniere_commande IS NULL OR derniere_commande < DATE('now','-180 days') ORDER BY derniere_commande ASC"),
        ("liste des bons de livraison du client CLI001",
         "SELECT e.DO_Piece, e.DO_Date, e.CT_Num, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS montant_ht "
         "FROM F_DOCENTETE e LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
         "WHERE e.DO_Type=2 AND e.DO_Domaine=0 AND e.CT_Num='CLI001' "
         "GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),
        ("clients ayant des factures superieures a 1000",
         "SELECT e.CT_Num, c.CT_Intitule, SUM(l.DL_Qte*l.DL_PrixUnitaire) AS total_ht "
         "FROM F_DOCENTETE e JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
         "LEFT JOIN F_COMPTET c ON e.CT_Num=c.CT_Num "
         "WHERE e.DO_Type=3 AND e.DO_Domaine=0 "
         "GROUP BY e.CT_Num HAVING total_ht > 1000 ORDER BY total_ht DESC"),
        ("tous les bons de livraison",
         "SELECT e.DO_Piece, e.DO_Date, e.CT_Num, c.CT_Intitule, "
         "SUM(l.DL_Qte*l.DL_PrixUnitaire) AS montant_ht "
         "FROM F_DOCENTETE e LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
         "LEFT JOIN F_COMPTET c ON e.CT_Num=c.CT_Num "
         "WHERE e.DO_Type=2 AND e.DO_Domaine=0 "
         "GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),

         ("factures fournisseur",
 "SELECT e.CT_Num, c.CT_Intitule, e.DO_Piece, e.DO_Date, "
 "SUM(l.DL_Qte*l.DL_PrixUnitaire) AS montant_ht "
 "FROM F_DOCENTETE e "
 "JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
 "JOIN F_COMPTET c ON e.CT_Num=c.CT_Num "
 "WHERE e.DO_Type=3 AND e.DO_Domaine=1 AND c.CT_Type=1 "
 "GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),

("bons de reception fournisseur",
 "SELECT e.CT_Num, c.CT_Intitule, e.DO_Piece, e.DO_Date, "
 "SUM(l.DL_Qte*l.DL_PrixUnitaire) AS montant_ht "
 "FROM F_DOCENTETE e "
 "JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
 "JOIN F_COMPTET c ON e.CT_Num=c.CT_Num "
 "WHERE e.DO_Type=2 AND e.DO_Domaine=1 AND c.CT_Type=1 "
 "GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),

        # Nouveaux exemples pour questions filtrées
        ("clients qui ont passe plus de 3 commandes",
         "SELECT c.CT_Num, c.CT_Intitule, COUNT(DISTINCT e.DO_Piece) AS nb_factures, "
         "COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS ca_total "
         "FROM F_COMPTET c "
         "JOIN F_DOCENTETE e ON c.CT_Num=e.CT_Num AND e.DO_Type=3 AND e.DO_Domaine=0 "
         "LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
         "WHERE c.CT_Type=0 "
         "GROUP BY c.CT_Num, c.CT_Intitule "
         "HAVING COUNT(DISTINCT e.DO_Piece) > 3 "
         "ORDER BY nb_factures DESC"),

        ("articles dont le prix de vente depasse 500",
         "SELECT AR_Ref, AR_Design, AR_PrixVen AS prix_vente, AR_PrixAch AS prix_achat, "
         "ROUND(AR_PrixVen - AR_PrixAch, 2) AS marge "
         "FROM F_ARTICLE WHERE AR_PrixVen > 500 ORDER BY AR_PrixVen DESC"),

        ("factures du mois de juin",
         "SELECT e.DO_Piece, e.DO_Date, e.CT_Num, c.CT_Intitule, "
         "COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS montant_ht "
         "FROM F_DOCENTETE e "
         "LEFT JOIN F_COMPTET c ON e.CT_Num=c.CT_Num "
         "LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
         "WHERE e.DO_Type=3 AND e.DO_Domaine=0 "
         "AND CAST(STRFTIME('%m', e.DO_Date) AS INTEGER) = 6 "
         "GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),

        ("factures du mois 3",
         "SELECT e.DO_Piece, e.DO_Date, e.CT_Num, c.CT_Intitule, "
         "COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS montant_ht "
         "FROM F_DOCENTETE e "
         "LEFT JOIN F_COMPTET c ON e.CT_Num=c.CT_Num "
         "LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
         "WHERE e.DO_Type=3 AND e.DO_Domaine=0 "
         "AND CAST(STRFTIME('%m', e.DO_Date) AS INTEGER) = 3 "
         "GROUP BY e.DO_Piece ORDER BY e.DO_Date DESC"),

        ("clients avec moins de 2 factures",
         "SELECT c.CT_Num, c.CT_Intitule, COUNT(DISTINCT e.DO_Piece) AS nb_factures "
         "FROM F_COMPTET c "
         "LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.CT_Num AND e.DO_Type=3 AND e.DO_Domaine=0 "
         "WHERE c.CT_Type=0 "
         "GROUP BY c.CT_Num, c.CT_Intitule "
         "HAVING COUNT(DISTINCT e.DO_Piece) < 2 "
         "ORDER BY nb_factures ASC"),
    ]
    for question, sql in exemples:
        vn.train(question=question, sql=sql)
    print(f"   📚 [Vanna] {len(exemples)} exemples + schéma entraînés.")


def _vanna_generer_sql(question: str) -> tuple[str | None, float]:
    """
    FIX B : few-shot limité à 3 exemples + timeout thread 60s.
    Évite que qwen2.5:7b se bloque sur un prompt ChromaDB trop long.
    """
    import threading

    vn = _vanna_client
    if vn is None:
        return None, 0.0

    result_container: list = [None, None]  # [sql, error]

    def _run():
        try:
            # FIX B : max 3 few-shot (was 5)
            original_get = getattr(vn, "get_similar_question_sql", None)
            if original_get:
                def _get_limited(q, **kw):
                    results = original_get(q, **kw)
                    return results[:3] if results else []  # 3 au lieu de 5
                vn.get_similar_question_sql = _get_limited

            sql = vn.generate_sql(question)

            if original_get:
                vn.get_similar_question_sql = original_get

            result_container[0] = sql
        except Exception as e:
            result_container[1] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=VANNA_GENERATE_TIMEOUT)

    if t.is_alive():
        print(f"   ⚠️  [Vanna] Timeout {VANNA_GENERATE_TIMEOUT}s → fallback patterns")
        return None, 0.0

    if result_container[1] is not None:
        print(f"   ⚠️  [Vanna] {_safe_str(result_container[1])}")
        return None, 0.0

    sql = result_container[0]
    if not sql or not sql.strip().upper().startswith("SELECT"):
        return None, 0.0

    try:
        import sqlglot
        sqlglot.parse_one(sql, dialect="sqlite")
        score = 0.85
    except Exception:
        score = 0.55
    return sql.strip(), score


def _vanna_entrainer(question: str, sql: str):
    vn = _vanna_client
    if vn is None:
        return
    try:
        vn.train(question=question, sql=sql)
    except Exception:
        pass


def _valider_sql(sql: str) -> tuple[bool, str]:
    try:
        import sqlglot
        sqlglot.parse_one(sql, dialect="sqlite")
        return True, ""
    except ImportError:
        return True, ""
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────
# LANGSMITH
# ─────────────────────────────────────────────────────────────────────
def _init_langsmith():
    api_key = (os.getenv("LANGCHAIN_API_KEY") or "").strip()
    tracing = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    if api_key and tracing:
        os.environ.setdefault("LANGCHAIN_PROJECT", "copilot-erp-sage100")
        print("✅ [LangSmith] Tracing activé.")
    else:
        print("ℹ️  [LangSmith] Non configuré.")


_init_langsmith()

# ─────────────────────────────────────────────────────────────────────
# TABLES DE DISPATCH
# ─────────────────────────────────────────────────────────────────────
ACTIONS_LECTURE = {
    "TOP_CLIENTS", "LISTE_CLIENTS", "LISTE_ARTICLES", "PALMARES_ARTICLES",
    "CA_GLOBAL", "CLIENTS_BAISSE", "FACTURES_NON_REGLEES", "FACTURES_NON_REGLEES_FOURN",
    "TOUTES_FACTURES_CLIENT", "VERIFIER_STOCK", "FICHE_CLIENT",
    "DOCS_PERIODE", "RENTABILITE", "SAISONNALITE", "DSO", "RFM", "STATUT_CLIENT",
    "LISTE_FOURNISSEURS", "FICHE_FOURNISSEUR", "TOP_FOURNISSEURS",
}
ACTIONS_NL2SQL   = {"NL2SQL_LIBRE"}
ACTIONS_EXPORT   = {
    "OFFRE_PRIX_EXCEL", "DECLARATION_EXCEL",
    "BALANCE_AGEE_EXCEL", "DASHBOARD_EXCEL",
}
ACTIONS_ECRITURE = {
    "CREER_CLIENT", "CREER_FOURNISSEUR", "MODIFIER_STATUT", "GENERER_DOC",
    "TRANSFORMER_DOC", "CREER_AVOIR", "REGLEMENT",
    "MOUVEMENT_STOCK", "PROPOSITION_ACHAT",
}
ACTIONS_WORKFLOW     = {"WORKFLOW_COMMANDE"}
ACTIONS_KB           = {
    "RECHERCHE_PROCEDURE", "RECOMMANDATION",
    "SEUIL_STOCK", "LISTE_PROCEDURES",
}
ACTIONS_HUB = {
    "VALIDER_DEMANDE", "RESOUDRE_TYPE_DOC",
    "GET_SCHEMA", "CONTEXTE_CLIENT",
}
ACTIONS_ENRICHIR_RAG  = {"CLIENTS_BAISSE", "DSO", "FACTURES_NON_REGLEES", "RFM"}
ACTIONS_SYNTHESE_LITE = {
    "GENERER_DOC", "TRANSFORMER_DOC", "CREER_AVOIR", "REGLEMENT",
    "MODIFIER_STATUT", "CREER_CLIENT", "CREER_FOURNISSEUR", "MOUVEMENT_STOCK", "FACTURES_NON_REGLEES_FOURN",
    "VERIFIER_STOCK", "FICHE_CLIENT", "STATUT_CLIENT",
}

_STATUTS_ACTIONS_V3_OK = {
    "GENERE", "TRANSFORME", "CREE", "MODIFIE",
    "REGLE", "MOUVEMENT_ENREGISTRE", "INCHANGE",
}
_STATUTS_ERREUR_MCP = {
    "CLIENT_NON_TROUVE", "ARTICLE_NON_TROUVE", "STOCK_INSUFFISANT",
    "CLIENT_BLOQUE", "COMPOSANTS_INSUFFISANTS", "NON_TROUVE",
    "EXISTE_DEJA", "ERREUR",
}

MOTS_REFERENCE_DOCUMENT = (
    "précédent", "précédente", "dernier", "dernière",
    "celui-ci", "celle-ci", "ce document", "ce bl", "cette facture",
)
_TYPES_DOC_INVALIDES_COMME_ARTICLE = {
    "OF", "BF", "BL", "BL_ACHAT", "FA_ACHAT", "FA", "FC", "BC", "FACTURE", "AVOIR", "AV",
}
TYPES_DOC_FABRICATION = {"OF", "BF"}

_EXCL_ARTICLE = {
    "CLI", "BL", "FA", "FC", "BC", "OF", "BF", "BA", "AV",
    "ERP", "NL2SQL", "SQL", "PDF", "KPI", "DSO", "RFM", "CA",
    "CREE", "CREER", "POUR", "AVEC", "PIECES", "PIECE", "PCS",
    "UNITE", "UNITES", "LE", "LA", "LES", "UN", "UNE", "DES",
    "OK", "OUI", "NON", "LANCE", "GENERE", "FAIRE", "NOUVEAU",
    "PROD", "INT", "PRODINT", "SAGE", "LISTE", "DONNE", "AFFICHE",
    "TOUS", "TOUTES", "MONTRE", "CLIENTS", "ARTICLES", "CLIENT",
    "ARTICLE", "ENCOURS", "STATUT", "FICHE", "INFO", "FOURNISSEUR", "FOURNISSEURS", "FOUR", "GROSSISTE", "FOURN",
    "ACHAT", "ACHATS", "COMMANDE", "COMMANDES",
    # FIX BUGD : mots génériques qui ne sont pas des articles
    "FACTURES", "FACTURE", "FOURNISSEUR", "FOURNISSEURS",
    "LISTE", "DETAIL", "DETAILS", "RAPPORT",
    # FIX BUG-ARTICLE : mots capturés à tort comme référence article
    "STOCK", "STOCKS", "DISPONIBLE", "DISPONIBLES", "RESTANT",
    "RUPTURE", "RUPTURES", "FAIBLE", "FAIBLES",
    "ONT", "PASSE", "COMMANDE", "DEPUIS", "MOIS",
    "EST", "SONT", "AVEZ", "AVONS", "AVAIT",
    "NON", "PAS", "SANS", "AUCUN", "AUCUNE",
    "FACTURE", "FACTURES", "LISTE", "DONNE", "MONTRE",
    "QUEL", "QUELS", "QUELLE", "QUELLES", "QUI", "QUOI",
    "COMMENT", "COMBIEN", "POURQUOI",
    "INACTIFS", "BLOQUES", "BLOQUE", "ACTIFS", "VALIDE", "SUSPECT",
    "VENDUS", "ACHETÉS", "COMMANDÉS",
    "GLOBAL", "TOTAL", "MENSUEL", "ANNUEL",
    "CLIENTS", "ARTICLES", "FOURNISSEURS",
}

_MOTS_GENERIQUES_NER = {
    "client", "tiers", "le", "la", "les", "un", "une", "des",
    "pour", "avec", "article", "produit", "référence", "ref",
    "piece", "pièce", "unité", "unite", "quantite", "quantité",
    "société", "societe", "entreprise",
}
_MARQUEURS_NL2SQL_FORCE = {
    "mois par mois", "par mois", "évolution", "tendance",
    "uniquement", "seulement", "n'ont pas", "aucune commande",
    "depuis plus de", "inactifs", "croisement", "en commun",
    "meilleurs clients", "top.*client.*fourni", "vendus à un seul",
    "having", "ratio", "panier moyen", "taux de",
    "par nombre de commandes", "nombre de commandes",
    "seuil", "commandés ce mois", "commandé ce mois",
    "inférieur au seuil", "stock insuffisant", "trier par commandes",
    "classement", "classé", "classe",
}

CAPACITES_SYSTEME = """Ce que je sais faire sur votre ERP Sage 100 :
  📊 Lecture & Analyse  : liste clients/articles, top clients CA, palmarès, CA global, saisonnalité, rentabilité, DSO, RFM
  🧾 Factures           : toutes les factures d'un client, factures non réglées/impayées
  🔍 Recherche          : fiche client, statut client, stock article, documents par période
  📁 Export Excel       : offre de prix, déclaration fiscale, balance âgée, dashboard KPI
  ✍️  Écriture          : créer client, modifier statut, générer BL/Facture/BC/OF/BF, transformer document, créer avoir, régler facture, mouvement stock
  🔄 Workflow           : flux commande complet (vérification → production → livraison → facturation)
  📚 Base de connaissances : procédures internes, recommandations, seuils de stock"""

_LLM_PLACEHOLDERS = {
    "INCONNU", "AUCUN", "N/A", "0", "-", "",
    "VALEUR_NON_REPRESENTE", "VALEUR_NON_REPRÉSENTE",
    "NON_REPRESENTE", "NON_REPRÉSENTE",
    "NULL", "NONE", "VIDE", "ABSENT", "NA",
    "NON_RENSEIGNE", "NON_RENSEIGNÉ",
    "INDEFINI", "INDÉFINI", "UNDEFINED", "UNKNOWN",
    "VALEUR", "VALEUR_MANQUANTE", "MANQUANT",
}


def _clean(v: str) -> str:
    v = v.replace('"', "").replace("'", "").strip()
    return "" if v.upper() in _LLM_PLACEHOLDERS else v


# ═════════════════════════════════════════════════════════════════════
# PRÉ-CLASSIFICATION REGEX — v9.3
# ═════════════════════════════════════════════════════════════════════
_PATTERNS_PRECLASS = [
    # ══════════════════════════════════════════════════════════════
    # FIX 2 : TRANSFORMER_DOC — PRIORITÉ ABSOLUE (avant GENERER_DOC)
    # ══════════════════════════════════════════════════════════════
    (r"transform[e\s]+.{0,30}\bof\b.{0,20}\bbf\b",            "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,30}\bbl\b.{0,20}facture",           "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,30}\bbc\b.{0,20}\bbl\b",            "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,15}(?:fa|bl|bc|of|bf)\d+",          "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,15}[a-z]{2}\d{6,}",                 "TRANSFORMER_DOC"),
    (r"convert[i\s]+.{0,30}(?:bl|of|bc).{0,20}(?:facture|bf|bl)", "TRANSFORMER_DOC"),
    (r"facturer\s+(?:le\s+)?bl\b",                             "TRANSFORMER_DOC"),
    (r"passer\s+(?:le\s+)?(?:bl|of)\b.{0,20}en\b",            "TRANSFORMER_DOC"),

    # ── GÉNÉRATION DOCUMENTS ──────────────────────────────────────
    # Priorité absolue : BL ACHAT (avant tout autre pattern 'facture')
    (r"bl\s+achat|bon\s+de\s+r[eé]ception|r[eé]ception\s+fournisseur|livraison\s+fournisseur", "GENERER_DOC"),
    (r"cr[eé][eé]?\s+.{0,20}bl\s+achat",                "GENERER_DOC"),
    (r"cr[eé][eé]?\s+.{0,20}r[eé]ception\s+fournisseur","GENERER_DOC"),
    # Priorité absolue : REGLEMENT (avant TOUTES_FACTURES_CLIENT)
    (r"r[eé]gler?\s+(la\s+|une\s+|les\s+)?(?:facture|fa)\s+[A-Z0-9]+",   "REGLEMENT"),
    (r"r[eé]glement\s+(?:de\s+la\s+)?(?:facture|fa)\s+[A-Z0-9]+",        "REGLEMENT"),
    (r"change.{0,30}(?:statut|status).{0,30}(?:facture|fa)\s+[A-Z0-9]+",  "REGLEMENT"),
    (r"marquer?\s+(?:la\s+)?(?:facture|fa)\s+[A-Z0-9]+.{0,30}r[eé]gl[eé]","REGLEMENT"),
    (r"(?:facture|fa)\s+([A-Z0-9]{3,})\s+.{0,20}r[eé]gl[eé]e?",          "REGLEMENT"),
    (r"cr[eé][eé]?\s+(un\s+)?bl\b",                        "GENERER_DOC"),
    (r"g[eé]n[eè]re?\s+(un\s+)?bl\b",                      "GENERER_DOC"),
    (r"\bbl\s+(pour|client|cli)",                           "GENERER_DOC"),
    (r"cr[eé][eé]?\s+(un\s+)?of\b",                        "GENERER_DOC"),
    (r"ordre\s+de\s+fabrication",                           "GENERER_DOC"),
    (r"cr[eé][eé]?\s+(un\s+)?bf\b",                        "GENERER_DOC"),
    (r"cr[eé][eé]?\s+(une?\s+)?facture",                    "GENERER_DOC"),
    (r"g[eé]n[eè]re?\s+(une?\s+)?facture",                  "GENERER_DOC"),
    (r"[eé]tabli[rs]\s+(une?\s+)?facture",                  "GENERER_DOC"),
    (r"cr[eé][eé]?\s+(un\s+)?bc\b",                        "GENERER_DOC"),
    (r"bon\s+de\s+commande",                                "GENERER_DOC"),
    (r"g[eé]n[eè]re?\s+(un\s+)?bon",                       "GENERER_DOC"),

    # ── ÉCRITURE CLIENTS ──────────────────────────────────────────
    (r"cr[eé][eé]?(?:r|er)?\s+(?:un\s+|le\s+|la\s+|un\s+nouveau\s+|nouveau\s+)?client", "CREER_CLIENT"),
    (r"cr[eé][eé]?z?\s+(?:un\s+|le\s+|la\s+)?(?:nouveau\s+)?client",    "CREER_CLIENT"),
    (r"enregistr(?:er?|ez?)\s+(?:un\s+|le\s+)?(?:nouveau\s+)?client",   "CREER_CLIENT"),
    (r"saisi[rs]?\s+(?:un\s+|le\s+)?(?:nouveau\s+)?client",             "CREER_CLIENT"),
    (r"nouveau\s+client",                                               "CREER_CLIENT"),
    (r"ajouter?\s+(un\s+)?client",                                      "CREER_CLIENT"),
    # ── CREER_FOURNISSEUR ──
    (r"cr[eé][eé]?(?:r|er)?\s+(?:un\s+|le\s+|un\s+nouveau\s+|nouveau\s+)?fournisseur", "CREER_FOURNISSEUR"),
    (r"cr[eé][eé]?z?\s+(?:un\s+|le\s+)?(?:nouveau\s+)?fournisseur",    "CREER_FOURNISSEUR"),
    (r"enregistr(?:er?|ez?)\s+(?:un\s+|le\s+)?(?:nouveau\s+)?fournisseur", "CREER_FOURNISSEUR"),
    (r"saisi[rs]?\s+(?:un\s+|le\s+)?(?:nouveau\s+)?fournisseur",       "CREER_FOURNISSEUR"),
    (r"nouveau\s+fournisseur",                                             "CREER_FOURNISSEUR"),
    (r"ajouter?\s+(un\s+)?fournisseur",                                   "CREER_FOURNISSEUR"),
    (r"bloquer?\s+(le\s+)?client",                          "MODIFIER_STATUT"),
    (r"d[eé]bloquer?\s+(le\s+)?client",                     "MODIFIER_STATUT"),
    (r"r[eé]activer?\s+(le\s+)?client",                     "MODIFIER_STATUT"),
    (r"modifier?\s+(le\s+)?statut",                         "MODIFIER_STATUT"),

    # ── AVOIR / RÈGLEMENT ─────────────────────────────────────────
    (r"cr[eé][eé]?\s+(un\s+)?avoir",                        "CREER_AVOIR"),
    (r"r[eé]gler?\s+(la\s+|une\s+|les\s+)?factures?",       "REGLEMENT"),
    (r"r[eé]glement\s+(d.une\s+|de\s+la\s+)?facture",       "REGLEMENT"),
    (r"payer?\s+(la\s+|une\s+|les\s+)?factures?",           "REGLEMENT"),
    (r"payer?\s+(?:la\s+)?(?:facture\s+)?(?:FA|BL|BC|BF)\d+","REGLEMENT"),
    (r"paiement\s+(d.une\s+|de\s+la\s+)?facture",           "REGLEMENT"),
    (r"change.{0,20}statut.{0,20}facture.{0,20}r[eé]gl[eé]","REGLEMENT"),

    # ══════════════════════════════════════════════════════════════
    # FIX 4 : DOCUMENTS PAR TYPE → NL2SQL_LIBRE (avant LISTE_CLIENTS)
    # ══════════════════════════════════════════════════════════════
    (r"(?:liste|donne|affiche|montre).{0,30}bons?\s+de\s+livraison",   "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,20}\bbl\b.{0,20}client",      "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,30}bons?\s+de\s+commande",    "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,30}bons?\s+de\s+fabrication", "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,30}ordres?\s+de\s+fabrication","NL2SQL_LIBRE"),
    (r"bons?\s+de\s+livraison\s+(?:du\s+|de\s+)?client",               "NL2SQL_LIBRE"),
    (r"\bbl\b.{0,30}(?:du\s+|de\s+)?client",                           "NL2SQL_LIBRE"),
    # ── REQUÊTES ANALYTIQUES AVEC FILTRE ─────────────────────────
    (r"factures?\s+(?:sup[eé]rieure?s?\s+[àa]|plus\s+(?:de|que)|>\s*)\s*\d+",  "NL2SQL_LIBRE"),
    (r"factures?\s+(?:inf[eé]rieure?s?\s+[àa]|moins\s+(?:de|que)|<\s*)\s*\d+", "NL2SQL_LIBRE"),
    (r"factures?\s+entre\s+\d+\s+et\s+\d+",                                     "NL2SQL_LIBRE"),
    (r"clients?\s+(?:ayant|avec|qui\s+ont)\s+(?:des?\s+)?factures?",             "NL2SQL_LIBRE"),
    (r"clients?\s+(?:dont|avec)\s+(?:un\s+)?(?:ca|chiffre).{0,30}\d+",          "NL2SQL_LIBRE"),
    (r"clients?\s+(?:dont|avec)\s+(?:un\s+)?encours.{0,30}\d+",                 "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec)\s+(?:un\s+)?stock.{0,20}\d+",                  "NL2SQL_LIBRE"),
    (r"articles?\s+(?:vendus?|achet[eé]s?)\s+(?:plus|moins)\s+(?:de|que)\s+\d+","NL2SQL_LIBRE"),
    (r"top\s+\d+\s+(?!clients?)(?:articles?|produits?|références?)",              "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre)\s+.{0,40}(?:o[ùu]|mais|dont|sauf|seulement|uniquement|filtre)", "NL2SQL_LIBRE"),

    # ── FACTURES PAR PÉRIODE (avant tout) ────────────────────────
    (r"factures?\s+(?:du\s+|de\s+|d['\u2019]?\s*)?(?:mois\s+(?:de\s+)?)?(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|jan|fév|mar|avr|jun|jul|aoû|sep|oct|nov|déc)", "NL2SQL_LIBRE"),
    (r"factures?\s+(?:du\s+)?mois\s+\d{1,2}",              "NL2SQL_LIBRE"),
    (r"factures?\s+(?:de\s+)?(?:l['\u2019]ann[eé]e|\d{4})", "NL2SQL_LIBRE"),
    (r"factures?\s+(?:du\s+|de\s+)?(?:trimestre|semestre)", "NL2SQL_LIBRE"),
    (r"(?:liste|affiche|montre|donne).{0,30}factures?.{0,30}(?:mois|ann[eé]e|p[eé]riode|semaine)", "NL2SQL_LIBRE"),
    (r"(?:liste|affiche|montre|donne).{0,30}factures?.{0,30}(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)", "NL2SQL_LIBRE"),
    # ── ARTICLES AVEC FILTRE PRIX / QUANTITÉ ─────────────────────
    (r"articles?\s+(?:dont|avec|au|ayant).{0,30}(?:prix|tarif|co[uû]t).{0,30}(?:sup[eé]r|inf[eé]r|d[eé]passe|plus|moins|\>|\<)", "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec).{0,30}prix.{0,30}\d+",    "NL2SQL_LIBRE"),
    (r"(?:prix|tarif)\s+(?:de\s+vente|d['\u2019]achat).{0,30}(?:sup[eé]r|inf[eé]r|d[eé]passe|plus|moins|\>|\<)", "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec).{0,30}(?:marge|rentabilit)", "NL2SQL_LIBRE"),
    # ── CLIENTS AVEC FILTRE QUANTITATIF ──────────────────────────
    (r"clients?\s+(?:qui\s+ont|ayant|avec).{0,30}(?:plus\s+de|plus\s+qu[e'\u2019]|au\s+moins)\s+\d+\s+(?:commandes?|factures?|achats?)", "NL2SQL_LIBRE"),
    (r"clients?\s+(?:qui\s+ont|ayant|avec).{0,30}(?:moins\s+de|moins\s+qu[e'\u2019])\s+\d+\s+(?:commandes?|factures?|achats?)", "NL2SQL_LIBRE"),
    (r"clients?\s+(?:qui\s+n['\u2019]ont\s+pas|sans|aucune?)\s+(?:commandes?|factures?)", "NL2SQL_LIBRE"),
    (r"clients?\s+(?:pass[eé]|effectu[eé]).{0,20}(?:plus\s+de|au\s+moins)\s+\d+\s+(?:commandes?|achats?)", "NL2SQL_LIBRE"),
    # ── CLASSEMENT PAR NOMBRE DE COMMANDES ───────────────────────
    (r"clas(?:se|sement|s[eé])\s+.{0,30}clients?.{0,30}(?:nombre|nb)\s+(?:de\s+)?commandes?", "NL2SQL_LIBRE"),
    (r"clients?.{0,30}(?:tri[eé]s?|class[eé]s?|ordonn[eé]s?|rang[eé]s?).{0,30}(?:nombre|nb).{0,20}commandes?", "NL2SQL_LIBRE"),
    (r"clients?.{0,30}par\s+(?:nombre|nb)\s+(?:de\s+)?commandes?",    "NL2SQL_LIBRE"),
    (r"(?:nombre|nb)\s+(?:de\s+)?commandes?\s+(?:par\s+)?client",     "NL2SQL_LIBRE"),
    (r"qui\s+(?:commande|achète|a\s+achet[eé])\s+le\s+plus",           "NL2SQL_LIBRE"),
    # ── ARTICLES STOCK SEUIL + COMMANDÉS ─────────────────────────
    (r"articles?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique).{0,40}command[eé]s?", "NL2SQL_LIBRE"),
    (r"articles?.{0,40}command[eé]s?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique)", "NL2SQL_LIBRE"),
    (r"articles?.{0,30}(?:stock\s+(?:faible|bas|insuffisant|inf[eé]r|critique)|sous.{0,10}seuil).{0,40}(?:command[eé]|achet[eé])", "NL2SQL_LIBRE"),
    (r"rupture.{0,20}command[eé]|command[eé].{0,20}rupture",           "NL2SQL_LIBRE"),
    # ── CLIENTS AVEC FILTRE QUANTITATIF SUR FACTURES + ENCOURS (NL2SQL_LIBRE) ─
    (
        r"clients?\s+(?:actifs?|avec|ayant|dont).{0,80}(?:factures?\s+impay[eé]es?|encours|ca\b)",
        "NL2SQL_LIBRE"
    ),
    (
        r"clients?.{0,50}(?:encours\s+sup[eé]r|encours\s+>\s*\d+|encours\s+plus)",
        "NL2SQL_LIBRE"
    ),
    # ── CLIENTS BLOQUÉS / INACTIFS (avant LISTE_CLIENTS) ─────────
    (r"clients?\s+bloqu[eé]s?",                             "NL2SQL_LIBRE"),
    (r"bloqu[eé]s?\s+clients?",                             "NL2SQL_LIBRE"),
    (r"quels?\s+clients?.{0,30}bloqu[eé]",                  "NL2SQL_LIBRE"),
    (r"clients?\s+inactifs?",                               "NL2SQL_LIBRE"),
    (r"clients?\s+sans\s+commande",                         "NL2SQL_LIBRE"),
    # ── ENCOURS CLIENT ────────────────────────────────────────────
    (r"encours\s+(du\s+|de\s+|d['\u2019]?\s*)?client",     "NL2SQL_LIBRE"),
    (r"cr[eé]dit\s+(du\s+)?client",                        "NL2SQL_LIBRE"),
    (r"solde\s+(du\s+)?client",                             "NL2SQL_LIBRE"),
    (r"limite\s+(du\s+)?client",                            "NL2SQL_LIBRE"),
    # Ajouter après les patterns LISTE_CLIENTS
(r"liste\s+(tous\s+)?(les\s+)?fournisseurs?",   "LISTE_FOURNISSEURS"),
(r"(tous|toutes)\s+(les\s+)?fournisseurs?",      "LISTE_FOURNISSEURS"),
(r"affiche\s+(les\s+)?fournisseurs?",            "LISTE_FOURNISSEURS"),
(r"montre\s+(moi\s+)?(les\s+)?fournisseurs?",    "LISTE_FOURNISSEURS"),
(r"donne\s+(moi\s+)?(les\s+)?fournisseurs?",     "LISTE_FOURNISSEURS"),
(r"fiche\s+(du\s+|de\s+)?fournisseur",           "FICHE_FOURNISSEUR"),
(r"info\w*\s+(sur\s+)?(le\s+)?fournisseur",      "FICHE_FOURNISSEUR"),
(r"fournisseurs?\s+actifs?",                     "LISTE_FOURNISSEURS"),
(r"quels?\s+fournisseurs?",                      "LISTE_FOURNISSEURS"),
(r"top\s*\d*\s*fournisseurs?",                   "TOP_FOURNISSEURS"),
(r"meilleurs?\s+fournisseurs?",                  "TOP_FOURNISSEURS"),
(r"achats?\s+(par\s+)?fournisseur",              "TOP_FOURNISSEURS"),
(r"commandes?\s+(chez|aupres|auprès)\s+",        "NL2SQL_LIBRE"),
(r"bons?\s+de\s+commande\s+(du\s+|de\s+)?fournisseur", "NL2SQL_LIBRE"),
    # ── LISTE_CLIENTS (générique) ─────────────────────────────────
    (r"liste\s+(tous\s+)?(les\s+|des\s+)?clients?",         "LISTE_CLIENTS"),
    (r"(tous|toutes)\s+(les\s+)?clients?",                  "LISTE_CLIENTS"),
    (r"affiche\s+(les\s+)?clients?",                        "LISTE_CLIENTS"),
    (r"montre\s+(moi\s+)?(les\s+)?clients?",                "LISTE_CLIENTS"),
    (r"donne\s+(moi\s+)?(les\s+)?clients?",                 "LISTE_CLIENTS"),
    (r"clients?\s+actifs?",                                 "LISTE_CLIENTS"),
    (r"quels?\s+clients?",                                  "LISTE_CLIENTS"),
    # ── TOP_CLIENTS ───────────────────────────────────────────────
    (r"top\s*\d*\s*clients?",                               "TOP_CLIENTS"),
    (r"meilleurs?\s+clients?",                              "TOP_CLIENTS"),
    (r"clients?\s+(par\s+)?ca\b",                           "TOP_CLIENTS"),
    # ── FICHE_CLIENT ──────────────────────────────────────────────
    (r"fiche\s+(du\s+|de\s+|d['\u2019]?\s*)?client",       "FICHE_CLIENT"),
    (r"info\w*\s+(sur\s+)?(le\s+)?client",                 "FICHE_CLIENT"),
    (r"d[eé]tail\s+(du\s+)?client",                        "FICHE_CLIENT"),
    (r"profil\s+(du\s+)?client",                           "FICHE_CLIENT"),
    # ── STATUT_CLIENT ─────────────────────────────────────────────
    (r"statut\s+(du\s+|de\s+)?client",                     "STATUT_CLIENT"),
    (r"client\s+est.il\s+bloqu[eé]",                       "STATUT_CLIENT"),
    # ── LISTE_ARTICLES ────────────────────────────────────────────
    (r"liste\s+(tous\s+)?(les\s+)?articles?",              "LISTE_ARTICLES"),
    (r"(tous|toutes)\s+(les\s+)?articles?",                "LISTE_ARTICLES"),
    (r"catalogue\s*(articles?|produits?)?",                "LISTE_ARTICLES"),
    (r"tous\s+(les\s+)?produits?",                         "LISTE_ARTICLES"),
    (r"affiche\s+(les\s+)?articles?",                      "LISTE_ARTICLES"),
    (r"liste\s+(les\s+)?produits?",                        "LISTE_ARTICLES"),
    # ── VERIFIER_STOCK ────────────────────────────────────────────
    (r"articles?\s+en\s+rupture",                              "VERIFIER_STOCK"),
    (r"rupture\s+de\s+stock",                                  "VERIFIER_STOCK"),
    # Stock avec référence article explicite (ne pas capturer "STOCK" comme ref)
    (r"stock\s+(?:disponible|actuel|restant)\s+de\s+l['\u2019]article", "VERIFIER_STOCK"),
    (r"stock\s+de\s+l['\u2019]article",                        "VERIFIER_STOCK"),
    (r"quel\s+est\s+le\s+stock",                               "VERIFIER_STOCK"),
    (r"stock\s+(?:disponible|actuel|restant)",                  "VERIFIER_STOCK"),
    (r"combien\s+(?:de\s+)?stock",                             "VERIFIER_STOCK"),
    (r"anomalies?\s+.{0,20}stocks?",                           "NL2SQL_LIBRE"),
    (r"stock\s+n[eé]gatif",                                    "NL2SQL_LIBRE"),
    # ── CLIENTS AVEC FILTRE TEMPOREL (avant LISTE_CLIENTS) ───────
    (r"clients?.{0,50}n['\u2019]ont\s+pas\s+command[eé]",     "NL2SQL_LIBRE"),
    (r"clients?.{0,30}(?:pas\s+command[eé]|pas\s+achet[eé]).{0,30}(?:depuis|\d+\s+mois)", "NL2SQL_LIBRE"),
    (r"quels?\s+clients?.{0,50}(?:depuis\s+\d+|depuis\s+(?:un|une|deux|trois|\d+)\s+mois)", "NL2SQL_LIBRE"),
    (r"clients?.{0,20}inactifs?.{0,20}(?:depuis|mois|\d+)",   "NL2SQL_LIBRE"),
    (r"ca\s+(global|total)",                               "CA_GLOBAL"),
    (r"chiffre\s+d.affaires?\s+(global|total)",            "CA_GLOBAL"),
    (r"chiffre\s+d.affaires?\s+global",                    "CA_GLOBAL"),
    # ── SAISONNALITE ──────────────────────────────────────────────
    (r"ca\s+(par\s+)?mois",                                "SAISONNALITE"),
    (r"ca\s+mensuel",                                      "SAISONNALITE"),
    (r"chiffre\s+d.affaires?\s+(par\s+)?mois",             "SAISONNALITE"),
    # ── FACTURES_NON_REGLEES_FOURN ─────────────────────────────────
    (r"factures?\s+(non\s+r[eé]gl[eé]es?|impay[eé]es?|en\s+attente).{0,30}fournisseur", "FACTURES_NON_REGLEES_FOURN"),
    (r"fournisseur.{0,30}factures?\s+(non\s+r[eé]gl[eé]es?|impay[eé]es?|en\s+attente)", "FACTURES_NON_REGLEES_FOURN"),
    (r"impay[eé]es?.{0,20}fournisseur",  "FACTURES_NON_REGLEES_FOURN"),
    (r"fournisseur.{0,20}impay[eé]es?",  "FACTURES_NON_REGLEES_FOURN"),
    (r"achats?\s+(non\s+r[eé]gl[eé]s?|impay[eé]s?)", "FACTURES_NON_REGLEES_FOURN"),
    # ── FACTURES_NON_REGLEES ──────────────────────────────────────
    (r"factures?\s+(non\s+r[eé]gl|impay|en\s+attente)",    "FACTURES_NON_REGLEES"),
    (r"(impay[eé]es?|non\s+r[eé]gl[eé]es?)",               "FACTURES_NON_REGLEES"),
    # ── LISTE GLOBALE FACTURES (sans mention de client spécifique) ───
    (r"listes?\s+(toutes?\s+)?(des\s+|les\s+)?factures?(?:\s+compl[eè]tes?)?\s*$", "NL2SQL_LIBRE"),
    (r"(?:affiche|montre|donne)\s+(toutes?\s+)?(des\s+|les\s+)?factures?(?:\s+compl[eè]tes?)?$", "NL2SQL_LIBRE"),
    (r"toutes?\s+(des\s+|les\s+)?factures?(?:\s+compl[eè]tes?)?$", "NL2SQL_LIBRE"),
    (r"listes?\s+(des\s+|les\s+)?factures?\s+d[\s']un\s+fournisseur\s+pr[eé]cis", "NL2SQL_LIBRE"),
    (r"toutes?\s+les?\s+factures?\s+(du\s+|de\s+)?fournisseur", "NL2SQL_LIBRE"),
    (r"factures?\s+(du\s+|de\s+)?fournisseur",                  "NL2SQL_LIBRE"),
    # ── TOUTES_FACTURES_CLIENT ────────────────────────────────────
    (r"toutes?\s+les?\s+factures?\s+(du\s+|de\s+)?client", "TOUTES_FACTURES_CLIENT"),
    (r"factures?\s+du\s+client",                           "TOUTES_FACTURES_CLIENT"),
    # ── DSO ───────────────────────────────────────────────────────
    (r"(d[eé]lai|dso|retard)\s+(de\s+)?paiement",         "DSO"),
    (r"\bdso\b",                                           "DSO"),
    # ── RFM ───────────────────────────────────────────────────────
    (r"\brfm\b",                                           "RFM"),
    (r"analyse\s+rfm",                                     "RFM"),
    (r"segmentation\s+clients?",                           "RFM"),
    # ── DASHBOARD_EXCEL ───────────────────────────────────────────
    (r"tableau\s+de\s+bord",                               "DASHBOARD_EXCEL"),
    (r"\bdashboard\b",                                     "DASHBOARD_EXCEL"),
    (r"\bkpi\b",                                           "DASHBOARD_EXCEL"),
    (r"r[eé]sum[eé]\s+(g[eé]n[eé]ral|global)?",            "DASHBOARD_EXCEL"),
    # ── PALMARES_ARTICLES ─────────────────────────────────────────
    (r"palm[aà]r[eè]s",                                    "PALMARES_ARTICLES"),
    (r"articles?\s+les?\s+plus?\s+vendus?",                "PALMARES_ARTICLES"),
    (r"meilleurs?\s+articles?",                            "PALMARES_ARTICLES"),
    # ── RENTABILITE ───────────────────────────────────────────────
    (r"marge\s+(brute\s+)?par\s+article",                  "RENTABILITE"),
    (r"rentabilit[eé]\s+(des?\s+)?articles?",              "RENTABILITE"),
    (r"taux\s+de\s+marge",                                 "RENTABILITE"),
    # ── CLIENTS_BAISSE ────────────────────────────────────────────
    (r"clients?\s+en\s+baisse",                            "CLIENTS_BAISSE"),
    (r"clients?\s+baisse\s+ca",                            "CLIENTS_BAISSE"),
    # ── DOCS_PERIODE ──────────────────────────────────────────────
    (r"documents?\s+entre\s+\d{4}",                       "DOCS_PERIODE"),
    (r"documents?\s+du\s+\d{4}",                          "DOCS_PERIODE"),
    # ── BON DE LIVRAISON / FABRICATION générique (après NL2SQL) ──
    (r"bon\s+de\s+livraison",                               "GENERER_DOC"),
    (r"bon\s+de\s+fabrication",                             "GENERER_DOC"),
]


def _pre_classifier(question: str) -> str | None:
    """
    Classification ultra-rapide par regex (0ms).
    Prioritaire sur le LLM et les overrides sémantiques.
    Retourne l'action ou None si aucun pattern ne matche.
    """
    q = question.lower().strip()
    # Si marqueur NL2SQL détecté → on bypasse les regex simples
    if any(m in q for m in _MARQUEURS_NL2SQL_FORCE):
        return "NL2SQL_LIBRE"
    for pattern, action in _PATTERNS_PRECLASS:
        if re.search(pattern, q, re.IGNORECASE):
            print(f"   ⚡ [PreClass] {action} (regex, 0ms)")
            return action
    return None


# ─────────────────────────────────────────────────────────────────────
# ÉTAT
# ─────────────────────────────────────────────────────────────────────
class CopilotState(TypedDict):
    demande_brute:         str
    intention:             str
    action:                str
    ambigue:               bool
    score_confiance:       float
    code_client:           str
    ref_article:           str
    quantite:              float
    num_piece:             str
    type_doc:              str
    type_doc_code:         int
    date_debut:            str
    date_fin:              str
    mode_paiement:         str
    validation_ok:         bool
    hub_validation:        str
    reponse_brute:         str
    rag_complement:        str
    reponse_finale:        str
    hallucination_flag:    bool
    mem0_contexte:         str
    dernier_type_doc:      str
    dernier_num_piece:     str
    dernier_code_client:   str
    dernier_ref_article:   str
    dernier_quantite:      float
    plan_execution:        list
    etape_courante:        int
    nom_client_brut:       str
    suggestion_en_attente: dict
    pending_action: dict
    pending_document: dict
    attente_complements: bool


def _etat_initial(demande: str, contexte_session: dict | None = None) -> CopilotState:
    ctx = contexte_session or {}
    dd  = ctx.get("dernier_document", {})
    _dernier_num  = ctx.get("dernier_num_piece", "") or dd.get("num_piece", "")
    _dernier_type = ctx.get("dernier_type_doc",  "") or dd.get("type_doc",  "")
    return CopilotState(
        demande_brute=demande,
        intention="", action="",
        ambigue=False, score_confiance=1.0,
        code_client="", ref_article="", quantite=0.0,
        num_piece="", type_doc="", type_doc_code=0,
        date_debut="", date_fin="", mode_paiement="Virement",
        validation_ok=False, hub_validation="", reponse_brute="",
        rag_complement="", reponse_finale="",
        hallucination_flag=False, mem0_contexte="",
        dernier_type_doc=_dernier_type,
        dernier_num_piece=_dernier_num,
        dernier_code_client=ctx.get("dernier_code_client", ""),
        dernier_ref_article=ctx.get("dernier_ref_article", ""),
        dernier_quantite=ctx.get("dernier_quantite", 0.0),
        plan_execution=[], etape_courante=0,
        nom_client_brut=ctx.get("dernier_nom_client", ""),
        suggestion_en_attente={},
        pending_action={},
pending_document={},
attente_complements=False,
    )

def verifier_document_incomplet(state):
    doc = state.get("pending_document", {})

    type_doc = doc.get("type_doc")

    if type_doc == "BL_ACHAT":
        champs = [
            "code_fournisseur",
            "ref_article",
            "quantite",
            "prix_unitaire"
        ]
    elif type_doc == "BL":
        champs = [
            "code_client",
            "ref_article",
            "quantite"
        ]
    else:
        return None

    manquants = []

    for c in champs:
        if not doc.get(c):
            manquants.append(c)

    return manquants
async def noeud_complements(state):

    manquants = verifier_document_incomplet(state)

    if not manquants:
        state["attente_complements"] = False
        return state

    champ = manquants[0]

    questions = {
        "code_fournisseur":
            "Quel fournisseur ?",

        "code_client":
            "Quel client ?",

        "ref_article":
            "Quelle référence article ?",

        "quantite":
            "Quelle quantité ?",

        "prix_unitaire":
            "Quel prix unitaire ?"
    }

    state["attente_complements"] = True
    state["reponse_finale"] = questions[champ]

    return state
def injecter_complement(state):

    if not state.get("attente_complements"):
        return state

    doc = state.get("pending_document", {})
    texte = state["demande_brute"].strip()

    if not doc.get("prix_unitaire"):

        try:
            doc["prix_unitaire"] = float(
                texte.replace(",", ".")
            )
        except:
            pass

    elif not doc.get("quantite"):

        m = re.search(r"(\d+(?:[.,]\d+)?)", texte)

        if m:
            doc["quantite"] = float(
                m.group(1).replace(",", ".")
            )

    elif not doc.get("ref_article"):

        doc["ref_article"] = texte

    elif not doc.get("code_fournisseur"):

        doc["code_fournisseur"] = texte

    elif not doc.get("code_client"):

        doc["code_client"] = texte

    state["pending_document"] = doc

    return state
def _fusionner_demandes(precedente: str, complement: str) -> str:
    return (
        f"Demande initiale : {precedente}\n"
        f"Précision apportée par l'utilisateur : {complement}"
    )


def _extraire_dernier_document(final_state: dict) -> dict | None:
    if final_state.get("action") != "GENERER_DOC":
        return None
    if not final_state.get("validation_ok", True):
        return None
    rb = final_state.get("reponse_brute", "") or ""
    try:
        data = json.loads(rb)
        if data.get("DO_Piece"):
            return {
                "type_doc":    final_state.get("type_doc") or "BL",
                "num_piece":   data["DO_Piece"],
                "code_client": data.get("DO_Tiers", final_state.get("code_client", "")),
                "ref_article": data.get("AR_Ref",   final_state.get("ref_article", "")),
            }
    except Exception:
        pass
    m = re.search(r"\b([A-Z]{2,4}\d{3,})\b", rb)
    if not m:
        return None
    return {
        "type_doc":    final_state.get("type_doc") or "BL",
        "num_piece":   m.group(1),
        "code_client": final_state.get("code_client", ""),
        "ref_article": final_state.get("ref_article", ""),
    }


# ═════════════════════════════════════════════════════════════════════
# DÉCOUPAGE MULTI-DEMANDES
# ═════════════════════════════════════════════════════════════════════
_CONNECTEURS = re.compile(
    r"\bpuis\b|\bensuite\b|\baprès\b|\bet\s+(?=(?:crée|génère|transforme|règle|lance|fait|fais|liste|affiche|montre|vérifie))|"
    r"\bet\s+aussi\b|\bde\s+plus\b|\bégalement\b|,\s+(?=(?:crée|génère|liste|affiche|montre|vérifie|calcule|donne))",
    re.IGNORECASE
)


def _contient_multi_demandes(texte: str) -> bool:
    return bool(_CONNECTEURS.search(texte))

def _formater_liste_fournisseurs(data: dict) -> str:
    fournisseurs = data.get("fournisseurs", [])
    nb           = data.get("nb_fournisseurs", len(fournisseurs))
    if not fournisseurs:
        return "🏭 Aucun fournisseur enregistré."
    lignes = [f"🏭 Fournisseurs — {nb} fournisseur(s) :\n", "─" * 55]
    for f in fournisseurs:
        statut = f.get("CT_Validite", "VALIDE")
        icone  = "🔴" if statut == "BLOQUE" else "🟢"
        lignes.append(
            f"  {icone} {f['CT_Num']:<10} │ {f['CT_Intitule']:<30} │ {statut}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)
# ─────────────────────────────────────────────────────────────────────
# FORMATTERS DIRECTS
# ─────────────────────────────────────────────────────────────────────
def _formater_liste_articles(data: dict) -> str:
    articles = data.get("articles", [])
    nb       = data.get("nb_articles", len(articles))
    if not articles:
        return "📦 Aucun article dans le catalogue."
    lignes = [f"📦 Catalogue articles — {nb} référence(s) :\n", "─" * 55]
    for a in articles:
        stock  = a.get("stock", 0)
        alerte = " ⚠️ RUPTURE" if stock <= 0 else (" ⚠️ FAIBLE" if stock < 5 else "")
        lignes.append(
            f"  • {a['AR_Ref']:<14} │ {a['AR_Design']:<30} │ "
            f"Prix: {a.get('AR_PrixVen', 0):>8.2f} € │ Stock: {stock:>5.0f} u{alerte}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_liste_clients(data: dict) -> str:
    clients = data.get("clients", [])
    nb      = data.get("nb_clients", len(clients))
    if not clients:
        return "👥 Aucun client actif."
    lignes = [f"👥 Clients actifs — {nb} client(s) :\n", "─" * 55]
    for c in clients:
        statut = c.get("CT_Validite", c.get("statut", "VALIDE"))
        icone  = "🔴" if statut == "BLOQUE" else "🟡" if statut == "SUSPECT" else "🟢"
        lignes.append(
            f"  {icone} {c['CT_Num']:<10} │ {c['CT_Intitule']:<30} │ "
            f"CA: {c.get('ca_total', 0):>10.2f} € │ Fct: {c.get('nb_factures', 0)}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_top_clients(data: dict) -> str:
    clients = data.get("clients", [])
    top_n   = data.get("top_n", len(clients))
    if not clients:
        return "📊 Aucune donnée clients."
    lignes = [f"🏆 Top {top_n} clients par CA :\n", "─" * 55]
    for c in clients:
        lignes.append(
            f"  #{c.get('rang', '?'):<3} {c['code_client']:<10} │ "
            f"{c.get('nom_client', ''):<30} │ "
            f"CA: {c.get('ca_total', 0):>10.2f} € │ Fct: {c.get('nb_factures', 0)}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_factures(data: dict) -> str:
    factures  = data.get("factures", [])
    nb        = data.get("nb_factures", len(factures))
    intitule  = data.get("CT_Intitule", data.get("CT_Num", ""))
    total_ht  = data.get("total_ht", 0)
    total_att = data.get("total_en_attente", 0)
    total_reg = data.get("total_regle", 0)
    if not factures:
        return f"🧾 Aucune facture pour '{intitule}'."
    lignes = [
        f"🧾 Factures de '{intitule}' — {nb} facture(s) :",
        f"   Total HT        : {total_ht:,.2f} €",
        f"   Total réglé     : {total_reg:,.2f} €",
        f"   Total en attente: {total_att:,.2f} €",
        "─" * 55,
    ]
    for f in factures:
        regle = f.get("regle", False) or f.get("statut", "") == "RÉGLÉE"
        icone = "✅" if regle else "⏳"
        mnt   = f.get("montant_ht", f.get("DO_TotalHT", 0)) or 0
        lignes.append(
            f"  {icone} {f['DO_Piece']:<16} │ "
            f"{f.get('DO_Date', ''):<12} │ "
            f"{mnt:>8.2f} € │ {'RÉGLÉE' if regle else 'EN ATTENTE'}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_factures_fourn_impayees(data: dict) -> str:
    factures = data.get("factures", [])
    nb       = data.get("nb_factures", len(factures))
    total_du = data.get("total_du", 0)
    ct_num   = data.get("CT_Num", "")
    titre    = f"Factures fournisseur {ct_num}" if ct_num else "Toutes factures fournisseurs"
    if not factures:
        return f"✅ Aucune facture fournisseur impayée{' pour ' + ct_num if ct_num else ''}."
    lignes = [
        f"🧾  {titre} — non réglées : {nb} facture(s)",
        f"   Total dû : {total_du:,.2f} €",
        "─" * 65,
    ]
    for f in factures:
        mnt = f.get("montant_ht", 0) or 0
        lignes.append(
            f"  ⏳ {f['DO_Piece']:<16} │ "
            f"{f.get('CT_Intitule', f.get('CT_Num', '')):<25} │ "
            f"{f.get('DO_Date', ''):<12} │ {mnt:>10.2f} €"
        )
    lignes.append("─" * 65)
    return "\n".join(lignes)


def _formater_factures_impayees(data: dict) -> str:
    factures = data.get("factures", [])
    nb       = data.get("nb_factures", len(factures))
    total_du = data.get("total_du", 0)
    if not factures:
        return "✅ Aucune facture impayée."
    lignes = [
        f"⚠️  Factures impayées — {nb} facture(s)",
        f"   Total dû : {total_du:,.2f} €",
        "─" * 55,
    ]
    for f in factures:
        mnt = f.get("montant_ht", f.get("DO_TotalHT", 0)) or 0
        lignes.append(
            f"  ⏳ {f['DO_Piece']:<16} │ "
            f"{f.get('CT_Intitule', f.get('CT_Num', '')):<25} │ "
            f"{f.get('DO_Date', ''):<12} │ {mnt:>8.2f} €"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_fiche_client(data: dict) -> str:
    if data.get("statut") == "NON_TROUVE":
        return f"❌ Client introuvable : {data.get('message', '')}"
    validite = data.get("CT_Validite", data.get("CT_Statut", "VALIDE"))
    icone    = "🔴" if validite == "BLOQUÉ" else "🟡" if validite == "SUSPECT" else "🟢"
    return (
        f"👤 Fiche Client\n{'─' * 45}\n"
        f"  Code          : {data.get('CT_Num', '')}\n"
        f"  Raison sociale: {data.get('CT_Intitule', '')}\n"
        f"  Statut        : {icone} {validite}\n"
        f"  Encours       : {data.get('CT_Encours', 0):,.2f} € / max {data.get('CT_EncoursMax', 0):,.2f} €\n"
        f"{'─' * 45}\n"
        f"  CA Total      : {data.get('CA_Total', 0):,.2f} €\n"
        f"  Nb Factures   : {data.get('NB_Factures', 0)}\n"
        f"  Encours Fct   : {data.get('Encours_Factures', 0):,.2f} €"
    )


def _formater_palmares(data: dict) -> str:
    palmares = data.get("palmares", [])
    top_n    = data.get("top_n", len(palmares))
    if not palmares:
        return "📊 Aucune donnée de ventes."
    lignes = [f"🏆 Top {top_n} articles par CA :\n", "─" * 55]
    for a in palmares:
        lignes.append(
            f"  #{a.get('rang', '?'):<3} {a['AR_Ref']:<14} │ "
            f"{a.get('AR_Design', ''):<28} │ "
            f"CA: {a.get('ca_article', 0):>8.2f} € │ Qté: {a.get('qte_vendue', 0):.0f}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_ca_global(data: dict) -> str:
    return (
        f"💰 Chiffre d'Affaires Global\n{'─' * 40}\n"
        f"  CA HT         : {data.get('ca_ht', 0):>12,.2f} €\n"
        f"  TVA (19%)     : {data.get('tva_19', 0):>12,.2f} €\n"
        f"  CA TTC        : {data.get('ca_ttc', 0):>12,.2f} €\n"
        f"{'─' * 40}\n"
        f"  Nb factures   : {data.get('nb_factures', 0)}\n"
        f"  Nb clients    : {data.get('nb_clients', 0)}\n"
        f"  Période       : {data.get('date_debut', '?')} → {data.get('date_fin', '?')}"
    )


def _formater_kpi(data: dict) -> str:
    return (
        f"📊 Dashboard KPI\n{'─' * 40}\n"
        f"  CA Total      : {data.get('ca_total', 0):>12,.2f} €\n"
        f"  Marge (22%)   : {data.get('marge_22', 0):>12,.2f} €\n"
        f"  Panier moyen  : {data.get('panier_moy', 0):>12,.2f} €\n"
        f"{'─' * 40}\n"
        f"  Clients       : {data.get('nb_clients', 0)}\n"
        f"  Factures      : {data.get('nb_factures', 0)}\n"
        f"  Documents     : {data.get('nb_docs', 0)}"
    )


def _formater_rentabilite(data: dict) -> str:
    articles = data.get("articles", [])
    if not articles:
        return "📊 Aucune donnée de rentabilité."
    lignes = [f"📊 Rentabilité par article — {len(articles)} ligne(s) :\n", "─" * 65]
    for a in articles:
        taux  = a.get("taux_marge", 0)
        icone = "🟢" if taux >= 30 else "🟡" if taux >= 15 else "🔴"
        lignes.append(
            f"  {icone} {a['AR_Ref']:<14} │ {a.get('AR_Design', ''):<25} │ "
            f"CA: {a.get('ca_vente', 0):>8.2f} € │ "
            f"Marge: {a.get('marge_brute', 0):>8.2f} € │ Taux: {taux:>5.1f}%"
        )
    lignes.append("─" * 65)
    return "\n".join(lignes)


def _formater_saisonnalite(data: dict) -> str:
    mois_list = data.get("mois", [])
    if not mois_list:
        return "📅 Aucune donnée mensuelle."
    lignes = [f"📅 CA Mensuel — {len(mois_list)} mois :\n", "─" * 50]
    max_ca = max((x.get("ca_mensuel", 0) for x in mois_list), default=1) or 1
    for m in mois_list:
        ca    = m.get("ca_mensuel", 0)
        barre = "█" * int(ca / max_ca * 15)
        lignes.append(
            f"  {m.get('mois', ''):<8} │ {barre:<15} │ "
            f"{ca:>10,.2f} € │ {m.get('nb_factures', 0)} fct"
        )
    lignes.append("─" * 50)
    return "\n".join(lignes)


def _formater_dso(data: dict) -> str:
    clients  = data.get("clients", [])
    dso_glob = data.get("dso_global", 0)
    lignes   = [f"⏱️  DSO Global : {dso_glob:.1f} jours\n", "─" * 50]
    for c in clients:
        dso_c = c.get("dso_jours", 0)
        icone = "🔴" if dso_c > 60 else "🟡" if dso_c > 30 else "🟢"
        lignes.append(
            f"  {icone} {c.get('CT_Num', ''):<10} │ "
            f"{c.get('CT_Intitule', ''):<28} │ "
            f"DSO: {dso_c:>5.1f} j │ Fct: {c.get('nb_factures', 0)}"
        )
    lignes.append("─" * 50)
    return "\n".join(lignes)


def _formater_rfm(data: dict) -> str:
    clients = data.get("clients", [])
    nb      = data.get("nb_clients", len(clients))
    lignes  = [f"🎯 Analyse RFM — {nb} client(s) :\n", "─" * 65]
    for c in clients:
        statut  = c.get("statut", "VALIDE")
        icone   = "🔴" if statut == "BLOQUÉ" else "🟡" if statut == "SUSPECT" else "🟢"
        dernier = c.get("derniere_commande", "Jamais")
        lignes.append(
            f"  {icone} {c['CT_Num']:<10} │ {c.get('CT_Intitule', ''):<28} │ "
            f"CA: {c.get('ca_total', 0):>8.2f} € │ Dernier: {dernier}"
        )
    lignes.append("─" * 65)
    return "\n".join(lignes)


def _formater_clients_baisse(data: dict) -> str:
    clients = data.get("clients", [])
    nb      = data.get("nb", len(clients))
    if not clients:
        return "✅ Aucun client en baisse de CA détecté."
    lignes = [f"📉 Clients en baisse CA — {nb} client(s) :\n", "─" * 60]
    for c in clients:
        var = c.get("variation_pct", 0)
        lignes.append(
            f"  📉 {c['CT_Num']:<10} │ {c.get('CT_Intitule', ''):<28} │ "
            f"Récent: {c.get('ca_recent', 0):>8.2f} € │ "
            f"Ancien: {c.get('ca_ancien', 0):>8.2f} € │ Var: {var:>+.1f}%"
        )
    lignes.append("─" * 60)
    return "\n".join(lignes)


_FORMATEURS_JSON: dict[str, callable] = {
    "LISTE_ARTICLES":         _formater_liste_articles,
    "LISTE_CLIENTS":          _formater_liste_clients,
    "TOP_CLIENTS":            _formater_top_clients,
    "PALMARES_ARTICLES":      _formater_palmares,
    "CA_GLOBAL":              _formater_ca_global,
    "CLIENTS_BAISSE":         _formater_clients_baisse,
    "FACTURES_NON_REGLEES":        _formater_factures_impayees,
    "FACTURES_NON_REGLEES_FOURN":   _formater_factures_fourn_impayees,
    "TOUTES_FACTURES_CLIENT": _formater_factures,
    "FICHE_CLIENT":           _formater_fiche_client,
    "RENTABILITE":            _formater_rentabilite,
    "SAISONNALITE":           _formater_saisonnalite,
    "DSO":                    _formater_dso,
    "RFM":                    _formater_rfm,
    "DASHBOARD_EXCEL":        _formater_kpi,
}

_ACTIONS_DEJA_TEXTE: set[str] = {
    "VERIFIER_STOCK", "STATUT_CLIENT",
    "LISTE_FOURNISSEURS", "TOP_FOURNISSEURS", "FICHE_FOURNISSEUR",
}


def _formater_reponse_directe(action: str, reponse_brute: str) -> str | None:
    if not reponse_brute or reponse_brute.startswith("__"):
        return None
    if action in _ACTIONS_DEJA_TEXTE:
        return reponse_brute
    try:
        data = json.loads(reponse_brute)
    except (json.JSONDecodeError, ValueError):
        if reponse_brute.startswith(("📊", "Question :", "─", "👥", "📦", "🏆")):
            return reponse_brute
        return None
    if not isinstance(data, dict):
        return None
    statut = data.get("statut", "")
    if statut in _STATUTS_ERREUR_MCP:
        return data.get("message", f"❌ Erreur : {statut}")
    if statut == "OK" and action in _FORMATEURS_JSON:
        try:
            return _FORMATEURS_JSON[action](data)
        except Exception as e:
            print(f"   ⚠️  [Formateur] {action} : {_safe_str(e)}")
            return None
    if statut in _STATUTS_ACTIONS_V3_OK:
        msg = data.get("message", "")
        if msg:
            alertes = data.get("alertes", [])
            if alertes:
                msg += "\n\n⚠️ Alertes :\n" + "\n".join(f"   {a}" for a in alertes)
            return msg
    return None


# ─────────────────────────────────────────────────────────────────────
# MULTI-DEMANDES
# ─────────────────────────────────────────────────────────────────────
async def decouper_demande_composite(demande: str) -> list[dict]:
    if not _contient_multi_demandes(demande):
        return [{"demande": demande, "sequentiel": False, "index": 0}]
    prompt = f"""Tu es un expert ERP Sage 100.
Analyse ce message et découpe-le en actions atomiques.
Message : "{demande}"
RÈGLES :
1. Chaque action doit être complète
2. Indique si l'action dépend du résultat de la précédente (sequentiel: true)
3. Si une seule action → retourne un tableau avec un seul élément
Réponds UNIQUEMENT avec ce JSON :
[{{"demande": "texte complet action 1", "sequentiel": false}},
 {{"demande": "texte complet action 2", "sequentiel": true}}]"""
    try:
        texte = await _invoke_llm(prompt, use_smart=False)
        texte = texte.replace("```json", "").replace("```", "").strip()
        m = re.search(r"\[.*\]", texte, re.DOTALL)
        if not m:
            return [{"demande": demande, "sequentiel": False, "index": 0}]
        parsed = json.loads(m.group(0))
        if not isinstance(parsed, list) or not parsed:
            return [{"demande": demande, "sequentiel": False, "index": 0}]
        result = []
        for i, item in enumerate(parsed):
            if isinstance(item, str):
                result.append({"demande": item.strip(), "sequentiel": False, "index": i})
            elif isinstance(item, dict):
                d = item.get("demande", "").strip()
                if d:
                    result.append({
                        "demande": d,
                        "sequentiel": bool(item.get("sequentiel", False)),
                        "index": i,
                    })
        if len(result) <= 1:
            return [{"demande": demande, "sequentiel": False, "index": 0}]
        return result
    except Exception:
        return [{"demande": demande, "sequentiel": False, "index": 0}]


# ─────────────────────────────────────────────────────────────────────
# RÉSOLUTION RÉFÉRENCES CONTEXTUELLES
# ─────────────────────────────────────────────────────────────────────
_REFS_CONTEXTUELLES = re.compile(
    r"\b(ce|cet|cette|celui-ci|celle-ci|ce\s+bl|cette\s+facture|"
    r"ce\s+document|le\s+même|la\s+même|ce\s+of|ce\s+bon)\b",
    re.IGNORECASE
)


def _resoudre_references(demande: str, contexte_precedent: dict) -> str:
    if not _REFS_CONTEXTUELLES.search(demande):
        return demande
    num_piece   = contexte_precedent.get("num_piece", "")
    type_doc    = contexte_precedent.get("type_doc", "")
    demande_resolue = demande
    if num_piece:
        demande_resolue = re.sub(
            r"\b(ce|cet|cette|celui-ci|celle-ci|ce\s+bl|cette\s+facture|ce\s+document|ce\s+of)\b",
            f"le {type_doc} {num_piece}", demande_resolue, flags=re.IGNORECASE,
        )
    if demande_resolue != demande:
        print(f"   🔗 [Résolution] '{demande}' → '{demande_resolue}'")
    return demande_resolue


# ─────────────────────────────────────────────────────────────────────
# HELPERS HUB
# ─────────────────────────────────────────────────────────────────────
async def _hub_resoudre_type_doc(libelle: str) -> dict:
    if not libelle:
        return {}
    try:
        text = await mcp_pool.call("hub", "resoudre_type_document", {"libelle": libelle})
        return json.loads(text)
    except Exception:
        return {}


async def _hub_valider_demande(type_action: str, payload: dict) -> dict:
    try:
        text = await mcp_pool.call("hub", "valider_demande_metier", {
            "type_action": type_action,
            "payload": json.dumps(payload, ensure_ascii=False),
        })
        return json.loads(text)
    except Exception as e:
        return {"valide": False, "message": _safe_str(e)}


async def _hub_contexte_client(
    code_client: str, statut: str, stock_dispo: float, qte: float
) -> dict:
    try:
        text = await mcp_pool.call("hub", "construire_contexte_client", {
            "code_client":       code_client,
            "statut":            statut,
            "stock_disponible":  stock_dispo,
            "quantite_demandee": qte,
        })
        return json.loads(text)
    except Exception:
        return {"decision": "VALIDER", "alertes": [], "pret_pour_livraison": True}


# ─────────────────────────────────────────────────────────────────────
# EXTRACTION NOM CLIENT
# ─────────────────────────────────────────────────────────────────────
_client_nom_cache: dict[str, str] = {}

_PREFIXES_PARASITES = re.compile(
    r"^\s*(?:le\s+|la\s+|les\s+|l['\u2019]\s*|du\s+|de\s+la\s+|de\s+)?",
    re.IGNORECASE
)
_SUFFIXES_PARASITES = re.compile(
    r"\s+(?:dans|de|du|pour|avec|sur|est|a|au|aux|et|ou|les|des|qui|dont|que)\s*$",
    re.IGNORECASE
)
_MOTS_PREFIX_CLIENT = (
    r"(?:informations?\s+sur\s+(?:le\s+|la\s+)?(?:client\s+|société\s+|tiers\s+)?)"
    r"|(?:fiche\s+(?:du\s+|de\s+la\s+)?(?:client\s+|société\s+|tiers\s+)?)"
    r"|(?:statut\s+(?:actuel\s+)?(?:du\s+|de\s+la\s+)?(?:client\s+|société\s+|tiers\s+)?)"
    r"|(?:(?:non\s+réglées?\s+)?(?:du\s+|de\s+la\s+)?(?:client\s+|tiers\s+))"
    r"|(?:(?:toutes?\s+les\s+)?factures?\s+(?:du\s+|de\s+la\s+)(?:client\s+))"
    r"|(?:pour\s+(?:le\s+|la\s+)?(?:client\s+|tiers\s+)?)"
    r"|(?:client\s+)"
)
_PATTERN_NOM_CLIENT = re.compile(
    r"(?:" + _MOTS_PREFIX_CLIENT + r")"
    r"((?:société\s+|sarl\s+|sas\s+|sa\s+)?"
    r"[A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&'.]{1,80}?)"
    r"(?:\s*[?.,;!]|\s*$)",
    re.IGNORECASE
)


def _nettoyer_nom_client(nom: str) -> str:
    nom = _PREFIXES_PARASITES.sub("", nom).strip()
    nom = _SUFFIXES_PARASITES.sub("", nom).strip()
    return re.sub(r"\s{2,}", " ", nom).strip()


# FIX 5 : _est_nom_valide rejette les noms contenant des chiffres
def _est_nom_valide(nom: str) -> bool:
    if not nom or len(nom) < 2:
        return False
    # Rejeter si contient des chiffres (montants, codes numériques, "4k", etc.)
    if re.search(r'\d', nom):
        return False
    mots = nom.strip().split()
    if len(mots) >= 2:
        return True
    mot_lower = mots[0].lower()
    invalides = {
        "client", "tiers", "societe", "société", "entreprise",
        "sarl", "sa", "sas", "le", "la", "les", "un", "une",
        "pour", "avec", "sur", "du", "de", "plus", "moins",
        "que", "dont", "ayant", "liste", "tous", "toutes",
        "bons", "bon", "livraison", "commande", "fabrication",
        "facture", "factures", "pieces", "piece", "unites", "unite",
        # mots métier génériques → ne jamais résoudre en client
        "fournisseur", "fournisseurs", "fourn", "grossiste",
        "achat", "achats", "reception", "réception",
        "article", "articles", "catalogue", "produit", "produits",
        "stock", "stocks", "rupture",
        # mots NL2SQL / filtres quantitatifs → jamais un nom de client
        "impayées", "impayees", "impayés", "impayés", "impayé",
        "encours", "supérieur", "superieur", "inférieur", "inferieur",
        "actifs", "actif", "inactifs", "inactif", "bloqués", "bloques",
        "reglées", "réglées", "reglés", "réglés",
    }
    return mot_lower not in invalides and len(mots[0]) > 2


_PREFIXES_PIECES = re.compile(r"^(FA|FF|BL|BC|BF|OF|AV|BR|AF)[A-Z0-9]*\d+$", re.IGNORECASE)

def _extraire_code_ou_nom_depuis_texte(db: str) -> tuple[str, str]:
    # FIX BUGA : \d{2,} au lieu de \d{3,} pour couvrir FOUR01, CLI01, F001, etc.
    m_code = re.search(r"\b([A-Z]{2,6}\d{2,})\b", db, re.IGNORECASE)
    if m_code:
        code = m_code.group(1).upper()
        # Si c'est un numéro de pièce document → ne pas l'assigner comme client
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


# FIX 6 : _rechercher_client_par_nom — len > 3, sortie rapide si liste vide
async def _rechercher_client_par_nom(nom: str) -> str:
    if not nom or len(nom.strip()) < 2:
        return ""
    nom_lower = nom.lower().strip()
    if nom_lower in _client_nom_cache:
        cached = _client_nom_cache[nom_lower]
        if cached:
            print(f"   ⚡ [Cache Nom] '{nom}' → {cached}")
        return cached

    _MOTS_PARASITES = {"le","la","les","du","de","des","et","ou","un","une","pour","avec","sur"}
    # FIX 6 : len > 3 (était > 1) + rejet chiffres et mots mixtes numériques
    mots_significatifs = [
        m for m in nom_lower.split()
        if m not in _MOTS_PARASITES
        and len(m) > 3
        and not m.isdigit()
        and not re.search(r'\d', m)   # rejette "4k", "27p", "1000", etc.
    ]
    # FIX 6 : sortie immédiate si liste vide (évite les faux positifs MCP)
    if not mots_significatifs:
        _client_nom_cache[nom_lower] = ""
        print(f"   ⚠️  [Recherche Nom] '{nom}' → aucun mot significatif, abandon")
        return ""

    print(f"   🔍 [Recherche Nom] '{nom}' → mots : {mots_significatifs}")

    async def _appel_fiche(query: str) -> str:
        try:
            return await mcp_pool.call("nl2sql", "rechercher_fiche_client", {"code_client": query})
        except Exception:
            return ""

    async def _appel_statut(query: str) -> str:
        try:
            return await mcp_pool.call("nl2sql", "verifier_statut_client", {"code_client": query})
        except Exception:
            return ""

    _CODES_INVALIDES_CACHE = {
        "client", "clients", "tiers", "societe", "société",
        "entreprise", "none", "null", "inconnu", "unknown",
        "non_trouve", "vide", "absent",
    }

    def _extraire_code(text: str) -> str:
        try:
            data = json.loads(text)
            code = data.get("CT_Num", "")
            if (code
                    and code not in ("NON_TROUVE", "")
                    and code.lower() not in _CODES_INVALIDES_CACHE
                    and len(code) >= 3):
                return code
        except Exception:
            pass
        m = re.search(r"CT_Num[:\s]+([A-Z0-9]+)", text, re.IGNORECASE)
        code_m = m.group(1).strip() if m else ""
        return code_m if code_m.lower() not in _CODES_INVALIDES_CACHE else ""

    for essai_fn, essai_val in [(_appel_fiche, nom), (_appel_fiche, " ".join(mots_significatifs))]:
        t = await essai_fn(essai_val)
        if t:
            code = _extraire_code(t)
            if code:
                _client_nom_cache[nom_lower] = code
                print(f"   ✅ [MCP] '{essai_val}' → {code}")
                return code

    for taille in range(len(mots_significatifs), 0, -1):
        for combo in itertools.combinations(mots_significatifs, taille):
            sous_nom = " ".join(combo)
            if len(sous_nom) < 2:
                continue
            for fn in (_appel_fiche, _appel_statut):
                t = await fn(sous_nom)
                if t:
                    code = _extraire_code(t)
                    if code:
                        _client_nom_cache[nom_lower] = code
                        print(f"   ✅ [MCP Combo] '{sous_nom}' → {code}")
                        return code

    _client_nom_cache[nom_lower] = ""
    print(f"   ⚠️  [Recherche Nom] '{nom}' introuvable")
    return ""


# ─────────────────────────────────────────────────────────────────────
# HELPERS MCP ACTIONS
# ─────────────────────────────────────────────────────────────────────
def _parse_mcp_response(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {"statut": "ERREUR", "message": "Réponse vide"}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"statut": "ERREUR", "message": str(raw)}


async def _mcp_workflow_bl_achat(
    code_fournisseur: str, ref_article: str, quantite: float, prix_unitaire: float = 0.0
) -> dict:
    raw = await mcp_pool.call("actions", "workflow_bl_achat", {
        "code_fournisseur": code_fournisseur,
        "ref_article":      ref_article,
        "quantite":         quantite,
        "prix_unitaire":    prix_unitaire,
    })
    return _parse_mcp_response(raw)


async def _mcp_workflow_bl(
    code_client: str, ref_article: str, quantite: float, prix_unitaire: float = 0.0
) -> dict:
    raw = await mcp_pool.call("actions", "workflow_bl", {
        "code_client":   code_client,
        "ref_article":   ref_article,
        "quantite":      quantite,
        "prix_unitaire": prix_unitaire,
    })
    return _parse_mcp_response(raw)


async def _mcp_workflow_of(
    ref_article: str, quantite: float, code_client: str = "PROD-INT"
) -> dict:
    raw = await mcp_pool.call("actions", "workflow_of", {
        "ref_article": ref_article,
        "quantite":    quantite,
        "code_client": code_client,
    })
    return _parse_mcp_response(raw)


async def _mcp_workflow_bf(
    ref_article: str, quantite: float, num_of: str = "", code_client: str = "PROD-INT"
) -> dict:
    raw = await mcp_pool.call("actions", "workflow_bf", {
        "ref_article": ref_article,
        "quantite":    quantite,
        "num_of":      num_of,
        "code_client": code_client,
    })
    return _parse_mcp_response(raw)


# ─────────────────────────────────────────────────────────────────────
# NŒUD CLASSIFIER — v9.3
# ─────────────────────────────────────────────────────────────────────
async def noeud_classifier(state: CopilotState) -> CopilotState:
    state = injecter_complement(state)

    if state.get("attente_complements"):
        manquants = verifier_document_incomplet(state)
        if not manquants:
            state["attente_complements"] = False
            state["action"] = "GENERER_DOC"
            return state

    print("\n🧠 [Orchestrateur] Classification de la demande...")
    question = state["demande_brute"]
    t0       = time.perf_counter()

    # ══════════════════════════════════════════════════════════════
    # ÉTAPE 0 — Pré-classification regex (0ms, prioritaire)
    # ══════════════════════════════════════════════════════════════
    action_preclass = _pre_classifier(question)
    if action_preclass:
        entites_ner = _ner_extraire_entites(question)
        for _champ in ("client", "article"):
            _v = entites_ner.get(_champ, "")
            if _v.lower().strip() in _MOTS_GENERIQUES_NER:
                entites_ner.pop(_champ, None)

        _regex_code, _nom_regex = _extraire_code_ou_nom_depuis_texte(question)
        _ner_client = entites_ner.get("client", "")

        if _regex_code:
            state["code_client"]     = _regex_code
            state["nom_client_brut"] = ""
        elif _ner_client:
            state["nom_client_brut"] = _ner_client
            state["code_client"]     = ""
        elif _nom_regex:
            state["nom_client_brut"] = _nom_regex
            state["code_client"]     = ""

        if state.get("nom_client_brut") and not state.get("code_client"):
            code_trouve = await _rechercher_client_par_nom(state["nom_client_brut"])
            if code_trouve:
                state["code_client"] = code_trouve
            elif action_preclass == "CREER_CLIENT":
                # FIX CREER_CLIENT : client nouveau → générer code incrémental
                state["code_client"] = _generer_code_client(state["nom_client_brut"])
                print(f"   🔧 [CREER_CLIENT] Nouveau client → code généré : '{state['code_client']}'")
            elif action_preclass == "CREER_FOURNISSEUR":
                state["code_client"] = _generer_code_fournisseur(state["nom_client_brut"])
                # Effacer ref_article s'il = nom fournisseur
                _nom_up = (state.get("nom_client_brut") or "").upper()
                if _nom_up and state.get("ref_article", "").upper() == _nom_up:
                    state["ref_article"] = ""
                print(f"   🔧 [CREER_FOURNISSEUR] Nouveau fournisseur → code généré : '{state['code_client']}'")

        # FIX 1 : extraction article insensible à la casse
        # Priorité 1 : mot explicitement après "article", "stock de", "ref", "produit"
        _ref_article_trouvee = ""
        m_art_ctx = re.search(
            r"(?:article|stock\s+de\s+(?:l['\u2019])?(?:article\s+)?|r[eé]f(?:[eé]rence)?\s+|produit)\s+([A-Za-z][A-Za-z0-9\-]{1,})",
            question, re.IGNORECASE
        )
        if m_art_ctx:
            cand = m_art_ctx.group(1).upper()
            if cand not in _EXCL_ARTICLE and not cand.startswith("CLI"):
                _ref_article_trouvee = cand
                print(f"   🔎 [PreClass] ref_article (contexte article) : '{cand}'")
        # Priorité 2 : token ressemblant à une vraie réf ERP (chiffres + lettres)
        if not _ref_article_trouvee:
            for mot in re.findall(r"\b([A-Za-z][A-Za-z0-9\-]{2,})\b", question):
                mot_upper = mot.upper()
                has_digit = bool(re.search(r"\d", mot_upper))
                has_dash_ref = ("-" in mot_upper and
                    all(len(p) >= 2 for p in mot_upper.split("-")) and
                    any(len(p) >= 3 for p in mot_upper.split("-")))
                if (has_digit or has_dash_ref) and mot_upper not in _EXCL_ARTICLE and not mot_upper.startswith("CLI"):
                    _ref_article_trouvee = mot_upper
                    print(f"   🔎 [PreClass] ref_article (ref ERP) : '{mot_upper}'")
                    break
        state["ref_article"] = _ref_article_trouvee

        # FIX 2 CREER_CLIENT / CREER_FOURNISSEUR : effacer ref_article = nom tiers
        if action_preclass in ("CREER_CLIENT", "CREER_FOURNISSEUR"):
            nom_up = (state.get("nom_client_brut") or "").upper()
            if nom_up and state.get("ref_article", "").upper() == nom_up:
                state["ref_article"] = ""
                print(f"   🧹 [CREER_CLIENT] ref_article '{nom_up}' effacé (= nom client)")

        # Extraction quantité
        m_q = re.search(
            r"(?:quantit[eé]s?\s*[=:>]?\s*|qte\s*[=:]?\s*|\b)(\d+(?:[.,]\d+)?)\s*(?:pièces?|pieces?|unités?|u\.?\b)?",
            question, re.IGNORECASE
        )
        if m_q:
            val = float(m_q.group(1).replace(",", "."))
            if val > 0:
                state["quantite"] = val

        # ── Pour GENERER_DOC : détecter type_doc (BL_ACHAT en priorité) ──
        if action_preclass == "GENERER_DOC" and not state.get("type_doc"):
            q_lower = question.lower()
            if re.search(r"bl\s+achat|bon\s+de\s+r[eé]ception|r[eé]ception\s+fournisseur", q_lower):
                state["type_doc"] = "BL_ACHAT"
                # Extraction souple des champs BL_ACHAT
                m_four = re.search(r"fournisseur\s+([A-Z0-9]+)", question, re.IGNORECASE)
                m_art  = re.search(r"article\s+([A-Z0-9]+)", question, re.IGNORECASE)
                m_qte  = re.search(r"quantit[eé]s?\s*[=:>]?\s*(\d+(?:[.,]\d+)?)", question, re.IGNORECASE)
                m_prix = re.search(r"prix\s*[=:>]?\s*(\d+(?:[.,]\d+)?)", question, re.IGNORECASE)
                
                doc = state.get("pending_document", {})
                if m_four: doc["code_fournisseur"] = m_four.group(1).upper()
                if m_art:  doc["ref_article"]      = m_art.group(1).upper()
                if m_qte:  doc["quantite"]         = float(m_qte.group(1).replace(",", "."))
                if m_prix: doc["prix_unitaire"]    = float(m_prix.group(1).replace(",", "."))
                doc["type_doc"] = "BL_ACHAT"
                state["pending_document"] = doc
                
                # Mettre à jour state principal
                if doc.get("code_fournisseur"): state["code_client"] = doc["code_fournisseur"]
                if doc.get("ref_article"):      state["ref_article"] = doc["ref_article"]
                if doc.get("quantite"):         state["quantite"]    = doc["quantite"]
            elif re.search(r"\bbf\b|bon\s+de\s+fabrication", q_lower):   state["type_doc"] = "BF"
            elif re.search(r"\bof\b|ordre\s+de\s+fabrication", q_lower): state["type_doc"] = "OF"
            elif re.search(r"\bbc\b|bon\s+de\s+commande", q_lower):     state["type_doc"] = "BC"
            elif re.search(r"facture|facturer", q_lower):                state["type_doc"] = "FACTURE"
            else:                                                          state["type_doc"] = "BL"

        # FIX 3 : TRANSFORMER_DOC — extraction num_piece + type_doc destination
        if action_preclass == "TRANSFORMER_DOC":
            # Extraire le numéro de pièce source (OF260625..., BL001..., etc.)
            m_piece = re.search(
                r"\b((?:OF|BL|BC|FA|BF)[A-Z0-9]{3,})\b", question, re.IGNORECASE
            )
            if m_piece:
                state["num_piece"] = m_piece.group(1).upper()
                print(f"   🔎 [Fix3] num_piece extrait : '{state['num_piece']}'")
            elif state.get("dernier_num_piece"):
                state["num_piece"] = state["dernier_num_piece"]
                print(f"   🔗 [Fix3] num_piece hérité session : '{state['num_piece']}'")
            # Détecter le type destination
            q_lower = question.lower()
            if re.search(r"\bbf\b|bon\s+de\s+fabrication", q_lower):
                state["type_doc"] = "BF"
            elif re.search(r"facture|facturer", q_lower):
                state["type_doc"] = "FACTURE"
            elif re.search(r"\bbl\b|bon\s+de\s+livraison", q_lower):
                state["type_doc"] = "BL"
            elif re.search(r"\bbc\b|bon\s+de\s+commande", q_lower):
                state["type_doc"] = "BC"
            # Ambiguïté seulement si num_piece absent
            if not state.get("num_piece"):
                state["ambigue"] = True

        # ── Pour MODIFIER_STATUT ──────────────────────────────────
        if action_preclass == "MODIFIER_STATUT" and not state.get("type_doc"):
            q_lower = question.lower()
            if re.search(r"d[eé]bloquer?|r[eé]activer?|valider?", q_lower):
                state["type_doc"] = "VALIDE"
            else:
                state["type_doc"] = "BLOQUE"

        state["action"]          = action_preclass
        state["score_confiance"] = 1.0
        state["ambigue"]         = state.get("ambigue", False)
        state["intention"]       = "ERP"

        # ── GENERER_DOC : ambiguïté si client/article/quantité manquants
        if action_preclass == "GENERER_DOC":
            type_d = (state.get("type_doc") or "").upper()
            _a_client = bool(state.get("code_client") or state.get("nom_client_brut"))
            if type_d not in {"OF", "BF"} and not _a_client:
                state["ambigue"] = True
            if not state.get("ref_article"):
                state["ambigue"] = True
            if state.get("quantite", 0.0) <= 0.0:
                state["ambigue"] = True

        # ── MODIFIER_STATUT : ambiguïté si client absent ──────────
        if action_preclass == "MODIFIER_STATUT":
            q_lower = question.lower()
            if re.search(r"d[eé]bloquer?|r[eé]activer?|valider?|activer?", q_lower):
                state["type_doc"] = "VALIDE"
            else:
                state["type_doc"] = "BLOQUE"
            if not state.get("code_client"):
                state["ambigue"] = True

        # ── CREER_CLIENT / REGLEMENT / CREER_AVOIR ────────────────
        if action_preclass == "CREER_CLIENT":
            # PATCH B-2 : si on a un nom mais pas de code, générer le code
            nom_brut = state.get("nom_client_brut") or ""
            if not state.get("code_client") and nom_brut:
                state["code_client"] = _generer_code_client(nom_brut)
                print(f"   🔧 [CREER_CLIENT] Code généré depuis nom '{nom_brut}' → '{state['code_client']}'")
            # FIX 2 : si nom_client_brut vide, extraire depuis la demande (noms courts inclus)
            if not state.get("code_client"):
                m_cli = re.search(
                    r"(?:cr[eé][eé]?|nouveau|ajouter?)\s+(?:le\s+|un\s+)?(?:client|tiers)\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&'.]{1,60}?)(?:\s*[?.,;!]|\s*$)",
                    question, re.IGNORECASE
                )
                if not m_cli:
                    m_cli = re.search(
                        r"(?:client|tiers)\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&'.]{1,60}?)(?:\s*[?.,;!]|\s*$)",
                        question, re.IGNORECASE
                    )
                if m_cli:
                    nom_extrait = m_cli.group(1).strip()
                    _invalides_cli = {"le","la","les","du","de","un","une","des","pour","avec","sur"}
                    if nom_extrait.lower() not in _invalides_cli and len(nom_extrait) >= 2:
                        state["nom_client_brut"] = nom_extrait
                        state["code_client"] = _generer_code_client(nom_extrait)
                        print(f"   🔧 [CREER_CLIENT] Nom extrait: '{nom_extrait}' → Code: '{state['code_client']}'")
                # FIX 3 : fallback sur ref_article si toujours vide
                if not state.get("code_client") and state.get("ref_article"):
                    nom_secours = state["ref_article"]
                    state["nom_client_brut"] = nom_secours
                    state["code_client"] = _generer_code_client(nom_secours)
                    state["ref_article"] = ""
                    print(f"   🔧 [CREER_CLIENT] Nom depuis ref_article: '{nom_secours}' → Code: '{state['code_client']}'")
                if not state.get("code_client"):
                    state["ambigue"] = True
            
        if action_preclass == "CREER_FOURNISSEUR":
            nom_brut = state.get("nom_client_brut") or ""
            if not state.get("code_client") and nom_brut:
                state["code_client"] = _generer_code_fournisseur(nom_brut)
                print(f"   🔧 [CREER_FOURNISSEUR] Code généré depuis nom '{nom_brut}' → '{state['code_client']}'")
            if not state.get("code_client"):
                m_fourn = re.search(
                    r"(?:cr[eé][eé]?|nouveau|ajouter?)\s+(?:le\s+|un\s+)?fournisseur\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&'.]{1,60}?)(?:\s*[?.,;!]|\s*$)",
                    question, re.IGNORECASE
                )
                if not m_fourn:
                    m_fourn = re.search(
                        r"fournisseur\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&'.]{1,60}?)(?:\s*[?.,;!]|\s*$)",
                        question, re.IGNORECASE
                    )
                if m_fourn:
                    nom_extrait = m_fourn.group(1).strip()
                    _invalides = {"le","la","les","du","de","un","une","des","pour","avec","sur"}
                    if nom_extrait.lower() not in _invalides and len(nom_extrait) >= 2:
                        state["nom_client_brut"] = nom_extrait
                        state["code_client"] = _generer_code_fournisseur(nom_extrait)
                        # Effacer ref_article s'il a capturé le nom du fournisseur
                        if state.get("ref_article", "").upper() == nom_extrait.upper():
                            state["ref_article"] = ""
                        print(f"   🔧 [CREER_FOURNISSEUR] Nom extrait: '{nom_extrait}' → Code: '{state['code_client']}'")
                if not state.get("code_client") and state.get("ref_article"):
                    nom_secours = state["ref_article"]
                    state["nom_client_brut"] = nom_secours
                    state["code_client"] = _generer_code_fournisseur(nom_secours)
                    state["ref_article"] = ""
                    print(f"   🔧 [CREER_FOURNISSEUR] Nom depuis ref_article: '{nom_secours}' → Code: '{state['code_client']}'")
                if not state.get("code_client"):
                    state["ambigue"] = True

        if action_preclass == "REGLEMENT":
            # FIX BUG1: \d{3,} au lieu de [A-Z0-9]{3,} pour éviter de capturer "facture" (FA+cture)
            pieces_trouvees = re.findall(r"\b((?:FA|BL|BC|BF|FF|AV|BR|AF)[A-Z0-9]{2,}\d{2,})\b", question, re.IGNORECASE)
            if pieces_trouvees:
                state["num_piece"]   = pieces_trouvees[-1].upper()  # dernier match = le vrai numéro
                state["code_client"] = ""  # pas un client
            # Extraire le mode de paiement
            m_mode = re.search(r"(?:par\s+)(chèque|cheque|virement|espèces?|especes?|cb|carte)", question, re.IGNORECASE)
            if m_mode:
                state["mode_paiement"] = m_mode.group(1).capitalize()
            # Ambiguïté seulement si num_piece vraiment absent
            if not state.get("num_piece"):
                state["ambigue"] = True

        if action_preclass == "CREER_AVOIR" and not state.get("num_piece"):
            state["ambigue"] = True

        elapsed = time.perf_counter() - t0
        print(
            f"   Action    : {action_preclass} {'[AMBIGUE]' if state.get('ambigue') else ''}\n"
            f"   Confiance : 1.00\n"
            f"   Client: {state.get('code_client') or state.get('nom_client_brut') or '—'} | "
            f"Article: {state.get('ref_article') or '—'} | "
            f"Qté: {state.get('quantite', 0)} | Pièce: {state.get('num_piece') or '—'}\n"
            f"   ⏱️  {elapsed:.2f}s"
        )
        return state

    # ══════════════════════════════════════════════════════════════
    # ÉTAPE 1 — GLiNER NER
    # ══════════════════════════════════════════════════════════════
    entites_ner = _ner_extraire_entites(state["demande_brute"])
    for _champ in ("client", "article"):
        _v = entites_ner.get(_champ, "")
        if _v.lower().strip() in _MOTS_GENERIQUES_NER:
            print(f"   ⚠️  [GLiNER-Fix] '{_champ}'='{_v}' ignoré")
            entites_ner.pop(_champ, None)
    if entites_ner.get("article", "").lower() in {
        "encours", "crédit", "solde", "statut", "fiche",
        "info", "rupture", "stock", "marge", "rentabilité",
    }:
        print(f"   🧹 [GLiNER] Faux positif article supprimé : '{entites_ner['article']}'")
        entites_ner.pop("article", None)

    if entites_ner:
        print(f"   🏷️  [GLiNER] Entités : {entites_ner}")

    # ══════════════════════════════════════════════════════════════
    # ÉTAPE 2 — LLM Classification
    # ══════════════════════════════════════════════════════════════
    prompt_unique = f"""Tu es un classificateur ERP Sage 100.
Message : "{state['demande_brute']}"

ÉTAPE 1 — Intention :
- ERP        → gestion commerciale, clients, factures, stocks, commandes, CA, fabrication
- AIDE       → l'utilisateur demande tes capacités
- HORS_SUJET → hors ERP

ÉTAPE 2 — Action (si ERP) :
LISTE_CLIENTS | TOP_CLIENTS | LISTE_ARTICLES | PALMARES_ARTICLES | CA_GLOBAL
CLIENTS_BAISSE | FACTURES_NON_REGLEES | FACTURES_NON_REGLEES_FOURN | TOUTES_FACTURES_CLIENT | VERIFIER_STOCK
STATUT_CLIENT | FICHE_CLIENT | DOCS_PERIODE | RENTABILITE | SAISONNALITE | DSO | RFM
OFFRE_PRIX_EXCEL | DECLARATION_EXCEL | BALANCE_AGEE_EXCEL | DASHBOARD_EXCEL
CREER_CLIENT | MODIFIER_STATUT | GENERER_DOC | TRANSFORMER_DOC | CREER_AVOIR
REGLEMENT | MOUVEMENT_STOCK | PROPOSITION_ACHAT | WORKFLOW_COMMANDE
RECHERCHE_PROCEDURE | RECOMMANDATION | SEUIL_STOCK | LISTE_PROCEDURES
NL2SQL_LIBRE | AMBIGUE

RÈGLES IMPORTANTES :
- "encours client" → NL2SQL_LIBRE (jamais RFM)
- "clients inactifs" → NL2SQL_LIBRE
- "clients bloqués" → NL2SQL_LIBRE
- "liste BL client" → NL2SQL_LIBRE
- "liste clients" générique → LISTE_CLIENTS
- "transformer OF en BF" → TRANSFORMER_DOC
- "OF" seul → GENERER_DOC type_doc=OF
- "BF" seul → GENERER_DOC type_doc=BF
- Si client absent → écris INCONNU

FORMAT STRICT (une clé par ligne) :
intention:VALEUR
action:VALEUR
confiance:0.0-1.0
client:NOM_COMPLET_OU_CODE_OU_INCONNU
article:VALEUR_OU_INCONNU
quantite:VALEUR_OU_INCONNU
piece:VALEUR_OU_INCONNU
type_doc:VALEUR_OU_INCONNU
date_debut:VALEUR_OU_INCONNU
date_fin:VALEUR_OU_INCONNU"""

    if ENABLE_MEM0:
        r1, mem_contexte = await asyncio.gather(
            _invoke_llm(prompt_unique, use_smart=False),
            _mem0_rechercher(state["demande_brute"]),
        )
    else:
        r1 = await _invoke_llm(prompt_unique, use_smart=False)
        mem_contexte = ""

    state["mem0_contexte"] = mem_contexte
    lignes = r1.strip().split("\n")

    def _val(key: str, default: str = "INCONNU") -> str:
        for line in lignes:
            clean = line.strip().replace("*", "").replace("`", "")
            if clean.lower().startswith(f"{key}:"):
                v = clean.split(":", 1)[1].strip()
                if key == "client":
                    return v if v else default
                return v.split()[0] if v.split() else default
        return default

    intention = _val("intention", "ERP").strip().upper().split()[0]
    if intention not in ("ERP", "AIDE", "HORS_SUJET"):
        intention = "ERP"
    state["intention"] = intention
    if state["intention"] != "ERP":
        return state

    try:
        state["score_confiance"] = max(0.0, min(1.0, float(_val("confiance", "0.8"))))
    except ValueError:
        state["score_confiance"] = 0.8

    raw_action  = _val("action", "NL2SQL_LIBRE").upper().strip().split()[0]
    llm_client  = _clean(_val("client"))
    llm_article = _clean(_val("article"))
    llm_piece   = _clean(_val("piece"))
    llm_type    = _clean(_val("type_doc"))

    db = state["demande_brute"]
    n  = db.lower()

    # ── Extraction client ──────────────────────────────────────────
    _regex_code, _nom_regex = _extraire_code_ou_nom_depuis_texte(db)
    _ner_client = entites_ner.get("client", "")
    _llm_client_clean = ""
    if llm_client and llm_client.upper() not in _LLM_PLACEHOLDERS:
        if _est_nom_valide(llm_client) or re.match(r"^[A-Z]{2,6}\d{2,}$", llm_client, re.IGNORECASE):
            _llm_client_clean = llm_client

    if _regex_code:
        state["code_client"]     = _regex_code
        state["nom_client_brut"] = ""
        print(f"   ✅ [Client] Code regex : '{_regex_code}'")
    elif _ner_client:
        state["nom_client_brut"] = _ner_client
        state["code_client"]     = ""
        print(f"   ✅ [Client] NER : '{_ner_client}'")
    elif _nom_regex:
        state["nom_client_brut"] = _nom_regex
        state["code_client"]     = ""
        print(f"   ✅ [Client] Nom regex : '{_nom_regex}'")
    elif _llm_client_clean:
        state["nom_client_brut"] = _llm_client_clean
        state["code_client"]     = ""
        print(f"   ✅ [Client] LLM : '{_llm_client_clean}'")
    else:
        if state.get("nom_client_brut"):
            print(f"   🔗 [Client] Hérité session : '{state['nom_client_brut']}'")

    if state.get("nom_client_brut") and not state.get("code_client"):
        code_trouve = await _rechercher_client_par_nom(state["nom_client_brut"])
        if code_trouve:
            state["code_client"] = code_trouve

    if (state.get("code_client") and state.get("ref_article")
            and state["code_client"].upper() == state.get("ref_article", "").upper()):
        print("   ⚠️  [Anti-Hallucination] Client == Article → client vidé")
        state["code_client"] = ""

    _CODES_IGNORES = {
        "client","tiers","societe","société","entreprise",
        "le","la","les","un","une","des","pour","avec",
    }
    if state.get("code_client", "").lower() in _CODES_IGNORES:
        state["code_client"] = ""

    # FIX 1 : extraction article insensible à la casse
    _regex_article = ""
    for mot in re.findall(r"\b([A-Za-z][A-Za-z0-9\-]{2,})\b", db):
        mot_upper = mot.upper()
        if (mot_upper not in _EXCL_ARTICLE
                and not mot_upper.startswith("CLI")
                and mot_upper != state.get("code_client", "").upper()):
            _regex_article = mot_upper
            print(f"   🔎 [Regex] ref_article : {_regex_article}")
            break

    state["ref_article"] = entites_ner.get("article") or _regex_article or llm_article
    if state["ref_article"].upper() in _TYPES_DOC_INVALIDES_COMME_ARTICLE:
        state["ref_article"] = ""

    # ── Autres champs ──────────────────────────────────────────────
    state["num_piece"]     = entites_ner.get("piece")      or _clean(llm_piece)
    state["type_doc"]      = entites_ner.get("type_doc")   or _clean(llm_type)
    state["date_debut"]    = entites_ner.get("date_debut") or _clean(_val("date_debut"))
    state["date_fin"]      = entites_ner.get("date_fin")   or _clean(_val("date_fin"))
    state["mode_paiement"] = _val("mode_paiement", "Virement")

    # ── Quantité ──────────────────────────────────────────────────
    quantite_explicite = False
    qr = entites_ner.get("quantite") or _clean(_val("quantite"))
    if qr:
        try:
            state["quantite"] = float(re.sub(r"[^\d.]", "", qr) or "0")
            if state["quantite"] > 0:
                quantite_explicite = True
        except ValueError:
            state["quantite"] = 0.0

    if not quantite_explicite:
        m_q = (
            re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:pièces?|pieces?|unités?|u\.?\b)", db, re.IGNORECASE)
            or re.search(r"\bde\s+(\d+(?:[.,]\d+)?)\b", db, re.IGNORECASE)
            or re.search(r"\b(\d+)\s+(?:pièces?|pieces?|unités?)", db, re.IGNORECASE)
        )
        if m_q:
            state["quantite"] = float(m_q.group(1).replace(",", "."))
            quantite_explicite = True
            print(f"   🔎 [Regex] quantite : {state['quantite']}")

    # ── Nettoyage num_piece ────────────────────────────────────────
    if state["num_piece"] and not re.match(r"^[A-Z]{1,4}\d{3,}$", state["num_piece"]):
        if (re.search(r"[A-Za-z]", state["num_piece"])
                and not state["ref_article"]
                and state["num_piece"].upper() not in _TYPES_DOC_INVALIDES_COMME_ARTICLE):
            state["ref_article"] = state["num_piece"]
        state["num_piece"] = ""

    # ── Détection type_doc ─────────────────────────────────────────
    if not state["type_doc"]:
        _TDM = {
            r"facture|facturer":                                  "FACTURE",
            r"bl achat|bon de r[eé]ception|r[eé]ception fournisseur": "BL_ACHAT",
            r"bon de livraison|\bbl\b":                           "BL",
            r"bon de commande|\bbc\b":                            "BC",
            r"bon de fabrication|\bbf\b":                         "BF",
            r"ordre de fabrication|\bof\b":                       "OF",
        }
        for pattern, doc_type in _TDM.items():
            if re.search(pattern, n):
                state["type_doc"] = doc_type
                break

    # ── AIDE ──────────────────────────────────────────────────────
    _MOTS_AIDE = {"aide", "help", "que sais-tu", "que peux-tu", "tes capacités", "fonctionnalités"}
    if any(w in n for w in _MOTS_AIDE) and not any(
        w in n for w in ("client", "facture", "article", "stock", "liste", "ca ", "fournisseur")
    ):
        state["intention"] = "AIDE"
        return state

    # ══════════════════════════════════════════════════════════════
    # ÉTAPE 3 — Overrides sémantiques
    # ══════════════════════════════════════════════════════════════
    if re.search(r"transform", n) and re.search(r"\bof\b.{0,20}\bbf\b|\bbl\b.{0,20}facture|\bbc\b.{0,20}\bbl\b", n):
        raw_action = "TRANSFORMER_DOC"
        # FIX 3 appliquer aussi depuis le path LLM
        m_piece = re.search(r"\b((?:OF|BL|BC|FA|BF)[A-Z0-9]{3,})\b", db, re.IGNORECASE)
        if m_piece and not state.get("num_piece"):
            state["num_piece"] = m_piece.group(1).upper()
    elif re.search(r"bl\s+achat|bon\s+de\s+r[eé]ception|r[eé]ception\s+fournisseur", n):
        raw_action = "GENERER_DOC"; state["type_doc"] = "BL_ACHAT"
    elif re.search(r"\bbl\b", n) or "bon de livraison" in n:
        raw_action = "GENERER_DOC"; state["type_doc"] = "BL"
    elif re.search(r"\bbc\b", n) or "bon de commande" in n:
        raw_action = "GENERER_DOC"; state["type_doc"] = "BC"
    elif (re.search(r"\bbf\b", n) or "bon de fabrication" in n) and "livraison" not in n:
        raw_action = "GENERER_DOC"; state["type_doc"] = "BF"
    elif re.search(r"\bof\b", n) or "ordre de fabrication" in n:
        raw_action = "GENERER_DOC"; state["type_doc"] = "OF"
    elif any(w in n for w in ("facture","factures")) and any(w in n for w in ("impayé","impayés","non réglé","non reglé","non payé","en attente","souffrance")):
        raw_action = "FACTURES_NON_REGLEES"
    elif any(w in n for w in ("facture","factures")) and any(w in n for w in ("client","tiers","pour","de","ses","son","sa","leur")):
        raw_action = "TOUTES_FACTURES_CLIENT"
    elif any(w in n for w in ("facture","factures","bl")) and any(w in n for w in ("période","periode","mois","année","annee","entre","du","au")):
        raw_action = "DOCS_PERIODE"
    elif any(w in n for w in ("factur","facturer")) and any(w in n for w in ("créer","générer","faire","établir","émettre")):
        raw_action = "GENERER_DOC"
        if not state["type_doc"]:
            state["type_doc"] = "FACTURE"
    elif "top" in n and "client" in n:
        raw_action = "TOP_CLIENTS"
    elif any(w in n for w in ("information","informations","info","renseign","fiche","détail","detail","profil","qui est","présente","dis moi","connais","connaître","tout sur","données sur","données du")) and any(w in n for w in ("client","tiers","société","entreprise")):
        raw_action = "FICHE_CLIENT"
    elif any(w in n for w in ("statut","status","validité","validite")) and any(w in n for w in ("client","tiers")):
        raw_action = "STATUT_CLIENT"
    elif "encours" in n and "client" in n:
        raw_action = "NL2SQL_LIBRE"
    elif "client" in n and any(w in n for w in ("bloqu","solvab","risque")):
        raw_action = "RFM"
    elif any(w in n for w in ("liste","tous","toutes","affiche","montre","donne")) and "client" in n and "facture" not in n and "bl" not in n and "livraison" not in n:
        raw_action = "LISTE_CLIENTS"
    elif any(w in n for w in ("produit","article","référence","réf")) and any(w in n for w in ("moins","plus","palmares","mieux","vendu")):
        raw_action = "PALMARES_ARTICLES"
    elif any(w in n for w in ("liste","tous","toutes","affiche","montre","donne")) and any(w in n for w in ("produit","article","référence","réf","catalogue")):
        raw_action = "LISTE_ARTICLES"
    elif "stock" in n and any(w in n for w in ("rentre","sort","mouvement","ajust")):
        raw_action = "MOUVEMENT_STOCK"
    elif "stock" in n:
        raw_action = "VERIFIER_STOCK"
    elif "dashboard" in n or "kpi" in n:
        raw_action = "DASHBOARD_EXCEL"
    elif "avoir" in n and (state["num_piece"] or re.search(r"\b[A-Z]{2,4}\d{3,}\b", db)):
        raw_action = "CREER_AVOIR"
    elif any(w in n for w in ("regle","payer","solder","règlement","régler")):
        raw_action = "REGLEMENT"
    elif "commande" in n and any(w in n for w in ("créer","passer","enregistr","nouveau")):
        raw_action = "WORKFLOW_COMMANDE"
    elif any(w in n for w in ("ca ","chiffre d'affaires","chiffre d affaires")) and "client" not in n:
        raw_action = "CA_GLOBAL"
    elif any(w in n for w in ("dso","délai","delai","retard de paiement")):
        raw_action = "DSO"
    elif any(w in n for w in ("rentabilit","marge","profit")):
        raw_action = "RENTABILITE"
    elif any(w in n for w in ("saison","mensuel","tendance","évolution","evolution")):
        raw_action = "SAISONNALITE"

    # ══════════════════════════════════════════════════════════════
    # ÉTAPE 4 — Fallback agressif vers NL2SQL_LIBRE
    # ══════════════════════════════════════════════════════════════
    toutes = (
        ACTIONS_LECTURE | ACTIONS_NL2SQL | ACTIONS_EXPORT
        | ACTIONS_ECRITURE | ACTIONS_WORKFLOW | ACTIONS_KB
        | ACTIONS_HUB | {"AMBIGUE"}
    )

    if not raw_action or raw_action not in toutes:
        print(f"   ⚠️  [Classifier] '{raw_action}' invalide → NL2SQL_LIBRE")
        raw_action = "NL2SQL_LIBRE"

    state["action"] = raw_action

    # ── Type doc ──────────────────────────────────────────────────
    if state["type_doc"]:
        hub_doc = await _hub_resoudre_type_doc(state["type_doc"])
        state["type_doc_code"] = hub_doc.get("DO_Type", 0)

    # ── GENERER_DOC : complétion depuis session ────────────────────
    if state["action"] == "GENERER_DOC":
        if not state["ref_article"] and state["dernier_ref_article"]:
            state["ref_article"] = state["dernier_ref_article"]
        if not quantite_explicite and state["dernier_quantite"] > 0:
            state["quantite"] = state["dernier_quantite"]
        if state["type_doc"] == "BF" and not state["num_piece"]:
            m_of = re.search(r"(?:ordre de fabrication|\bof\b)\s*(?:n[°o]?\s*)?0*(\d{3,})", n)
            if m_of:
                state["num_piece"] = f"OF{int(m_of.group(1)):05d}"
            elif re.match(r"^OF\d+$", state.get("dernier_num_piece", ""), re.IGNORECASE):
                state["num_piece"] = state["dernier_num_piece"]
                print(f"   🔗 [BF] Lié au dernier OF : {state['num_piece']}")

    # ── Fabrication : code client PROD-INT ─────────────────────────
    if state["type_doc"] in TYPES_DOC_FABRICATION:
        ca = state.get("code_client", "")
        if not ca:
            state["code_client"] = "PROD-INT"
        elif ca.upper() == state.get("ref_article", "").upper():
            state["code_client"] = "PROD-INT"
        elif not re.match(r"^([A-Z]{2,6}\d+|PROD)", ca, re.IGNORECASE):
            state["code_client"] = "PROD-INT"

    # ── Références contextuelles ───────────────────────────────────
    if state["action"] in ("TRANSFORMER_DOC", "CREER_AVOIR", "REGLEMENT") and not state["num_piece"]:
        ref_det = any(m in n for m in MOTS_REFERENCE_DOCUMENT)
        if state["dernier_num_piece"] and ref_det:
            state["num_piece"]   = state["dernier_num_piece"]
            state["type_doc"]    = state["type_doc"] or state["dernier_type_doc"]
            state["code_client"] = state["code_client"] or state["dernier_code_client"]
            state["ref_article"] = state["ref_article"] or state["dernier_ref_article"]

    # ── Ambiguïtés ─────────────────────────────────────────────────
    _a_client = bool(state.get("code_client") or state.get("nom_client_brut"))
    if state["action"] in ("VERIFIER_STOCK","FICHE_CLIENT","STATUT_CLIENT") and not (_a_client or state["ref_article"]):
        state["ambigue"] = True
    if state["action"] in ("TOUTES_FACTURES_CLIENT","FICHE_CLIENT","STATUT_CLIENT") and not _a_client:
        state["ambigue"] = True
    if state["action"] == "GENERER_DOC":
        type_d = (state["type_doc"] or "").upper()
        if type_d not in TYPES_DOC_FABRICATION and not _a_client:
            state["ambigue"] = True
        if not state["ref_article"]:
            state["ambigue"] = True
        if state["quantite"] <= 0.0:
            state["ambigue"] = True
    if state["action"] in ("TRANSFORMER_DOC","CREER_AVOIR","REGLEMENT") and not state["num_piece"]:
        state["ambigue"] = True
    if state["score_confiance"] < SEUIL_CONFIANCE and not state["ambigue"]:
        state["ambigue"] = True

    elapsed = time.perf_counter() - t0
    print(
        f"   Action    : {state['action']} {'[AMBIGUE]' if state['ambigue'] else ''}\n"
        f"   Confiance : {state['score_confiance']:.2f}\n"
        f"   Client: {state.get('code_client') or state.get('nom_client_brut') or '—'} | "
        f"Article: {state['ref_article'] or '—'} | "
        f"Qté: {state['quantite']} | Pièce: {state['num_piece'] or '—'}\n"
        f"   ⏱️  {elapsed:.2f}s"
    )
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD PLANNER
# ─────────────────────────────────────────────────────────────────────
async def noeud_planner(state: CopilotState) -> CopilotState:
    print("🧩 [Planner] Construction du plan...")
    if state["action"] in (ACTIONS_LECTURE | ACTIONS_EXPORT | ACTIONS_KB | ACTIONS_NL2SQL):
        state["plan_execution"] = [{"step": 1, "action": state["action"], "reason": "direct"}]
        return state
    prompt = (
        f'Tu es un planner ERP Sage 100.\nDemande : "{state["demande_brute"]}"\n'
        f'Réponds UNIQUEMENT avec un JSON : [{{"step":1,"action":"...","reason":"..."}}]'
    )
    try:
        r = await asyncio.wait_for(_invoke_llm(prompt, use_smart=True), timeout=PLANNER_TIMEOUT)
        plan = json.loads(r.replace("```json", "").replace("```", "").strip())
        state["plan_execution"] = (
            plan if isinstance(plan, list) and plan
            else [{"step": 1, "action": state["action"], "reason": "fallback"}]
        )
    except Exception:
        state["plan_execution"] = [{"step": 1, "action": state["action"], "reason": "fallback"}]
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUDS SIMPLES
# ─────────────────────────────────────────────────────────────────────
async def noeud_hors_sujet(state: CopilotState) -> CopilotState:
    state["reponse_finale"] = await _invoke_llm(
        f'Assistant ERP Sage 100.\nMessage hors-sujet : "{state["demande_brute"]}"\n'
        f'Réponds en 1-2 phrases naturelles en rappelant ton rôle ERP.',
        use_smart=False,
    )
    return state


async def noeud_aide(state: CopilotState) -> CopilotState:
    state["reponse_finale"] = await _invoke_llm(
        f'Assistant ERP Sage 100.\nCapacités demandées : "{state["demande_brute"]}"\n'
        f'Réponds clairement :\n{CAPACITES_SYSTEME}\nInvite à formuler une demande.',
        use_smart=False,
    )
    return state


async def noeud_clarification(state: CopilotState) -> CopilotState:
    state["reponse_finale"] = await _invoke_llm(
        f'Assistant ERP Sage 100.\nDemande ambiguë : "{state["demande_brute"]}"\n'
        f'Pose UNE question courte pour clarifier. Donne 2-3 exemples.',
        use_smart=False,
    )
    return state




def _generer_code_client(nom: str) -> str:
    """
    PATCH B-1 : Génère le prochain code client incrémental disponible dans F_COMPTET :
      CLI001 → CLI002 → CLI003 …
    Consulte la DB pour trouver le dernier numéro CLI existant.
    Le nom n'est utilisé que comme fallback si la DB est inaccessible.
    """
    import sqlite3 as _sqlite3
    import re as _re
    try:
        conn = _sqlite3.connect(str(_db_path))
        rows = conn.execute(
            "SELECT CT_Num FROM F_COMPTET WHERE CT_Num LIKE 'CLI%' ORDER BY CT_Num"
        ).fetchall()
        conn.close()
        nums = []
        for (code,) in rows:
            m = _re.match(r"CLI(\d+)$", code, _re.IGNORECASE)
            if m:
                nums.append(int(m.group(1)))
        prochain = (max(nums) + 1) if nums else 1
        return f"CLI{prochain:03d}"
    except Exception:
        import unicodedata
        nom_clean = unicodedata.normalize("NFD", nom)
        nom_clean = "".join(c for c in nom_clean if unicodedata.category(c) != "Mn")
        import re as _re2
        nom_clean = _re2.sub(r"[^A-Za-z0-9]", "", nom_clean).upper()
        return f"CLI{nom_clean[:5]}" if nom_clean else "CLI001"


def _generer_code_fournisseur(nom: str) -> str:
    """
    Génère le prochain code fournisseur incrémental disponible dans F_COMPTET :
      FOUR001 → FOUR002 → FOUR003 …
    Consulte la DB pour trouver le dernier numéro FOUR existant.
    """
    import sqlite3 as _sqlite3
    import re as _re
    try:
        conn = _sqlite3.connect(str(_db_path))
        rows = conn.execute(
            "SELECT CT_Num FROM F_COMPTET WHERE CT_Num LIKE 'FOUR%' ORDER BY CT_Num"
        ).fetchall()
        conn.close()
        nums = []
        for (code,) in rows:
            m = _re.match(r"FOUR(\d+)$", code, _re.IGNORECASE)
            if m:
                nums.append(int(m.group(1)))
        prochain = (max(nums) + 1) if nums else 1
        return f"FOUR{prochain:03d}"
    except Exception:
        import unicodedata
        nom_clean = unicodedata.normalize("NFD", nom)
        nom_clean = "".join(c for c in nom_clean if unicodedata.category(c) != "Mn")
        import re as _re2
        nom_clean = _re2.sub(r"[^A-Za-z0-9]", "", nom_clean).upper()
        return f"FOUR{nom_clean[:4]}" if nom_clean else "FOUR001"


# ─────────────────────────────────────────────────────────────────────
# NŒUD CONFIRMATION
# ─────────────────────────────────────────────────────────────────────
async def noeud_confirmation(state: CopilotState) -> CopilotState:
    act = state["action"]
    print("\n🔧 [Hub] Validation...")

    if act in ("GENERER_DOC", "MOUVEMENT_STOCK") and state["quantite"] <= 0.0:
        state["validation_ok"] = False
        state["reponse_brute"] = "🚫 Validation refusée : Quantité manquante ou invalide (doit être > 0)"
        return state

    type_d = (state.get("type_doc") or "").upper()
    if type_d in TYPES_DOC_FABRICATION and state.get("code_client") == "PROD-INT":
        champs_requis = ["ref_article"]
    else:
        champs_requis_map = {
            "CREER_CLIENT":      ["code_client"],
            "CREER_FOURNISSEUR": [],
            "MODIFIER_STATUT":   ["code_client"],
            "GENERER_DOC":       ["ref_article"],
            "TRANSFORMER_DOC":   ["num_piece", "type_doc"],
            "CREER_AVOIR":       ["num_piece"],
            "REGLEMENT":         ["num_piece"],
            "MOUVEMENT_STOCK":   ["ref_article"],
            "PROPOSITION_ACHAT": ["ref_article"],
        }
        champs_requis = champs_requis_map.get(act, [])

    # Pour BL_ACHAT et REGLEMENT, le code_client serait un fournisseur/n°pièce
    # → ne pas l'envoyer au Hub qui ne vérifie que les clients de vente
    _code_pour_hub = state["code_client"]
    if type_d == "BL_ACHAT" or act in ("REGLEMENT", "CREER_FOURNISSEUR"):
        _code_pour_hub = ""  # skip validation client/fournisseur inexistant

    hub_result = await _hub_valider_demande("ECRITURE", {
        "action": act, "code_client": _code_pour_hub,
        "ref_article": state["ref_article"], "quantite": state["quantite"],
        "num_piece": state["num_piece"], "type_doc": state["type_doc"],
        "champs_requis": champs_requis,
    })
    state["hub_validation"] = json.dumps(hub_result, ensure_ascii=False)

    if not hub_result.get("valide", True):
        state["validation_ok"] = False
        state["reponse_brute"] = f"🚫 Validation refusée : {hub_result.get('message')}"
        return state

    detail = ""
    if state["code_client"]: detail += f" | Client: {state['code_client']}"
    if state["ref_article"]: detail += f" | Article: {state['ref_article']}"
    if state["quantite"]:    detail += f" | Qté: {state['quantite']}"
    if state["num_piece"]:   detail += f" | Pièce: {state['num_piece']}"
    if state["type_doc"]:    detail += f" | Type: {state['type_doc']}"

    print(f"\n⚠️  [Sécurité] [{act}]{detail}")
    rep = await _input("❓ Confirmez-vous ? [Y/n] : ")
    state["validation_ok"] = rep.strip().lower() not in ("n", "no", "non")
    if not state["validation_ok"]:
        state["reponse_brute"] = "🛑 Action annulée."
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD LECTURE
# ─────────────────────────────────────────────────────────────────────
async def noeud_lecture(state: CopilotState) -> CopilotState:
    print("📊 [Agent Lecture] Interrogation Sage...")
    act = state["action"]

    _actions_client_requis = {"FICHE_CLIENT","STATUT_CLIENT","TOUTES_FACTURES_CLIENT","FACTURES_NON_REGLEES","FACTURES_NON_REGLEES_FOURN"}
    if act in _actions_client_requis and not state.get("code_client"):
        nom_candidat = state.get("nom_client_brut") or state.get("dernier_code_client") or ""
        if nom_candidat:
            code = await _rechercher_client_par_nom(nom_candidat)
            if code:
                state["code_client"] = code
            else:
                state["code_client"] = nom_candidat

    try:
        tool_map = {
            "TOP_CLIENTS":             ("nl2sql", "analyser_top_clients_ca",       {}),
            "LISTE_CLIENTS":           ("nl2sql", "lister_clients_actifs",          {}),
            "LISTE_ARTICLES":          ("nl2sql", "lister_articles_catalogue",      {}),
            "PALMARES_ARTICLES":       ("nl2sql", "analyser_palmares_articles",     {}),
            "CA_GLOBAL":               ("nl2sql", "calculer_ca_global_periode",     {}),
            "CLIENTS_BAISSE":          ("nl2sql", "detecter_clients_baisse_ca",     {}),
            "FACTURES_NON_REGLEES":    ("nl2sql", "lister_factures_impayees",       {"code_client": state.get("code_client", "")}),
            "TOUTES_FACTURES_CLIENT":  ("nl2sql", "lister_toutes_factures_client",  {"code_client": state.get("code_client", "")}),
            "VERIFIER_STOCK":          ("nl2sql", "verifier_stock_article",         {"ref_article": state.get("ref_article", "")}),
            "FICHE_CLIENT":            ("nl2sql", "rechercher_fiche_client",        {"code_client": state.get("code_client", "")}),
            "STATUT_CLIENT":           ("nl2sql", "verifier_statut_client",         {"code_client": state.get("code_client", "")}),
            "DOCS_PERIODE":            ("nl2sql", "lister_documents_par_periode",   {"date_debut": state.get("date_debut",""), "date_fin": state.get("date_fin","")}),
            "RENTABILITE":             ("nl2sql", "analyser_rentabilite_clients",   {}),
            "SAISONNALITE":            ("nl2sql", "analyser_saisonnalite_ventes",   {}),
            "DSO":                     ("nl2sql", "calculer_dso_clients",           {"code_client": state.get("code_client","")}),
            "RFM":                     ("nl2sql", "analyser_rfm_clients",           {"code_client": state.get("code_client","")}),
            "OFFRE_PRIX_EXCEL":        ("nl2sql", "exporter_offre_prix_excel",      {"code_client": state.get("code_client","")}),
            "DECLARATION_EXCEL":       ("nl2sql", "exporter_declaration_fiscale_excel", {}),
            "BALANCE_AGEE_EXCEL":      ("nl2sql", "exporter_balance_agee_excel",    {}),
            "DASHBOARD_EXCEL":         ("nl2sql", "exporter_dashboard_kpi_excel",   {}),
            "LISTE_FOURNISSEURS": ("nl2sql", "executer_sql_vanna", {
                "sql": "SELECT CT_Num, CT_Intitule, CT_Encours, CT_EncoursMax, CT_Validite FROM F_COMPTET WHERE CT_Type=1 ORDER BY CT_Intitule",
                "description": "Liste des fournisseurs",
            }),
            "TOP_FOURNISSEURS": ("nl2sql", "executer_sql_vanna", {
                "sql": (
                    "SELECT c.CT_Num, c.CT_Intitule, "
                    "COUNT(DISTINCT e.DO_Piece) AS nb_commandes, "
                    "COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS volume_achat "
                    "FROM F_COMPTET c "
                    "LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.CT_Num AND e.DO_Type=6 AND e.DO_Domaine=1 "
                    "LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
                    "WHERE c.CT_Type=1 "
                    "GROUP BY c.CT_Num ORDER BY volume_achat DESC LIMIT 10"
                ),
                "description": "Top fournisseurs par volume d'achat",
            }),
            "FICHE_FOURNISSEUR": ("nl2sql", "executer_sql_vanna", {
                "sql": (
                    f"SELECT CT_Num, CT_Intitule, CT_Encours, CT_EncoursMax, CT_Validite "
                    f"FROM F_COMPTET "
                    f"WHERE CT_Type=1 AND (CT_Num='{state.get('code_client','')}' "
                    f"OR UPPER(CT_Intitule) LIKE UPPER('%{state.get('code_client','')}%')) LIMIT 1"
                ),
                "description": f"Fiche fournisseur {state.get('code_client','')}",
            }),
            "FACTURES_NON_REGLEES_FOURN": ("nl2sql", "lister_factures_fournisseurs_non_reglees", {
                "code_fournisseur": state.get("code_client", ""),
            }),
        }
        if act in tool_map:
            server, tool, args = tool_map[act]
            state["reponse_brute"] = await mcp_pool.call(server, tool, args)
        else:
            state["reponse_brute"] = f"__INCONNU__:{act}"
    except Exception as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD NL2SQL
# ─────────────────────────────────────────────────────────────────────
async def noeud_nl2sql_libre(state: CopilotState) -> CopilotState:
    print("🤖 [Agent NL2SQL]...")

    if ENABLE_VANNA and _vanna_client is not None:
        sql, score = await asyncio.to_thread(_vanna_generer_sql, state["demande_brute"])
        if sql and score >= 0.5:
            code = state.get("code_client", "")
            # FIX A : ne pas injecter le filtre CT_Num si :
            #   - code fournisseur (FOUR*, F0*) : Vanna gère déjà DO_Domaine=1
            #   - SQL contient déjà DO_Domaine=1 (requête achat)
            #   - SQL contient déjà ce code
            _est_fournisseur = bool(re.match(r"^F(OUR|0)\w*", code, re.IGNORECASE)) if code else False
            _sql_est_achat   = "DO_DOMAINE=1" in sql.upper().replace(" ", "") or "DO_DOMAINE = 1" in sql.upper()
            if (sql and code
                    and code.upper() not in ("PROD-INT", "")
                    and f"'{code}'" not in sql.upper()
                    and not _est_fournisseur
                    and not _sql_est_achat):
                sql = re.sub(
                    r"(WHERE\s+)",
                    rf"\1e.CT_Num='{code}' AND ",
                    sql, count=1, flags=re.IGNORECASE
                )
                print(f"   🔧 [NL2SQL Fix] Filtre CT_Num='{code}' injecté dans le SQL")
            elif _est_fournisseur:
                print(f"   🔧 [NL2SQL Fix] Code fournisseur '{code}' → injection filtre ignorée (Vanna gère)")
                # FIX TIERS : Vanna peut recycler un exemple avec un code différent
                # → remplacer le code tiers dans le SQL par le code réel détecté
                if code:
                    import re as _re2
                    _code_upper = code.upper()
                    sql_fixed = _re2.sub(
                        r"'([A-Z]{2,5}\d{2,6})'",
                        lambda m: f"'{_code_upper}'" if m.group(1).upper() != _code_upper else m.group(0),
                        sql, flags=_re2.IGNORECASE
                    )
                    if sql_fixed != sql:
                        print(f"   🔧 [NL2SQL Fix] Code tiers corrigé → '{_code_upper}' dans le SQL")
                        sql = sql_fixed

            print(f"   ✨ [Vanna] SQL généré (confiance {score:.0%}) : {sql[:80]}...")
            try:
                reponse = await mcp_pool.call(
                    "nl2sql", "executer_sql_vanna",
                    {"sql": sql, "description": state["demande_brute"]},
                )
                state["reponse_brute"] = reponse
                if reponse and "__ERREUR__" not in reponse:
                    asyncio.create_task(
                        asyncio.to_thread(_vanna_entrainer, state["demande_brute"], sql)
                    )
                return state
            except Exception as e:
                print(f"   ⚠️  [Vanna] exécution : {_safe_str(e)}")
        else:
            if ENABLE_VANNA:
                print(f"   ℹ️  [Vanna] Score insuffisant ({score:.0%}) → fallback patterns")
    elif ENABLE_VANNA and _vanna_client is None:
        print("   ⚠️  [Vanna] Non initialisé → fallback patterns")

    # FIX BUGB : enrichir la question avec le code client/fournisseur
    # pour que le fallback MCP génère un SQL filtré
    _question_enrichie = state["demande_brute"]
    _code_injecte = state.get("code_client", "")
    # PATCH A-3 : n'injecter le code client QUE si la demande mentionne explicitement
    # un client/fournisseur précis — ne pas injecter pour des requêtes globales
    _demande_lower = state["demande_brute"].lower()
    _est_requete_globale = not any(w in _demande_lower for w in (
        "client", "fournisseur", "tiers", "pour", "de", "du",
        "facture de", "bl de", "commande de",
    )) or any(w in _demande_lower for w in (
        "toutes", "tous", "liste", "global", "général",
        "factures fournisseur", "factures fournisseurs",
    ))
    # FIX C : ne pas injecter si code fournisseur (FOUR*, F0*)
    _est_code_fournisseur = bool(re.match(r"^F(OUR|0)\w*", _code_injecte, re.IGNORECASE)) if _code_injecte else False

    if (_code_injecte
            and _code_injecte.upper() not in ("PROD-INT", "")
            and not _est_requete_globale
            and not _est_code_fournisseur):
        if _code_injecte.upper() not in _question_enrichie.upper():
            _question_enrichie = f"{_question_enrichie} (code: {_code_injecte})"
            print(f"   🔧 [NL2SQL Fallback] Question enrichie avec code '{_code_injecte}'")
    elif _code_injecte and _est_requete_globale:
        print(f"   🔧 [NL2SQL Fallback] Requête globale → filtre '{_code_injecte}' NON injecté")
    elif _est_code_fournisseur:
        print(f"   🔧 [NL2SQL Fallback] Code fournisseur '{_code_injecte}' → injection ignorée")
    try:
        reponse = await mcp_pool.call(
            "nl2sql", "interpreter_et_analyser_via_sql",
            {"question_metier": _question_enrichie},
        )
        state["reponse_brute"] = reponse
        if (ENABLE_VANNA and _vanna_client is not None
                and reponse and "__ERREUR__" not in reponse):
            m = re.search(r"(SELECT.+?)(?:\n\n|$)", reponse, re.IGNORECASE | re.DOTALL)
            if m:
                asyncio.create_task(
                    asyncio.to_thread(_vanna_entrainer, state["demande_brute"], m.group(1).strip())
                )
    except Exception as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD ÉCRITURE
# ─────────────────────────────────────────────────────────────────────
async def noeud_ecriture(state: CopilotState) -> CopilotState:
    if not state["validation_ok"]:
        return state

    print("⚡ [Agent Écriture]...")
    act    = state["action"]
    type_d = (state.get("type_doc") or "").upper()
    state["suggestion_en_attente"] = {}

    def _traiter_erreur_mcp(data: dict) -> str | None:
        statut = data.get("statut", "")
        if statut not in _STATUTS_ERREUR_MCP:
            return None
        message = data.get("message", "")
        if statut == "CLIENT_NON_TROUVE":
            suggestions = data.get("suggestions", [])
            sugg_txt = ""
            if suggestions:
                sugg_txt = "\n💡 Clients similaires : " + ", ".join(
                    f"{s['CT_Num']} ({s['CT_Intitule']})" for s in suggestions
                )
            return f"❌ Client '{state.get('code_client') or state.get('nom_client_brut')}' introuvable.{sugg_txt}"
        if statut == "CLIENT_BLOQUE":
            return message or "🚫 Client bloqué."
        if statut == "ARTICLE_NON_TROUVE":
            suggestions = data.get("suggestions", [])
            sugg_txt = ""
            if suggestions:
                sugg_txt = "\n💡 Articles similaires : " + ", ".join(
                    f"{s['AR_Ref']} ({s['AR_Design']})" for s in suggestions
                )
            return f"❌ Article '{state['ref_article']}' introuvable.{sugg_txt}"
        if statut == "STOCK_INSUFFISANT":
            return message or (
                f"❌ Stock insuffisant pour '{state['ref_article']}'.\n"
                f"   Dispo : {data.get('stock_dispo', 0)} u | "
                f"Demandé : {data.get('qte_demandee', 0)} u"
            )
        if statut == "COMPOSANTS_INSUFFISANTS":
            return message or "❌ Composants insuffisants pour la fabrication."
        if statut == "NON_TROUVE":
            return f"❌ Document '{state.get('num_piece', '?')}' introuvable."
        if statut == "EXISTE_DEJA":
            return message or "⚠️  Élément déjà existant."
        return message or f"❌ Erreur MCP : statut={statut}"

    try:
        if act == "GENERER_DOC":
            code_client_final = (
                state.get("code_client") or state.get("nom_client_brut", "") or ""
            )

            if type_d == "BL_ACHAT":
                doc = state.get("pending_document", {})
                # FIX BUG2: utiliser _mcp_workflow_bl_achat au lieu de generer_document_sage
                data = await _mcp_workflow_bl_achat(
                    code_fournisseur = doc.get("code_fournisseur", code_client_final),
                    ref_article      = doc.get("ref_article",   state["ref_article"]),
                    quantite         = doc.get("quantite",      state["quantite"]),
                    prix_unitaire    = doc.get("prix_unitaire", 0.0),
                )
                err  = _traiter_erreur_mcp(data)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["num_piece"]     = data.get("DO_Piece", "")
                state["reponse_brute"] = json.dumps(data, ensure_ascii=False)
                state["suggestion_en_attente"] = {
                    "type":        "FACTURE_ACHAT",
                    "description": f"Créer la facture fournisseur pour BL {data.get('DO_Piece', '')}",
                    "params":      {"num_bl": data.get("DO_Piece")},
                }
                state["reponse_finale"] = (
                    data.get("message", "")
                    + "\n\n💡 Tapez **ok** pour créer la facture fournisseur."
                )
                return state

            elif type_d == "BL":
                result = await _mcp_workflow_bl(
                    code_client_final, state["ref_article"], state["quantite"], 0.0,
                )
                err = _traiter_erreur_mcp(result)
                if result.get("statut") == "STOCK_INSUFFISANT":
                    state["suggestion_en_attente"] = {
                        "type": "CREER_OF",
                        "description": f"Lancer un OF pour {result.get('manque', 0)} u de '{state['ref_article']}'",
                        "params": {
                            "ref_article":        result.get("ref_article", state["ref_article"]),
                            "quantite":           result.get("manque", state["quantite"]),
                            "code_client":        "PROD-INT",
                            "data_bl_en_attente": result.get("data_bl_en_attente", {}),
                        },
                    }
                    state["reponse_finale"] = (
                        result.get("message", "") + "\n\n💡 Tapez **ok** pour lancer l'OF, ou **non** pour annuler."
                    )
                    return state
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["num_piece"]     = result.get("DO_Piece", "")
                state["reponse_brute"] = json.dumps(result, ensure_ascii=False)
                rapport = [result.get("message", "")]
                sugg_fa = result.get("suggestion_facture", {})
                if sugg_fa:
                    num_bl = sugg_fa.get("num_bl", "")
                    state["suggestion_en_attente"] = {
                        "type": "CREER_FACTURE",
                        "description": f"Créer la facture pour BL {num_bl}",
                        "params": sugg_fa,
                    }
                    rapport.append("💡 Tapez **ok** pour créer la Facture.")
                state["reponse_finale"] = "\n\n".join(rapport)
                return state
            elif type_d == "OF":
                result = await _mcp_workflow_of(
                    state["ref_article"], state["quantite"],
                    code_client_final or "PROD-INT",
                )
                err = _traiter_erreur_mcp(result)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["num_piece"]     = result.get("DO_Piece", "")
                state["reponse_brute"] = json.dumps(result, ensure_ascii=False)
                rapport = [result.get("message", "")]
                sugg_bf = result.get("suggestion_bf", {})
                if sugg_bf:
                    state["suggestion_en_attente"] = {
                        "type": "CREER_BF",
                        "description": f"Créer le BF pour OF {sugg_bf.get('num_of', '')}",
                        "params": sugg_bf,
                    }
                    rapport.append("💡 Tapez **ok** pour créer le BF.")
                state["reponse_finale"] = "\n\n".join(r for r in rapport if r)
                return state

            elif type_d == "BF":
                num_of_lie = state.get("num_piece", "")
                if not num_of_lie:
                    msg = (
                        "🚫 Impossible de créer un Bon de Fabrication sans Ordre de Fabrication.\n\n"
                        "   Un BF doit obligatoirement être lié à un OF existant.\n\n"
                        "   💡 Commencez par créer un OF :\n"
                        f"      → \"crée un OF de {state.get('quantite', '?')} pièces de "
                        f"{state.get('ref_article', '?')}\"\n"
                        "   Puis le système vous proposera automatiquement de créer le BF."
                    )
                    state["reponse_brute"]  = msg
                    state["reponse_finale"] = msg
                    return state
                result = await _mcp_workflow_bf(
                    state["ref_article"], state["quantite"],
                    num_of_lie, code_client_final or "PROD-INT",
                )
                err = _traiter_erreur_mcp(result)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["reponse_brute"]  = json.dumps(result, ensure_ascii=False)
                state["reponse_finale"] = result.get("message", "❌ Erreur BF.")
                return state

            else:
                if not type_d:
                    type_d = "FACTURE" if any(
                        w in state["demande_brute"].lower() for w in ("facture", "facturer")
                    ) else "BL"
                raw = await mcp_pool.call("actions", "generer_document_sage", {
                    "type_doc":      type_d,
                    "code_client":   code_client_final or "PROD-INT",
                    "ref_article":   state["ref_article"],
                    "qte":           state["quantite"],
                    "prix_unitaire": 0.0,
                    "num_of":        "",
                })
                data = _parse_mcp_response(raw)
                err  = _traiter_erreur_mcp(data)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                if data.get("DO_Piece") and type_d == "FACTURE":
                    state["num_piece"] = data["DO_Piece"]
                    state["type_doc"]  = "FACTURE"
                state["reponse_brute"] = json.dumps(data, ensure_ascii=False)

        elif act == "CREER_CLIENT":
            # FIX 3 : intitulé = nom_client_brut si disponible
            _intitule = state.get("nom_client_brut") or state.get("code_client") or "Nouveau Client"
            raw  = await mcp_pool.call("actions", "creer_nouveau_client", {
                "code_client": state["code_client"],
                "intitule":    _intitule,
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        elif act == "CREER_FOURNISSEUR":
            _intitule = state.get("nom_client_brut") or state.get("code_client") or "Nouveau Fournisseur"
            raw  = await mcp_pool.call("actions", "creer_nouveau_fournisseur", {
                "code_fournisseur": state["code_client"],
                "intitule":         _intitule,
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        elif act == "MODIFIER_STATUT":
            _statut_cible = state.get("type_doc", "BLOQUE") or "BLOQUE"
            if _statut_cible not in ("BLOQUE", "VALIDE", "SUSPECT"):
                _statut_cible = "BLOQUE"
            raw  = await mcp_pool.call("actions", "modifier_statut_client", {
                "code_client":    state["code_client"],
                "nouveau_statut": _statut_cible,
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        elif act == "TRANSFORMER_DOC":
            num_piece_src = state["num_piece"]
            type_dest     = state["type_doc"] or "FACTURE"

            # ── Vérification : document source déjà transformé ? ──────────
            try:
                import sqlite3
                _db  = sqlite3.connect(str(_db_path))
                _cur = _db.cursor()

                if type_dest == "FACTURE":
                    # Cherche si une facture (DO_Type=3) est déjà liée à ce BL via DO_Ref
                    _cur.execute("""
                        SELECT COUNT(*) FROM F_DOCENTETE
                        WHERE DO_Ref = ? AND DO_Type = 3 AND DO_Domaine = 0
                    """, (num_piece_src,))
                    nb_fa = _cur.fetchone()[0]
                    if nb_fa > 0:
                        _db.close()
                        state["reponse_brute"] = (
                            f"⚠️  Le BL **{num_piece_src}** a déjà été transformé en facture.\n"
                            f"   ({nb_fa} facture(s) existante(s) liée(s) à ce BL)\n"
                            f"   Utilisez 'liste des factures' pour retrouver la facture correspondante."
                        )
                        state["reponse_finale"] = state["reponse_brute"]
                        return state

                elif type_dest == "BF":
                    # Cherche si un BF (DO_Type=4) est déjà lié à cet OF via DO_Ref
                    _cur.execute("""
                        SELECT COUNT(*) FROM F_DOCENTETE
                        WHERE DO_Ref = ? AND DO_Type = 4 AND DO_Domaine = 2
                    """, (num_piece_src,))
                    nb_bf = _cur.fetchone()[0]
                    if nb_bf > 0:
                        _db.close()
                        state["reponse_brute"] = (
                            f"⚠️  L'OF **{num_piece_src}** a déjà été transformé en Bon de Fabrication.\n"
                            f"   ({nb_bf} BF existant(s) lié(s) à cet OF)\n"
                            f"   La fabrication est déjà en cours ou terminée."
                        )
                        state["reponse_finale"] = state["reponse_brute"]
                        return state

                _db.close()

            except Exception as e_verif:
                print(f"   ⚠️  [Vérif doublon] {_safe_str(e_verif)}")
                # On continue même si la vérification échoue

            # ── Transformation effective ───────────────────────────────────
            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_piece_src,
                "type_destination": type_dest,
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "CREER_AVOIR":
            raw  = await mcp_pool.call("actions", "creer_facture_avoir", {
                "num_facture_origine": state["num_piece"],
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "REGLEMENT":
            raw  = await mcp_pool.call("actions", "enregistrer_reglement_facture", {
                "num_piece":     state["num_piece"],
                "mode_paiement": state["mode_paiement"],
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "MOUVEMENT_STOCK":
            raw  = await mcp_pool.call("actions", "ajuster_mouvement_stock", {
                "ref_article":    state["ref_article"],
                "qte_mouvement":  state["quantite"],
                "type_mouvement": "SORTIE" if "sort" in state["demande_brute"].lower() else "ENTREE",
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "PROPOSITION_ACHAT":
            raw  = await mcp_pool.call("actions", "generer_proposition_achat", {
                "ref_article":      state["ref_article"],
                "qte_a_commander":  state["quantite"] or 0.0,
                "code_fournisseur": "FOUR01",
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        else:
            state["reponse_brute"] = f"__INCONNU__:{act}"
            return state

        await response_cache.invalidate_writes()

    except Exception as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state
# ─────────────────────────────────────────────────────────────────────
# NŒUD WORKFLOW
# ─────────────────────────────────────────────────────────────────────
async def noeud_workflow(state: CopilotState) -> CopilotState:
    if not state["validation_ok"]:
        return state
    print("🔄 [Workflow] Flux commande...")
    logs = []
    try:
        txt_c, txt_s = await asyncio.gather(
            mcp_pool.call("nl2sql", "verifier_statut_client",  {"code_client": state["code_client"]}),
            mcp_pool.call("nl2sql", "verifier_stock_article",  {"ref_article": state["ref_article"]}),
        )
        statut = (
            "NON_TROUVE" if "NON_TROUVE" in txt_c else
            "BLOQUE"     if "BLOQUE"     in txt_c else
            "SUSPECT"    if "SUSPECT"    in txt_c else "VALIDE"
        )
        m = re.search(r"net:\s*([\d.]+)", txt_s)
        stock_dispo = float(m.group(1)) if m else 0.0

        ctx      = await _hub_contexte_client(state["code_client"], statut, stock_dispo, state["quantite"])
        decision = ctx.get("decision", "VALIDER")
        logs.extend(f"⚠️  {a}" for a in ctx.get("alertes", []))

        if decision == "BLOQUER":
            state["reponse_brute"] = (
                f"🛑 COMMANDE BLOQUÉE — {state['code_client']}\n"
                + "\n".join(f"  • {l}" for l in logs)
            )
            return state

        if decision == "CREER_CLIENT":
            c = await _input(f"❓ Client '{state['code_client']}' inconnu. Créer ? [Y/n] : ")
            if c.strip().lower() in ("n", "no", "non"):
                state["reponse_brute"] = "🛑 Flux annulé."
                return state
            raw = await mcp_pool.call("actions", "creer_nouveau_client", {
                "code_client": state["code_client"],
                "intitule":    state.get("nom_client_brut") or state["code_client"],
            })
            data = _parse_mcp_response(raw)
            logs.append(data.get("message", "✅ Client créé."))

        result_bl = await _mcp_workflow_bl(
            state["code_client"], state["ref_article"], state["quantite"], 0.0
        )
        logs.append(result_bl.get("message", ""))

        if result_bl.get("statut") == "STOCK_INSUFFISANT":
            result_of = await _mcp_workflow_of(state["ref_article"], state["quantite"])
            logs.append(result_of.get("message", ""))
            if result_of.get("statut") == "GENERE":
                result_bf = await _mcp_workflow_bf(
                    state["ref_article"], state["quantite"], result_of.get("DO_Piece", "")
                )
                logs.append(result_bf.get("message", ""))
                result_bl2 = await _mcp_workflow_bl(
                    state["code_client"], state["ref_article"], state["quantite"], 0.0
                )
                logs.append(result_bl2.get("message", ""))
                if result_bl2.get("statut") == "GENERE":
                    result_bl = result_bl2

        if result_bl.get("statut") == "GENERE":
            num_bl = result_bl.get("DO_Piece", "")
            state["num_piece"] = num_bl
            raw_fa = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_bl,
                "type_destination": "FACTURE",
            })
            data_fa = _parse_mcp_response(raw_fa)
            logs.append(data_fa.get("message", ""))

        state["reponse_brute"] = "\n".join(l for l in logs if l)
        await response_cache.invalidate_writes()

    except Exception as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state


# ─────────────────────────────────────────────────────────────────────
# NŒUD SYNTHÈSE — v9.3 FIX 7
# ─────────────────────────────────────────────────────────────────────


def _formater_nl2sql_brut(rb: str, question: str) -> str:
    """
    PATCH C-3 : Formate une réponse NL2SQL brute de manière lisible
    sans passer par le LLM (utilisé en cas de timeout synthèse).
    Gère : texte tabulaire déjà formaté, JSON, erreur.
    """
    if not rb:
        return "⚠️  Aucun résultat trouvé."

    # Déjà bien formaté (commence par emoji ou tirets)
    if rb.startswith(("📊", "✅", "❌", "⚠️", "─", "👥", "📦", "🏆", "⏳", "Question :")):
        return rb

    # Tenter parsing JSON
    try:
        data = json.loads(rb)
        if isinstance(data, dict):
            if "erreur" in data:
                return f"❌ Erreur SQL : {data['erreur']}"
            statut = data.get("statut", "")
            if statut == "OK":
                for _act, _fmt in _FORMATEURS_JSON.items():
                    try:
                        r = _fmt(data)
                        if r:
                            return r
                    except Exception:
                        continue
                # Fallback générique : chercher une liste de données dans le JSON
                for key in ("clients", "factures", "articles", "resultats", "rows", "data", "lignes"):
                    items = data.get(key)
                    if items and isinstance(items, list) and items:
                        cols = list(items[0].keys()) if isinstance(items[0], dict) else []
                        lignes = [f"📊 {question} — {len(items)} résultat(s) :", "─" * 60]
                        for i, row in enumerate(items[:30], 1):
                            parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                            lignes.append(f"  {i:>3}. " + " │ ".join(parts))
                        if len(items) > 30:
                            lignes.append(f"  ... et {len(items) - 30} ligne(s) supplémentaire(s)")
                        lignes.append("─" * 60)
                        return "\n".join(lignes)
                # Dernier recours : afficher les clés/valeurs scalaires du dict
                parts = [f"{k}: {v}" for k, v in data.items()
                         if v is not None and not isinstance(v, (dict, list))]
                if parts:
                    return f"📊 {question} :\n  " + "\n  ".join(parts)
            if data.get("message"):
                return data["message"]
        elif isinstance(data, list):
            if not data:
                return f"📊 Résultat de « {question} » : Aucun résultat."
            cols = list(data[0].keys()) if isinstance(data[0], dict) else []
            if not cols:
                return str(data)
            lignes = [f"📊 Résultat de « {question} » — {len(data)} ligne(s) :", "─" * 60]
            for i, row in enumerate(data[:30], 1):
                parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                lignes.append(f"  {i:>3}. " + " │ ".join(parts))
            if len(data) > 30:
                lignes.append(f"  ... et {len(data) - 30} ligne(s) supplémentaire(s)")
            lignes.append("─" * 60)
            return "\n".join(lignes)
    except (json.JSONDecodeError, ValueError):
        pass

    # Texte brut tabulaire déjà lisible → retourner tel quel
    if "│" in rb or "─" in rb or ":" in rb:
        return rb

    # Dernier recours
    return f"📊 Résultat de « {question} » :\n{rb}"

# ── Helpers anti-hallucination (coller AVANT noeud_synthese) ──────────────

_HALLUCINATION_MARKERS = (
    "par exemple", "supposons", "imaginons", "à titre d'exemple",
    "typiquement", "généralement", "en général", "il est probable",
    "il se peut que", "je suppose", "je présume", "hypothétiquement",
    "dans ce cas fictif", "données fictives", "exemple fictif",
)

def _rb_est_vide(rb: str) -> bool:
    """True si rb ne contient aucune donnée exploitable."""
    if not rb or not rb.strip():
        return True
    rb_strip = rb.strip()
    if rb_strip in ("{}", "[]", '{"statut": "OK"}', '{"statut":"OK"}'):
        return True
    try:
        data = json.loads(rb_strip)
        if isinstance(data, dict):
            values = [v for k, v in data.items() if k != "statut"]
            if all(v in (None, [], {}, "", 0) for v in values):
                return True
        elif isinstance(data, list) and len(data) == 0:
            return True
    except Exception:
        pass
    return len(rb_strip) < 10

def _detecter_hallucination(synthese: str, rb: str) -> bool:
    """Détecte si le LLM a probablement inventé des données."""
    if not synthese:
        return False
    s_lower = synthese.lower()
    # 1. Marqueurs linguistiques d'invention
    for marker in _HALLUCINATION_MARKERS:
        if marker in s_lower:
            print(f"   🚨 [Anti-hallucination] Marqueur détecté : '{marker}'")
            return True
    # 2. Données vides mais synthèse longue → invention quasi-certaine
    if _rb_est_vide(rb) and len(synthese.strip()) > 200:
        print(f"   🚨 [Anti-hallucination] Données vides + synthèse longue ({len(synthese)} chars)")
        return True
    # 3. Nombres absents des données source (heuristique légère)
    import re
    nb_synthese = set(re.findall(r'\b\d{4,}\b', synthese))
    nb_rb       = set(re.findall(r'\b\d{4,}\b', rb))
    inventes    = nb_synthese - nb_rb
    if len(inventes) > 2:
        print(f"   🚨 [Anti-hallucination] Nombres absents du rb : {inventes}")
        return True
    return False
async def noeud_synthese(state: CopilotState) -> CopilotState:
    rb  = state.get("reponse_brute", "") or ""
    act = state.get("action", "")

    # Réponse finale déjà construite (workflow BL stock insuffisant, etc.)
    if state.get("reponse_finale"):
        return state

    if rb.startswith("__ERREUR__"):
        err = rb.replace("__ERREUR__:", "")
        state["reponse_finale"] = f"❌ Erreur système : {err}"
        return state

    if rb.startswith("__INCONNU__"):
        state["reponse_finale"] = f"⚠️  Action non reconnue : {rb}"
        return state

    # ══════════════════════════════════════════════════════════════
    # FIX 7 : NL2SQL_LIBRE → LLM systématique pour interpréter les résultats
    # ══════════════════════════════════════════════════════════════
    if act == "NL2SQL_LIBRE" and rb and not rb.startswith("__"):

        # COURT-CIRCUIT : réponse déjà formatée
        _deja_formate = rb.startswith((
            "📊", "✅", "❌", "⚠️", "─", "👥", "📦", "🏆", "⏳", "Question :"
        ))
        if _deja_formate:
            print("   ⚡ [Synthèse NL2SQL] Réponse déjà formatée → pas de LLM (gain de temps)")
            state["reponse_finale"] = rb
            return state

        # ── NOUVEAU : données vides → pas de LLM du tout ──────────
        if _rb_est_vide(rb):
            print("   ⚠️  [Synthèse NL2SQL] Données vides → pas de LLM (anti-hallucination)")
            state["reponse_finale"] = (
                "⚠️  Aucun résultat trouvé pour cette requête.\n"
                "Les données demandées ne sont pas disponibles dans la base."
            )
            return state
        # ──────────────────────────────────────────────────────────

        if ENABLE_LLM_SYNTHESE:
            # ── PROMPT RENFORCÉ anti-hallucination ────────────────
            prompt = (
                f'Tu es un assistant ERP Sage 100 expert et rigoureux.\n'
                f'QUESTION UTILISATEUR : "{state["demande_brute"]}"\n\n'
                f'DONNÉES RETOURNÉES PAR LA BASE :\n'
                f'```\n{rb[:3000]}\n```\n\n'
                f'RÈGLES ABSOLUES :\n'
                f'1. Base-toi UNIQUEMENT sur les données ci-dessus.\n'
                f'2. N\'invente AUCUN nom, chiffre, date ou montant absent des données.\n'
                f'3. Si une information est absente → écris "Information non disponible".\n'
                f'4. N\'utilise JAMAIS : "par exemple", "supposons", "typiquement",\n'
                f'   "généralement", "il est probable", "je suppose".\n'
                f'5. N\'écris JAMAIS de JSON, SQL ou bloc de code.\n'
                f'6. Réponds uniquement en français, de façon directe et factuelle.\n\n'
                f'Analyse les données et réponds clairement. '
                f'Utilise des tableaux ASCII, emojis et formatage pour la lisibilité.\n'
                f'Commence directement par la réponse en français.'
            )
            # ──────────────────────────────────────────────────────
            try:
                synthese = await asyncio.wait_for(
                    _invoke_llm(prompt, use_smart=True), timeout=SYNTHESE_TIMEOUT
                )
                _s = synthese.strip()
                _est_json_llm = (
                    (_s.startswith("{") and _s.endswith("}"))
                    or (_s.startswith("[") and _s.endswith("]"))
                    or _s.startswith("```json")
                    or (_s.startswith("```") and ("SELECT" in _s.upper() or "{" in _s))
                    or (len(_s) > 10 and _s.count("\n") < 2 and _s.startswith("{"))
                )
                if _est_json_llm:
                    print("   ⚠️  [Synthèse NL2SQL] LLM a répondu en JSON → fallback formateur")
                    state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"])
                # ── NOUVEAU : garde anti-hallucination ────────────
                elif _detecter_hallucination(_s, rb):
                    print("   🚨 [Synthèse NL2SQL] Hallucination détectée → fallback formateur")
                    state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"])
                elif not _s:
                    print("   ⚠️  [Synthèse NL2SQL] Synthèse vide → fallback formateur")
                    state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"])
                # ──────────────────────────────────────────────────
                else:
                    state["reponse_finale"] = synthese
                if ENABLE_MEM0:
                    asyncio.create_task(
                        asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], state["reponse_finale"])
                    )
                return state
            except asyncio.TimeoutError:
                print(f"   ⚠️  [Synthèse NL2SQL] Timeout {SYNTHESE_TIMEOUT}s → réponse brute")
            except Exception as e:
                print(f"   ⚠️  [Synthèse NL2SQL] {_safe_str(e)}")
        # PATCH C-2 : formateur fallback lisible si LLM désactivé ou timeout
        state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"])
        return state
    # ══════════════════════════════════════════════════════════════

    # Formateurs directs JSON pour actions LECTURE classiques
    formatted = _formater_reponse_directe(act, rb)
    if formatted:
        state["reponse_finale"] = formatted
        if ENABLE_MEM0 and state.get("mem0_contexte") is not None:
            asyncio.create_task(
                asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], formatted)
            )
        return state

    # Fallback : si JSON parseable avec statut OK et formateur connu → retry avec données
    if not formatted and rb and not rb.startswith("__"):
        try:
            data = json.loads(rb)
            if isinstance(data, dict) and data.get("statut") in ("OK", "TROUVE"):
                for _act_try in [act, act.upper()]:
                    if _act_try in _FORMATEURS_JSON:
                        try:
                            formatted = _FORMATEURS_JSON[_act_try](data)
                            if formatted:
                                state["reponse_finale"] = formatted
                                if ENABLE_MEM0 and state.get("mem0_contexte") is not None:
                                    asyncio.create_task(
                                        asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], formatted)
                                    )
                                return state
                        except Exception:
                            pass
                if "factures" in data or "clients" in data or "articles" in data:
                    state["reponse_finale"] = rb
                    return state
        except Exception:
            pass

    # Bypass LLM si c'est déjà un message d'exécution (Fix C)
    if rb.startswith("✅") or rb.startswith("❌") or rb.startswith("⚠️"):
        state["reponse_finale"] = rb
        return state

    # Synthèse LLM générale (actions LECTURE classiques sans formateur)
    if ENABLE_LLM_SYNTHESE and rb and not rb.startswith("__"):

        # ── NOUVEAU : données vides → pas de LLM du tout ──────────
        if _rb_est_vide(rb):
            print("   ⚠️  [Synthèse] Données vides → pas de LLM (anti-hallucination)")
            state["reponse_finale"] = "⚠️  Aucune donnée disponible pour cette demande."
            return state
        # ──────────────────────────────────────────────────────────

        mem_ctx = state.get("mem0_contexte", "")
        rag_ctx = state.get("rag_complement", "")

        # ── PROMPT RENFORCÉ anti-hallucination ────────────────────
        prompt = (
            f'Tu es un assistant ERP Sage 100 expert et rigoureux.\n'
            f'DEMANDE UTILISATEUR : "{state["demande_brute"]}"\n'
        )
        if mem_ctx:
            prompt += f'CONTEXTE MÉMORISÉ : {mem_ctx}\n'
        if rag_ctx:
            prompt += f'INFORMATIONS COMPLÉMENTAIRES : {rag_ctx}\n'
        prompt += (
            f'\nDONNÉES BRUTES :\n```\n{rb[:2000]}\n```\n\n'
            f'RÈGLES ABSOLUES :\n'
            f'1. Base-toi UNIQUEMENT sur les données ci-dessus.\n'
            f'2. N\'invente AUCUN nom, chiffre, date ou montant absent des données.\n'
            f'3. Si une information est absente → écris "Information non disponible".\n'
            f'4. N\'utilise JAMAIS : "par exemple", "supposons", "typiquement",\n'
            f'   "généralement", "il est probable", "je suppose".\n'
            f'5. Réponds uniquement en français, de façon directe et factuelle.\n\n'
            f'Rédige une réponse claire, concise et structurée. '
            f'Utilise des emojis et du formatage pour la lisibilité.'
        )
        # ──────────────────────────────────────────────────────────
        try:
            synthese = await asyncio.wait_for(
                _invoke_llm(prompt, use_smart=True), timeout=SYNTHESE_TIMEOUT
            )
            _s2 = synthese.strip()
            if ((_s2.startswith("{") and _s2.endswith("}"))
                    or (_s2.startswith("[") and _s2.endswith("]"))
                    or _s2.startswith("```json")):
                print("   ⚠️  [Synthèse] LLM a répondu en JSON → réponse brute")
                state["reponse_finale"] = rb
            # ── NOUVEAU : garde anti-hallucination ────────────────
            elif _detecter_hallucination(_s2, rb):
                print("   🚨 [Synthèse] Hallucination détectée → réponse brute")
                state["reponse_finale"] = rb
            # ──────────────────────────────────────────────────────
            else:
                state["reponse_finale"] = synthese
            if ENABLE_MEM0:
                asyncio.create_task(
                    asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], state["reponse_finale"])
                )
            return state
        except asyncio.TimeoutError:
            print(f"   ⚠️  [Synthèse] Timeout {SYNTHESE_TIMEOUT}s → réponse brute")
        except Exception as e:
            print(f"   ⚠️  [Synthèse] Erreur : {_safe_str(e)}")

    state["reponse_finale"] = rb or "⚠️  Aucune réponse disponible."
    return state
# ─────────────────────────────────────────────────────────────────────
# EXÉCUTEUR SUGGESTIONS
# ─────────────────────────────────────────────────────────────────────
async def _executer_suggestion(suggestion: dict, contexte_session: dict) -> str:
    type_sugg = suggestion.get("type", "")
    params    = suggestion.get("params", {})
    print(f"\n   ✅ [Suggestion] {suggestion.get('description', type_sugg)}")

    if type_sugg == "CREER_FACTURE_ACHAT":
        num_br           = params.get("num_br", "")
        code_fournisseur = params.get("code_fournisseur", "")
        nom_fournisseur  = params.get("nom_fournisseur", code_fournisseur)
        montant          = float(params.get("montant", 0.0))
        try:
            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_br,
                "type_destination": "FA_ACHAT",
            })
            data   = _parse_mcp_response(raw)
            num_fa = data.get("DO_Piece") or data.get("num_piece_dest", "?")
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur création facture fournisseur depuis {num_br}.")
            contexte_session["dernier_document"] = {
                "type_doc": "FA_ACHAT", "num_piece": num_fa, "code_client": code_fournisseur,
            }
            contexte_session["suggestion_en_attente"] = {}
            return (
                f"✅ Facture Fournisseur créée depuis {num_br} !\n"
                f"   • Numéro Facture  : {num_fa}\n"
                f"   • Fournisseur     : {nom_fournisseur}\n"
                f"   • Montant HT      : {montant:.2f} €\n"
                f"   ℹ️  Document enregistré en achat (DO_Domaine=1, DO_Type=3)"
            )
        except Exception as e:
            return f"❌ Erreur création facture fournisseur : {_safe_str(e)}"

    elif type_sugg == "CREER_FACTURE":
        num_bl      = params.get("num_bl", "")
        code_client = params.get("code_client", "")
        nom_client  = params.get("nom_client", code_client)
        montant     = float(params.get("montant", 0.0))
        try:
            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_bl,
                "type_destination": "FACTURE",
            })
            data   = _parse_mcp_response(raw)
            num_fa = data.get("DO_Piece") or data.get("num_piece_dest", "?")
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur création facture depuis {num_bl}.")
            contexte_session["dernier_document"] = {
                "type_doc": "FACTURE", "num_piece": num_fa, "code_client": code_client,
            }
            contexte_session["suggestion_en_attente"] = {
                "type": "REGLER_FACTURE",
                "description": f"Régler la facture {num_fa}",
                "params": {
                    "num_piece": num_fa, "mode_paiement": "Virement",
                    "montant": montant, "nom_client": nom_client,
                },
            }
            return (
                f"✅ Facture créée depuis {num_bl} !\n"
                f"   • Numéro Facture : {num_fa}\n"
                f"   • Client         : {nom_client}\n"
                f"   • Montant        : {montant:.2f} €"
            )
        except Exception as e:
            return f"❌ Erreur création facture : {_safe_str(e)}"

    elif type_sugg == "REGLER_FACTURE":
        num_piece     = params.get("num_piece", "")
        mode_paiement = params.get("mode_paiement", "Virement")
        montant       = float(params.get("montant", 0.0))
        nom_client    = params.get("nom_client", "")
        try:
            raw  = await mcp_pool.call("actions", "enregistrer_reglement_facture", {
                "num_piece": num_piece, "mode_paiement": mode_paiement,
            })
            data = _parse_mcp_response(raw)
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur règlement {num_piece}.")
            contexte_session["suggestion_en_attente"] = {}
            return (
                f"✅ Règlement enregistré !\n"
                f"   • Facture  : {num_piece}\n"
                f"   • Client   : {nom_client}\n"
                f"   • Montant  : {montant:.2f} €\n"
                f"   • Mode     : {mode_paiement}"
            )
        except Exception as e:
            return f"❌ Erreur règlement : {_safe_str(e)}"

    elif type_sugg == "CREER_BF":
        ref_article = params.get("ref_article", "")
        quantite    = float(params.get("quantite", 0.0))
        num_of      = params.get("num_of", "")
        code_client = params.get("code_client", "PROD-INT")
        try:
            result = await _mcp_workflow_bf(ref_article, quantite, num_of, code_client)
            if result.get("statut") in _STATUTS_ERREUR_MCP:
                return result.get("message", "❌ Erreur BF.")
            contexte_session["suggestion_en_attente"] = {}
            return result.get("message", "✅ BF créé.")
        except Exception as e:
            return f"❌ Erreur BF : {_safe_str(e)}"

    elif type_sugg == "CREER_OF":
        ref_article = params.get("ref_article", "")
        quantite    = float(params.get("quantite", 0.0))
        data_bl     = params.get("data_bl_en_attente", {})
        rapport     = []
        try:
            result_of = await _mcp_workflow_of(ref_article, quantite, "PROD-INT")
            rapport.append(result_of.get("message", ""))
            if result_of.get("statut") == "GENERE":
                num_of  = result_of.get("DO_Piece", "")
                sugg_bf = result_of.get("suggestion_bf", {})
                if sugg_bf:
                    contexte_session["suggestion_en_attente"] = {
                        "type": "CREER_BF_PUIS_BL",
                        "description": f"Créer le BF pour OF {num_of} puis le BL",
                        "params": {**sugg_bf, "data_bl_apres_bf": data_bl},
                    }
            elif result_of.get("statut") in _STATUTS_ERREUR_MCP:
                contexte_session["suggestion_en_attente"] = {}
            return "\n\n".join(r for r in rapport if r)
        except Exception as e:
            return f"❌ Erreur OF : {_safe_str(e)}"

    elif type_sugg == "CREER_BF_PUIS_BL":
        ref_article = params.get("ref_article", "")
        quantite    = float(params.get("quantite", 0.0))
        num_of      = params.get("num_of", "")
        data_bl     = params.get("data_bl_apres_bf", {})
        rapport     = []

        try:
            result_bf = await _mcp_workflow_bf(ref_article, quantite, num_of, "PROD-INT")
            rapport.append(result_bf.get("message", ""))
            if result_bf.get("statut") not in _STATUTS_ERREUR_MCP and data_bl:
                result_bl = await _mcp_workflow_bl(
                    data_bl.get("code_client", ""),
                    data_bl.get("ref_article", ""),
                    float(data_bl.get("quantite", 0.0)),
                    float(data_bl.get("prix_unitaire", 0.0)),
                )
                rapport.append(result_bl.get("message", ""))
                if result_bl.get("statut") == "GENERE":
                    sugg_fa = result_bl.get("suggestion_facture", {})
                    if sugg_fa:
                        num_bl = sugg_fa.get("num_bl", "")
                        contexte_session["suggestion_en_attente"] = {
                            "type": "CREER_FACTURE",
                            "description": f"Créer la facture pour BL {num_bl}",
                            "params": sugg_fa,
                        }
                    else:
                        contexte_session["suggestion_en_attente"] = {}
                else:
                    contexte_session["suggestion_en_attente"] = {}
            else:
                contexte_session["suggestion_en_attente"] = {}
            return "\n\n".join(r for r in rapport if r)
        except Exception as e:
            return f"❌ Erreur BF→BL : {_safe_str(e)}"

    elif type_sugg == "FACTURE_ACHAT":
        num_bl = params.get("num_bl", "")
        try:
            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_bl,
                "type_destination": "FACTURE_ACHAT",
            })
            data   = _parse_mcp_response(raw)
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur création facture achat depuis {num_bl}.")
            num_fa = data.get("DO_Piece") or data.get("num_piece_dest", "?")
            contexte_session["suggestion_en_attente"] = {}
            return (
                f"✅ Facture Fournisseur créée depuis {num_bl} !\n"
                f"   • Numéro Facture : {num_fa}\n"
                f"   ℹ️  Document enregistré en achat (DO_Domaine=1, DO_Type=3)"
            )
        except Exception as e:
            return f"❌ Erreur création facture fournisseur : {_safe_str(e)}"

    return f"⚠️  Suggestion '{type_sugg}' non reconnue."


# ─────────────────────────────────────────────────────────────────────
# NŒUD KB
# ─────────────────────────────────────────────────────────────────────
async def noeud_kb(state: CopilotState) -> CopilotState:
    print("📚 [Agent KB]...")
    act = state["action"]
    try:
        if act == "RECHERCHE_PROCEDURE":
            raw = await mcp_pool.call("kb", "rechercher_procedure", {
                "contexte": state["demande_brute"],
            })
        elif act == "RECOMMANDATION":
            raw = await mcp_pool.call("kb", "generer_recommandation", {
                "contexte":    state["demande_brute"],
                "code_client": state.get("code_client", ""),
                "indicateur":  "CA",
            })
        elif act == "SEUIL_STOCK":
            raw = await mcp_pool.call("kb", "verifier_seuil_stock", {
                "ref_article": state.get("ref_article", ""),
            })
        elif act == "LISTE_PROCEDURES":
            raw = await mcp_pool.call("kb", "lister_procedures", {})
        else:
            raw = f"Action KB non reconnue : {act}"
        state["reponse_brute"] = raw
    except Exception as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state


# ─────────────────────────────────────────────────────────────────────
# CONSTRUCTION DU GRAPHE LANGGRAPH
# ─────────────────────────────────────────────────────────────────────
def _construire_graphe() -> object:
    g = StateGraph(CopilotState)

    g.add_node("classifier",    noeud_classifier)
    g.add_node("planner",       noeud_planner)
    g.add_node("hors_sujet",    noeud_hors_sujet)
    g.add_node("aide",          noeud_aide)
    g.add_node("clarification", noeud_clarification)
    g.add_node("lecture",       noeud_lecture)
    g.add_node("nl2sql",        noeud_nl2sql_libre)
    g.add_node("confirmation",  noeud_confirmation)
    g.add_node("ecriture",      noeud_ecriture)
    g.add_node("workflow",      noeud_workflow)
    g.add_node("kb",            noeud_kb)
    g.add_node("synthese",      noeud_synthese)
    g.add_node(
    "complements",
    noeud_complements
)

    g.add_edge(START, "classifier")

    def _router(state: CopilotState) -> str:
        intention = state.get("intention", "ERP")
        if intention == "HORS_SUJET":
            return "hors_sujet"
        if intention == "AIDE":
            return "aide"
        if state.get("ambigue"):
            return "clarification"
        act = state.get("action", "")
        if act in ACTIONS_LECTURE | ACTIONS_EXPORT:
            return "lecture"
        if act in ACTIONS_NL2SQL:
            return "nl2sql"
        if act in ACTIONS_ECRITURE | ACTIONS_WORKFLOW:
            return "confirmation"
        if act in ACTIONS_KB:
            return "kb"
        return "nl2sql"

    g.add_conditional_edges("classifier", _router, {
        "hors_sujet":    "hors_sujet",
        "aide":          "aide",
        "clarification": "clarification",
        "lecture":       "lecture",
        "nl2sql":        "nl2sql",
        "confirmation":  "confirmation",
        "kb":            "kb",
    })

    def _router_confirmation(state: CopilotState) -> str:
        if not state.get("validation_ok", False):
            return "synthese"
        act = state.get("action", "")
        if act == "WORKFLOW_COMMANDE":
            return "workflow"
        return "ecriture"

    g.add_conditional_edges("confirmation", _router_confirmation, {
        "synthese":  "synthese",
        "workflow":  "workflow",
        "ecriture":  "ecriture",
    })

    # ── Routeur ecriture → complements (si champs manquants) ou synthese ──
    def _router_ecriture(state: CopilotState) -> str:
        if state.get("attente_complements"):
            return "complements"
        return "synthese"

    g.add_conditional_edges("ecriture", _router_ecriture, {
        "complements": "complements",
        "synthese":    "synthese",
    })
    # noeud_complements pose la question → synthese l'affiche → fin du tour
    g.add_edge("complements", "synthese")

    for noeud in ("hors_sujet", "aide", "clarification", "lecture",
                   "nl2sql", "workflow", "kb"):
        g.add_edge(noeud, "synthese")
    g.add_edge("synthese", END)

    return g.compile()


# ─────────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────────────────────────────
async def main():
    vanna_status    = "ON ✨" if ENABLE_VANNA  else "OFF"
    gliner_status   = "ON"   if ENABLE_GLINER else "OFF"
    mem0_status     = "ON"   if ENABLE_MEM0   else "OFF"
    fallback_status = f"✅ {FALLBACK_MODEL}" if FALLBACK_KEY else "❌ non configuré"

    print(f"""
═════════════════════════════════════════════════════════════════
🤖  COPILOT ERP SAGE 100 — v9.3
    Fast : {MODELE_FAST}  (timeout {OLLAMA_TIMEOUT_FAST}s)
    Smart: {MODELE_SMART} (timeout {OLLAMA_TIMEOUT_SMART}s)
    Fallback : {fallback_status}
    GLiNER: {gliner_status} | Vanna: {vanna_status} | Mem0: {mem0_status}
    ✅ FIX1 Article insensible casse
    ✅ FIX2 TRANSFORMER_DOC prioritaire
    ✅ FIX3 Extraction num_piece TRANSFORMER_DOC
    ✅ FIX4 NL2SQL BL/BC/OF/BF par client + filtres analytiques
    ✅ FIX5 _est_nom_valide rejette chiffres
    ✅ FIX6 _rechercher_client len>3 + sortie rapide
    ✅ FIX7 Synthèse NL2SQL LLM + timeout 120s
    (tapez 'aide', 'cache', 'warmup', 'reset', 'quitter')
═════════════════════════════════════════════════════════════════
""")

    print("⏳ [Init] Chargement parallèle des composants...")
    init_tasks = [mcp_pool.init(), _warmup_ollama()]
    if ENABLE_VANNA:  init_tasks.append(_get_vanna_async())
    if ENABLE_GLINER: init_tasks.append(_get_gliner_async())
    if ENABLE_MEM0:   init_tasks.append(_get_mem0_async())

    await asyncio.gather(*init_tasks, return_exceptions=True)
    print("✅ [Init] Prêt.\n")

    graphe = _construire_graphe()

    contexte_session: dict = {
        "dernier_code_client":   "",
        "dernier_ref_article":   "",
        "dernier_quantite":      0.0,
        "dernier_nom_client":    "",
        "dernier_document":      {},
        "dernier_num_piece":     "",
        "dernier_type_doc":      "",
        "suggestion_en_attente": {},
    }
    demande_precedente = ""

    while True:
        try:
            demande = await _input("\n👤 Votre demande : ")
            demande = demande.strip()
            if not demande:
                continue

            if demande.lower() == "quitter":
                print("👋 Au revoir !")
                break
            if demande.lower() == "reset":
                contexte_session = {
                    "dernier_code_client": "", "dernier_ref_article": "",
                    "dernier_quantite": 0.0,   "dernier_nom_client": "",
                    "dernier_document": {},     "suggestion_en_attente": {},
                    "dernier_num_piece": "",    "dernier_type_doc": "",
                }
                demande_precedente = ""
                print("🔄 Session réinitialisée.")
                continue
            if demande.lower() == "cache":
                await response_cache.invalidate_writes()
                print("🗑️  Cache vidé.")
                continue
            if demande.lower() == "warmup":
                await _warmup_ollama()
                continue
            if demande.lower() == "session":
                print(f"📋 Session : {json.dumps(contexte_session, ensure_ascii=False, indent=2)}")
                continue
            if demande.lower() == "aide":
                print(f"\n{CAPACITES_SYSTEME}\n")
                continue

            sugg = contexte_session.get("suggestion_en_attente", {})
            if sugg and (_est_oui(demande) or _est_non(demande)):
                if _est_oui(demande):
                    reponse_sugg = await _executer_suggestion(sugg, contexte_session)
                    print(f"\n{'─'*65}\n📡 COPILOT ERP :\n{'─'*65}")
                    print(reponse_sugg)
                    print(f"{'─'*65}\n")
                else:
                    contexte_session["suggestion_en_attente"] = {}
                    print("🛑 Suggestion annulée.")
                continue

            demande_resolue = _resoudre_references(
                demande, contexte_session.get("dernier_document", {})
            )

            sous_demandes = await decouper_demande_composite(demande_resolue)
            reponses_multi = []

            for sous_d in sous_demandes:
                demande_courante = sous_d["demande"]

                if (demande_precedente
                        and demande_courante.lower() in ("oui","o","ok","yes","y")
                        and not sugg):
                    demande_courante = _fusionner_demandes(demande_precedente, demande_courante)

                etat = _etat_initial(demande_courante, contexte_session)
                # Héritage des champs multi-tours depuis la session
                if contexte_session.get("attente_complements"):
                    etat["attente_complements"] = True
                    etat["pending_document"]    = contexte_session.get("pending_document", {})
                try:
                    final_state = await graphe.ainvoke(etat)
                except Exception as e:
                    final_state = {**etat, "reponse_finale": f"❌ Erreur système : {_safe_str(e)}"}

                reponse = final_state.get("reponse_finale", "⚠️  Aucune réponse.")

                if final_state.get("code_client"):
                    contexte_session["dernier_code_client"] = final_state["code_client"]
                if final_state.get("ref_article"):
                    contexte_session["dernier_ref_article"] = final_state["ref_article"]
                if final_state.get("quantite", 0) > 0:
                    contexte_session["dernier_quantite"] = final_state["quantite"]
                if final_state.get("nom_client_brut"):
                    contexte_session["dernier_nom_client"] = final_state["nom_client_brut"]

                doc_extrait = _extraire_dernier_document(final_state)
                if doc_extrait and doc_extrait.get("type_doc", "") not in ("OF", "BF"):
                    contexte_session["dernier_document"] = doc_extrait
                elif not doc_extrait:
                    if final_state.get("action") not in ("GENERER_DOC",):
                        contexte_session["dernier_document"] = {}

                if doc_extrait:
                    num_p  = doc_extrait.get("num_piece", "")
                    type_p = doc_extrait.get("type_doc", "")
                    if num_p:
                        contexte_session["dernier_num_piece"] = num_p
                        contexte_session["dernier_type_doc"]  = type_p

                if not final_state.get("ambigue"):
                    contexte_session["dernier_quantite"] = 0.0

                sugg_nouvelle = final_state.get("suggestion_en_attente", {})
                if sugg_nouvelle:
                    contexte_session["suggestion_en_attente"] = sugg_nouvelle

                # Persistance état multi-tours dans la session
                if final_state.get("attente_complements"):
                    contexte_session["attente_complements"] = True
                    contexte_session["pending_document"]    = final_state.get("pending_document", {})
                else:
                    contexte_session["attente_complements"] = False
                    contexte_session["pending_document"]    = {}

                if final_state.get("ambigue"):
                    demande_precedente = demande_courante
                else:
                    demande_precedente = ""

                reponses_multi.append(reponse)

                if sugg_nouvelle:
                    desc = sugg_nouvelle.get("description", "")
                    reponses_multi.append(
                        f"\n💡 Suggestion : {desc}\n   Tapez **ok** pour confirmer ou **non** pour annuler."
                    )

            print(f"\n{'─'*65}")
            print(f"📡 COPILOT ERP :")
            print(f"{'─'*65}")
            print("\n\n".join(reponses_multi))
            print(f"{'─'*65}\n")

        except KeyboardInterrupt:
            print("\n👋 Au revoir !")
            break
        except Exception as e:
            print(f"\n❌ Erreur inattendue : {_safe_str(e)}")
            print(f"   Détails : {tb.format_exc()[-500:]}")


if __name__ == "__main__":
    asyncio.run(main())