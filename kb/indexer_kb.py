#!/usr/bin/env python3
"""
indexer_kb.py — Indexation vectorielle de documents PDF non structurés (Qdrant)
================================================================================
Remplace l'ancienne version basée sur des .md avec frontmatter par un pipeline
adapté à des PDF quelconques (contrats, fiches, réclamations, emails imprimés,
scans...) :

  1. Extraction texte par page (PyMuPDF), avec fallback OCR (Tesseract) si la
     page est un scan sans texte extractible.
  2. Métadonnées devinées automatiquement :
       - doc_type   → nom du sous-dossier parent si organisé ainsi,
                       sinon classification LLM sur le 1er extrait,
                       sinon "inconnu"
       - code_client / ref_article → détectés par regex sur le texte
         (et en secours dans le nom de fichier)
  3. Chunking par page avec overlap (garde le numéro de page pour la citation).
  4. Cache par hash de fichier : un PDF déjà indexé et inchangé n'est pas
     retraité (évite de refaire l'OCR à chaque run).
  5. Index vectoriel (Qdrant) + lexical (BM25), fusionnés par RRF — logique de
     recherche inchangée par rapport à la version précédente, donc
     mcp_knowledge_base.py n'a besoin d'aucune modification.

Organisation attendue des sources (mais pas obligatoire) :
    kb_docs_pdf/
      fiche_article/AR001.pdf
      reclamation_sav/AR001_reclamation_1.pdf
      note_crm/CLI003.pdf
      (PDF en vrac à la racine → doc_type deviné par LLM ou "inconnu")

Dépendances supplémentaires :
    pip install pymupdf pdf2image pytesseract
    + binaires système : poppler-utils (pdf2image), tesseract-ocr + langue "fra"

Usage :
    python indexer_kb.py                 # indexe (incrémental) tout depuis kb_docs_pdf/
    python indexer_kb.py --reset         # supprime et recrée la collection + le cache
    python indexer_kb.py --test "requête de test"
"""

import os
import re
import sys
import json
import pickle
import hashlib
import argparse
import unicodedata
from pathlib import Path
from datetime import datetime, date as _date

import fitz  # PyMuPDF
import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import (
    PointStruct, VectorParams, Distance,
    Filter, FieldCondition, MatchValue,
)

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────
KB_DOCS_DIR      = Path(os.getenv("KB_DOCS_DIR", str(Path(__file__).parent / "kb_docs_pdf")))
KB_QDRANT_PATH   = os.getenv("KB_QDRANT_DB_PATH", str(Path(__file__).parent / "kb_qdrant_db"))
KB_COLLECTION    = os.getenv("KB_COLLECTION_NAME", "kb_erp")
EMBED_MODEL      = os.getenv("MEM0_EMBED_MODEL", "nomic-embed-text")

CHUNK_MAX_CHARS  = 900
CHUNK_OVERLAP    = 150
OCR_MIN_CHARS    = 30          # sous ce seuil, une page est considérée "scan" → OCR
OCR_LANG         = os.getenv("KB_OCR_LANG", "fra")
OCR_DPI          = 200

BM25_INDEX_PATH  = Path(__file__).parent / "kb_bm25_index.pkl"
CACHE_STATE_PATH = Path(__file__).parent / "kb_index_state.json"

_DOC_TYPES_CONNUS = [
    "fiche_article", "commande_email", "note_crm", "reclamation_sav",
    "facture", "contrat", "procedure", "relance_commerciale",
    "recouvrement", "autre",
]

# ─────────────────────────────────────────────────────────────────────
# LLM (Groq) — réutilisé pour deviner doc_type quand le dossier ne le dit pas
# ─────────────────────────────────────────────────────────────────────
GROQ_URL   = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1")
GROQ_KEY   = (os.getenv("GROQ_KEY", "") or "").strip()
GROQ_MODEL = os.getenv("GROQ_FAST", "llama-3.1-8b-instant")

_llm_groq = None
if GROQ_KEY:
    try:
        from langchain_openai import ChatOpenAI
        _llm_groq = ChatOpenAI(model=GROQ_MODEL, temperature=0, api_key=GROQ_KEY, base_url=GROQ_URL)
    except Exception:
        _llm_groq = None


def _classifier_doc_type_llm(extrait: str) -> str:
    if _llm_groq is None:
        return "inconnu"
    try:
        prompt = (
            "Classe ce document dans une seule catégorie parmi : "
            + ", ".join(_DOC_TYPES_CONNUS)
            + f".\nExtrait du document :\n{extrait[:1000]}\n"
            "Réponds uniquement avec le mot de la catégorie, rien d'autre."
        )
        r = _llm_groq.invoke(prompt)
        val = (r.content or "").strip().lower().split()[0].strip(".,;:")
        return val if val in _DOC_TYPES_CONNUS else "autre"
    except Exception:
        return "inconnu"


# ─────────────────────────────────────────────────────────────────────
# TOKENISATION (BM25) — inchangé
# ─────────────────────────────────────────────────────────────────────
_RX_CODE_ERP = re.compile(r"\b[A-Za-z]{2,6}-?\d{2,6}\b")


def _normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFD", texte)
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def _tokeniser(texte: str) -> list[str]:
    texte_norm = _normaliser(texte.lower())
    mots = re.findall(r"[a-z0-9]{2,}", texte_norm)
    codes = [c.upper().replace("-", "") for c in _RX_CODE_ERP.findall(texte)]
    return mots + codes + codes


# ─────────────────────────────────────────────────────────────────────
# EXTRACTION PDF (texte natif + fallback OCR)
# ─────────────────────────────────────────────────────────────────────
_RX_CODE_CLIENT  = re.compile(r"\bCLI\d{2,}\b", re.IGNORECASE)
_RX_CODE_FOUR    = re.compile(r"\bFOUR\d{2,}\b", re.IGNORECASE)
_RX_CODE_ARTICLE = re.compile(r"\bAR-?\d{2,}\b", re.IGNORECASE)
_RX_DATE         = re.compile(r"\b(\d{2})[/\-](\d{2})[/\-](\d{4})\b")


def _hash_fichier(chemin: Path) -> str:
    h = hashlib.md5()
    h.update(chemin.read_bytes())
    return h.hexdigest()


def _ocr_page(chemin_pdf: Path, numero_page: int) -> str:
    """OCR d'une seule page (1-indexée) via pdf2image + pytesseract."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
        images = convert_from_path(
            str(chemin_pdf), dpi=OCR_DPI,
            first_page=numero_page, last_page=numero_page,
        )
        if not images:
            return ""
        return pytesseract.image_to_string(images[0], lang=OCR_LANG).strip()
    except Exception as e:
        print(f"   ⚠️  [OCR] Échec page {numero_page} de {chemin_pdf.name} : {e}")
        return ""


def _nettoyer_entetes_repetes(pages: list[dict]) -> list[dict]:
    """Supprime les lignes identiques répétées sur (presque) toutes les pages
    (en-têtes/pieds de page de type 'Société XYZ - Confidentiel - p.3')."""
    if len(pages) < 3:
        return pages
    compteur: dict[str, int] = {}
    for p in pages:
        for ligne in {l.strip() for l in p["texte"].splitlines() if l.strip()}:
            compteur[ligne] = compteur.get(ligne, 0) + 1
    seuil = max(3, int(len(pages) * 0.6))
    lignes_parasites = {l for l, n in compteur.items() if n >= seuil and len(l) < 100}
    if not lignes_parasites:
        return pages
    for p in pages:
        p["texte"] = "\n".join(
            l for l in p["texte"].splitlines() if l.strip() not in lignes_parasites
        ).strip()
    return pages


def extraire_texte_pdf(chemin_pdf: Path) -> list[dict]:
    """Retourne une liste de {page: int, texte: str}, une entrée par page non vide."""
    pages = []
    try:
        doc = fitz.open(chemin_pdf)
    except Exception as e:
        print(f"   ❌ [PDF] Impossible d'ouvrir {chemin_pdf.name} : {e}")
        return []

    for i, page in enumerate(doc, start=1):
        texte = page.get_text("text").strip()
        if len(texte) < OCR_MIN_CHARS:
            texte_ocr = _ocr_page(chemin_pdf, i)
            if len(texte_ocr) > len(texte):
                texte = texte_ocr
        if texte:
            pages.append({"page": i, "texte": texte})
    doc.close()

    return _nettoyer_entetes_repetes(pages)


def deviner_metadata(chemin_pdf: Path, pages: list[dict]) -> dict:
    """Devine doc_type / code_client / ref_article / date à partir du chemin
    (dossier parent), du nom de fichier, puis du contenu texte, avec un
    recours LLM si rien n'a été trouvé pour doc_type."""
    dossier_parent = chemin_pdf.parent.name.lower()
    nom_fichier    = chemin_pdf.stem

    doc_type = dossier_parent if dossier_parent in _DOC_TYPES_CONNUS else None

    texte_complet = " ".join(p["texte"] for p in pages[:2])  # 1-2 premières pages suffisent
    texte_pour_regex = f"{nom_fichier} {texte_complet}"

    m_client  = _RX_CODE_CLIENT.search(texte_pour_regex)
    m_fourn   = _RX_CODE_FOUR.search(texte_pour_regex)
    m_article = _RX_CODE_ARTICLE.search(texte_pour_regex)
    m_date    = _RX_DATE.search(texte_pour_regex)

    code_client = None
    if m_client:
        code_client = m_client.group(0).upper()
    elif m_fourn:
        code_client = m_fourn.group(0).upper()

    ref_article = m_article.group(0).upper().replace("AR-", "AR") if m_article else None
    date_iso = None
    if m_date:
        j, mo, an = m_date.groups()
        date_iso = f"{an}-{mo}-{j}"

    if not doc_type:
        doc_type = _classifier_doc_type_llm(texte_complet) if texte_complet else "inconnu"

    return {
        "doc_type":    doc_type,
        "code_client": code_client,
        "ref_article": ref_article,
        "date":        date_iso,
    }


def chunker_pages(pages: list[dict], max_chars: int = CHUNK_MAX_CHARS,
                   overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Découpe chaque page en chunks avec overlap ; conserve le numéro de page."""
    chunks = []
    for p in pages:
        texte = p["texte"]
        if len(texte) <= max_chars:
            chunks.append({"texte": texte, "page": p["page"]})
            continue
        start = 0
        while start < len(texte):
            fin = min(start + max_chars, len(texte))
            morceau = texte[start:fin].strip()
            if morceau:
                chunks.append({"texte": morceau, "page": p["page"]})
            if fin >= len(texte):
                break
            start = fin - overlap
    return chunks


# ─────────────────────────────────────────────────────────────────────
# EMBEDDING
# ─────────────────────────────────────────────────────────────────────
def embed(texte: str) -> list[float]:
    r = ollama.embeddings(model=EMBED_MODEL, prompt=texte)
    return r["embedding"]


def _verifier_ollama() -> int:
    try:
        v = embed("test")
        return len(v)
    except Exception as e:
        print(f"❌ Ollama/embeddings indisponible : {e}")
        print(f"   Vérifie que 'ollama serve' tourne et que le modèle est tiré :")
        print(f"   ollama pull {EMBED_MODEL}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────
# CACHE (fichier → hash) pour indexation incrémentale
# ─────────────────────────────────────────────────────────────────────
def _charger_cache_state() -> dict:
    if CACHE_STATE_PATH.exists():
        try:
            return json.loads(CACHE_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _sauvegarder_cache_state(state: dict):
    CACHE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# INDEXATION PRINCIPALE
# ─────────────────────────────────────────────────────────────────────
def indexer(reset: bool = False):
    if not KB_DOCS_DIR.exists():
        print(f"❌ Dossier introuvable : {KB_DOCS_DIR}")
        print("   Placez vos PDF dans ce dossier (organisés ou non par sous-dossier doc_type).")
        sys.exit(1)

    fichiers = sorted(KB_DOCS_DIR.glob("**/*.pdf"))
    if not fichiers:
        print(f"❌ Aucun fichier .pdf trouvé dans {KB_DOCS_DIR}")
        sys.exit(1)

    print(f"🔎 Vérification d'Ollama ({EMBED_MODEL})...")
    dim = _verifier_ollama()
    print(f"   ✅ Dimension des embeddings : {dim}")

    client = QdrantClient(path=KB_QDRANT_PATH)
    collections = [c.name for c in client.get_collections().collections]

    cache_state = {} if reset else _charger_cache_state()

    if reset:
        if KB_COLLECTION in collections:
            print(f"🗑️  Suppression de la collection existante '{KB_COLLECTION}'...")
            client.delete_collection(KB_COLLECTION)
            collections.remove(KB_COLLECTION)
        if BM25_INDEX_PATH.exists():
            BM25_INDEX_PATH.unlink()
        if CACHE_STATE_PATH.exists():
            CACHE_STATE_PATH.unlink()

    if KB_COLLECTION not in collections:
        print(f"📦 Création de la collection '{KB_COLLECTION}' (dim={dim})...")
        client.create_collection(
            collection_name=KB_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    # Récupère l'état BM25 existant (pour fusion incrémentale) s'il y en a un
    bm25_ids: list[int] = []
    bm25_tokens: list[list[str]] = []
    bm25_payloads: list[dict] = []
    if BM25_INDEX_PATH.exists() and not reset:
        with open(BM25_INDEX_PATH, "rb") as f:
            old = pickle.load(f)
            bm25_ids       = old.get("ids", [])
            bm25_tokens    = old.get("tokens", [])
            bm25_payloads  = old.get("payloads", [])

    # point_id : on repart après le max existant pour ne pas collisionner
    point_id = (max(bm25_ids) + 1) if bm25_ids else 0

    print(f"📚 {len(fichiers)} PDF détecté(s) — vérification du cache...")
    a_traiter, inchanges = [], 0
    for f in fichiers:
        h = _hash_fichier(f)
        cle = str(f.relative_to(KB_DOCS_DIR))
        if cache_state.get(cle) == h:
            inchanges += 1
            continue
        a_traiter.append((f, cle, h))

    print(f"   {inchanges} fichier(s) déjà indexé(s) et inchangé(s) → ignoré(s)")
    print(f"   {len(a_traiter)} fichier(s) à (ré)indexer")

    if not a_traiter:
        print("✅ Rien à faire, tout est déjà à jour.")
        return

    points = []
    stats = {}

    for chemin_pdf, cle_cache, h in a_traiter:
        print(f"   📄 {chemin_pdf.name} ...")

        # Si ce fichier avait déjà été indexé sous un hash différent,
        # on retire ses anciens chunks des listes BM25 avant de le réinsérer.
        if cle_cache in cache_state:
            keep_idx = [i for i, pl in enumerate(bm25_payloads) if pl.get("source_path") != cle_cache]
            bm25_ids      = [bm25_ids[i] for i in keep_idx]
            bm25_tokens   = [bm25_tokens[i] for i in keep_idx]
            bm25_payloads = [bm25_payloads[i] for i in keep_idx]

        pages = extraire_texte_pdf(chemin_pdf)
        if not pages:
            print(f"      ⚠️  Aucun texte extrait (PDF vide ou OCR indisponible) → ignoré")
            continue

        metadata = deviner_metadata(chemin_pdf, pages)
        doc_type = metadata["doc_type"]
        stats[doc_type] = stats.get(doc_type, 0) + 1

        chunks = chunker_pages(pages)
        for i, chunk in enumerate(chunks):
            vecteur = embed(chunk["texte"])
            payload = {
                "texte":        chunk["texte"],
                "page":         chunk["page"],
                "doc_type":     doc_type,
                "ref_article":  metadata.get("ref_article") or None,
                "code_client":  metadata.get("code_client") or None,
                "date":         metadata.get("date") or None,
                "source_file":  chemin_pdf.name,
                "source_path":  cle_cache,
                "chunk_index":  i,
            }
            points.append(PointStruct(id=point_id, vector=vecteur, payload=payload))

            texte_pour_bm25 = " ".join(filter(None, [
                chunk["texte"], metadata.get("ref_article") or "", metadata.get("code_client") or "",
            ]))
            bm25_ids.append(point_id)
            bm25_tokens.append(_tokeniser(texte_pour_bm25))
            bm25_payloads.append(payload)

            point_id += 1

        cache_state[cle_cache] = h

    if not points:
        print("⚠️  Aucun nouveau point à indexer (extraction vide sur tous les fichiers).")
        _sauvegarder_cache_state(cache_state)
        return

    print(f"⬆️  Envoi de {len(points)} nouveaux chunks vers Qdrant...")
    BATCH = 64
    for i in range(0, len(points), BATCH):
        client.upsert(collection_name=KB_COLLECTION, points=points[i:i + BATCH])

    print("🔤 Reconstruction de l'index BM25...")
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi(bm25_tokens)
    with open(BM25_INDEX_PATH, "wb") as f:
        pickle.dump({"bm25": bm25, "ids": bm25_ids, "tokens": bm25_tokens,
                     "payloads": bm25_payloads}, f)

    _sauvegarder_cache_state(cache_state)

    print("\n✅ Indexation terminée :")
    for doc_type, nb in stats.items():
        print(f"   {doc_type:<20} : {nb} fichier(s) (re)traité(s)")
    print(f"   Total chunks ajoutés  : {len(points)}")
    print(f"   Base Qdrant           : {KB_QDRANT_PATH} (collection '{KB_COLLECTION}')")


# ─────────────────────────────────────────────────────────────────────
# BM25 — chargement paresseux (cache module-level)
# ─────────────────────────────────────────────────────────────────────
_bm25_cache: dict | None = None


def _charger_bm25() -> dict | None:
    global _bm25_cache
    if _bm25_cache is not None:
        return _bm25_cache
    if not BM25_INDEX_PATH.exists():
        print("   ⚠️  Index BM25 introuvable — lancez : python indexer_kb.py --reset")
        return None
    with open(BM25_INDEX_PATH, "rb") as f:
        _bm25_cache = pickle.load(f)
    return _bm25_cache


def _rrf_fusion(rank_lists: list[list[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranks in rank_lists:
        for rang, doc_id in enumerate(ranks, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rang)
    return scores


def _boost_fraicheur_doctype(
    payload: dict,
    doc_type_boost: dict[str, float] | None,
    freshness_halflife_days: float | None,
) -> float:
    facteur = 1.0
    if doc_type_boost:
        facteur *= doc_type_boost.get(payload.get("doc_type", ""), 1.0)
    if freshness_halflife_days and payload.get("date"):
        try:
            d = datetime.strptime(payload["date"], "%Y-%m-%d").date()
            age_jours = (_date.today() - d).days
            recence = 0.5 ** (age_jours / freshness_halflife_days)
            facteur *= (0.7 + 0.3 * recence)
        except ValueError:
            pass
    return facteur


# ─────────────────────────────────────────────────────────────────────
# RECHERCHE — interface inchangée pour mcp_knowledge_base.py
# ─────────────────────────────────────────────────────────────────────
def rechercher(
    requete: str,
    doc_type: str | None = None,
    code_client: str | None = None,
    ref_article: str | None = None,
    top_k: int = 5,
    score_min: float = 0.0,
    hybride: bool = True,
    n_candidats: int = 30,
    doc_type_boost: dict[str, float] | None = None,
    freshness_halflife_days: float | None = None,
):
    """
    Recherche hybride vecteur (cosinus) + lexical (BM25), fusionnée par RRF.
    Renvoie une liste de dicts : texte, doc_type, code_client, ref_article,
    source_file, page, score, dans_vecteur, dans_bm25.
    """
    client = QdrantClient(path=KB_QDRANT_PATH)
    vecteur = embed(requete)

    conditions = []
    if doc_type:
        conditions.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type)))
    if code_client:
        conditions.append(FieldCondition(key="code_client", match=MatchValue(value=code_client)))
    if ref_article:
        conditions.append(FieldCondition(key="ref_article", match=MatchValue(value=ref_article)))
    filtre = Filter(must=conditions) if conditions else None

    resp_vec = client.query_points(
        collection_name=KB_COLLECTION,
        query=vecteur,
        query_filter=filtre,
        limit=n_candidats,
    )
    ranks_vecteur = [p.id for p in resp_vec.points]
    payloads_par_id = {p.id: p.payload for p in resp_vec.points}

    if not hybride:
        resultats = [
            {
                "texte": p.payload.get("texte", ""), "doc_type": p.payload.get("doc_type", ""),
                "code_client": p.payload.get("code_client"), "ref_article": p.payload.get("ref_article"),
                "source_file": p.payload.get("source_file"), "page": p.payload.get("page"),
                "score": p.score,
            }
            for p in resp_vec.points if p.score >= score_min
        ][:top_k]
        return resultats

    bm25_data = _charger_bm25()
    ranks_bm25 = []
    if bm25_data:
        tokens_requete = _tokeniser(requete)
        scores_bm25 = bm25_data["bm25"].get_scores(tokens_requete)
        classement = sorted(range(len(scores_bm25)), key=lambda i: scores_bm25[i], reverse=True)
        for idx in classement:
            if scores_bm25[idx] <= 0:
                continue
            pid = bm25_data["ids"][idx]
            pl  = bm25_data["payloads"][idx]
            if doc_type and pl.get("doc_type") != doc_type:
                continue
            if code_client and pl.get("code_client") != code_client:
                continue
            if ref_article and pl.get("ref_article") != ref_article:
                continue
            ranks_bm25.append(pid)
            payloads_par_id.setdefault(pid, pl)
            if len(ranks_bm25) >= n_candidats:
                break

    scores_fusion = _rrf_fusion([ranks_vecteur, ranks_bm25])

    for pid in scores_fusion:
        pl = payloads_par_id.get(pid, {})
        scores_fusion[pid] *= _boost_fraicheur_doctype(pl, doc_type_boost, freshness_halflife_days)

    classement_final = sorted(scores_fusion.items(), key=lambda kv: kv[1], reverse=True)

    resultats = []
    for pid, score in classement_final:
        if score < score_min:
            continue
        pl = payloads_par_id.get(pid, {})
        resultats.append({
            "texte":        pl.get("texte", ""),
            "doc_type":     pl.get("doc_type", ""),
            "code_client":  pl.get("code_client"),
            "ref_article":  pl.get("ref_article"),
            "source_file":  pl.get("source_file"),
            "page":         pl.get("page"),
            "score":        round(score, 4),
            "dans_vecteur": pid in ranks_vecteur,
            "dans_bm25":    pid in ranks_bm25,
        })
        if len(resultats) >= top_k:
            break

    return resultats


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Recrée la collection et le cache depuis zéro")
    parser.add_argument("--test", type=str, default=None, help="Lance une recherche de test après indexation")
    args = parser.parse_args()

    indexer(reset=args.reset)

    requete_test = args.test or "conditions négociées pour un client"
    print(f"\n🔍 Test de recherche : « {requete_test} »")
    resultats = rechercher(requete_test, top_k=3)
    if not resultats:
        print("   Aucun résultat (score trop faible ou collection vide).")
    for r in resultats:
        page_txt = f" p.{r['page']}" if r.get("page") else ""
        print(f"   [{r['score']:.4f}] ({r['doc_type']}) {r['source_file']}{page_txt}")
        print(f"        {r['texte'][:120]}...")


if __name__ == "__main__":
    main()