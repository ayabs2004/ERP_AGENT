import asyncio
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from orchestrateur_general import (
    mcp_pool, _warmup_ollama, ENABLE_VANNA, ENABLE_GLINER, ENABLE_MEM0,
    _get_vanna_async, _get_gliner_async, _get_mem0_async,
    _construire_graphe, _est_oui, _est_non, _executer_suggestion,
    _resoudre_references, decouper_demande_composite, _fusionner_demandes,
    _etat_initial, _extraire_dernier_document, _safe_str,
    formater_alertes_persistantes,
)

logger = logging.getLogger("sage.erp.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

graphe = None

# ── Dossier public pour servir les PDF générés ──────────────────────
PDF_PUBLIC_DIR = "static/pdf"
os.makedirs(PDF_PUBLIC_DIR, exist_ok=True)


def _exposer_pdf(pdf_path: str) -> Optional[str]:
    """Copie le PDF généré dans le dossier public et renvoie son URL relative."""
    if not pdf_path or not os.path.exists(pdf_path):
        return None
    nom = os.path.basename(pdf_path)
    dest = os.path.join(PDF_PUBLIC_DIR, nom)
    try:
        shutil.copy(pdf_path, dest)
        return f"/static/pdf/{nom}"
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graphe
    logger.info("Chargement parallèle des composants")
    init_tasks = [mcp_pool.init(), _warmup_ollama()]
    if ENABLE_VANNA:  init_tasks.append(_get_vanna_async())
    if ENABLE_GLINER: init_tasks.append(_get_gliner_async())
    if ENABLE_MEM0:   init_tasks.append(_get_mem0_async())

    await asyncio.gather(*init_tasks, return_exceptions=True)
    graphe = _construire_graphe()
    logger.info("API prête")
    yield
    logger.info("Arrêt de l'API")
    await mcp_pool.close()


app = FastAPI(title="Sage ERP Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sert les PDF générés sous /static/pdf/...
app.mount("/static", StaticFiles(directory="static"), name="static")

sessions: Dict[str, Dict[str, Any]] = {}
dernieres_demandes: Dict[str, str] = {}
SESSION_TTL_SECONDS = float(os.getenv("SESSION_TTL_SECONDS", "1800"))
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "2000"))


def _cleanup_sessions(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [sid for sid, state in sessions.items() if now - float(state.get("_last_access", now)) > SESSION_TTL_SECONDS]
    for sid in expired:
        sessions.pop(sid, None)
        dernieres_demandes.pop(sid, None)
    if len(sessions) > MAX_SESSIONS:
        oldest = sorted(sessions.items(), key=lambda item: float(item[1].get("_last_access", now)))[: len(sessions) - MAX_SESSIONS]
        for sid, _ in oldest:
            sessions.pop(sid, None)
            dernieres_demandes.pop(sid, None)


def _get_or_create_session(session_id: str) -> tuple[str, Dict[str, Any]]:
    _cleanup_sessions()
    normalized = (session_id or "").strip() or f"anon-{uuid.uuid4().hex[:8]}"
    state = sessions.get(normalized)
    if state is None:
        state = {
            "dernier_code_client":   "",
            "dernier_ref_article":   "",
            "dernier_quantite":      0.0,
            "dernier_nom_client":    "",
            "dernier_document":      {},
            "dernier_num_piece":     "",
            "dernier_type_doc":      "",
            "suggestion_en_attente": {},
            "attente_complements":   False,
            "pending_document":      {},
            "document_draft":        {},
            "statut_draft":          "",
            "alertes_persistantes":  [],
            "_last_access":         time.time(),
        }
        sessions[normalized] = state
        dernieres_demandes[normalized] = ""
    state["_last_access"] = time.time()
    return normalized, state


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    responses: List[str]
    suggestions: List[str]
    draft_status: str = ""
    pdf_url: Optional[str] = None
    alerts: List[str] = []
    attente_complements: bool = False


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    session_id, contexte_session = _get_or_create_session(req.session_id)
    demande = req.message.strip()
    demande_precedente = dernieres_demandes.get(session_id, "")

    if not demande:
        return ChatResponse(responses=["Demande vide."], suggestions=[])

    sugg = contexte_session.get("suggestion_en_attente", {})
    if sugg and (_est_oui(demande) or _est_non(demande)):
        if _est_oui(demande):
            contexte_session["pdf_path"] = ""
            reponse_sugg = await _executer_suggestion(sugg, contexte_session)
            contexte_session["suggestion_en_attente"] = {}
            alertes_txt = formater_alertes_persistantes(contexte_session)
            pdf_url = _exposer_pdf(contexte_session.get("pdf_path", ""))
            return ChatResponse(
                responses=[reponse_sugg],
                suggestions=[],
                draft_status=contexte_session.get("statut_draft", ""),
                pdf_url=pdf_url,
                alerts=[alertes_txt] if alertes_txt else [],
                attente_complements=contexte_session.get("attente_complements", False),
            )
        else:
            contexte_session["suggestion_en_attente"] = {}
            return ChatResponse(responses=["🛑 Suggestion annulée."], suggestions=[])

    try:
        demande_resolue = _resoudre_references(demande, contexte_session.get("dernier_document", {}))
        sous_demandes = await decouper_demande_composite(demande_resolue)
        reponses_multi = []
        suggestions = []
        dernier_pdf_url: Optional[str] = None

        for sous_d in sous_demandes:
            demande_courante = sous_d["demande"]

            if (demande_precedente and demande_courante.lower() in ("oui", "o", "ok", "yes", "y") and not sugg):
                demande_courante = _fusionner_demandes(demande_precedente, demande_courante)

            etat = _etat_initial(demande_courante, contexte_session)

            if contexte_session.get("attente_complements"):
                etat["attente_complements"] = True
                etat["pending_document"] = contexte_session.get("pending_document", {})

            if contexte_session.get("document_draft"):
                etat["document_draft"] = contexte_session["document_draft"]
                etat["statut_draft"] = contexte_session.get("statut_draft", "")

            try:
                final_state = await graphe.ainvoke(etat)
            except Exception as e:
                logger.exception("Erreur pendant l'exécution du graphe pour session %s", session_id)
                final_state = {**etat, "reponse_finale": "❌ Une erreur système s'est produite. Veuillez réessayer."}

            reponse = final_state.get("reponse_finale", "⚠️  Aucune réponse.")

            # ── Persistance du cycle draft/preview/confirm ──
            contexte_session["document_draft"] = final_state.get("document_draft", {})
            contexte_session["statut_draft"] = final_state.get("statut_draft", "")

            # ── PDF généré pendant ce tour ──
            pdf_url = _exposer_pdf(final_state.get("pdf_path", ""))
            if pdf_url:
                dernier_pdf_url = pdf_url

            if final_state.get("code_client"):
                contexte_session["dernier_code_client"] = final_state["code_client"]
            if final_state.get("ref_article"):
                contexte_session["dernier_ref_article"] = final_state["ref_article"]
            if final_state.get("quantite", 0) > 0:
                contexte_session["dernier_quantite"] = final_state["quantite"]

            _ACTIONS_AVEC_CLIENT = {
                "FICHE_CLIENT", "STATUT_CLIENT", "TOUTES_FACTURES_CLIENT",
                "GENERER_DOC", "TRANSFORMER_DOC", "CREER_AVOIR", "REGLEMENT",
                "MODIFIER_STATUT", "CREER_CLIENT", "WORKFLOW_COMMANDE",
            }
            if final_state.get("nom_client_brut") and final_state.get("action", "") in _ACTIONS_AVEC_CLIENT:
                contexte_session["dernier_nom_client"] = final_state["nom_client_brut"]
            elif final_state.get("action", "") not in _ACTIONS_AVEC_CLIENT:
                contexte_session["dernier_nom_client"] = ""

            doc_extrait = _extraire_dernier_document(final_state)
            if doc_extrait and doc_extrait.get("type_doc", "") not in ("OF", "BF"):
                contexte_session["dernier_document"] = doc_extrait
            elif not doc_extrait:
                if final_state.get("action") not in ("GENERER_DOC",):
                    contexte_session["dernier_document"] = {}

            if doc_extrait:
                num_p = doc_extrait.get("num_piece", "")
                type_p = doc_extrait.get("type_doc", "")
                if num_p:
                    contexte_session["dernier_num_piece"] = num_p
                    contexte_session["dernier_type_doc"] = type_p

            if not final_state.get("ambigue"):
                contexte_session["dernier_quantite"] = 0.0

            sugg_nouvelle = final_state.get("suggestion_en_attente", {})
            if sugg_nouvelle:
                contexte_session["suggestion_en_attente"] = sugg_nouvelle

            if final_state.get("attente_complements"):
                contexte_session["attente_complements"] = True
                contexte_session["pending_document"] = final_state.get("pending_document", {})
            else:
                contexte_session["attente_complements"] = False
                contexte_session["pending_document"] = {}

            if final_state.get("ambigue"):
                dernieres_demandes[session_id] = demande_courante
            else:
                dernieres_demandes[session_id] = ""

            reponses_multi.append(reponse)
            if sugg_nouvelle:
                suggestions.append(sugg_nouvelle.get("description", ""))

        # ── Alertes persistantes (ex : BF requis pour OF) ──
        alertes_txt = formater_alertes_persistantes(contexte_session)
        alerts = [alertes_txt] if alertes_txt else []

        return ChatResponse(
            responses=reponses_multi,
            suggestions=suggestions,
            draft_status=contexte_session.get("statut_draft", ""),
            pdf_url=dernier_pdf_url,
            alerts=alerts,
            attente_complements=contexte_session.get("attente_complements", False),
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))