import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import traceback
from typing import Dict, Any, List

# Imports depuis l'orchestrateur général
from orchestrateur_general import (
    mcp_pool, _warmup_ollama, ENABLE_VANNA, ENABLE_GLINER, ENABLE_MEM0,
    _get_vanna_async, _get_gliner_async, _get_mem0_async,
    _construire_graphe, _est_oui, _est_non, _executer_suggestion,
    _resoudre_references, decouper_demande_composite, _fusionner_demandes,
    _etat_initial, _extraire_dernier_document, _safe_str
)

# Initialisation du graphe
graphe = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global graphe
    print("⏳ [API] Chargement parallèle des composants...")
    init_tasks = [mcp_pool.init(), _warmup_ollama()]
    if ENABLE_VANNA:  init_tasks.append(_get_vanna_async())
    if ENABLE_GLINER: init_tasks.append(_get_gliner_async())
    if ENABLE_MEM0:   init_tasks.append(_get_mem0_async())
    
    await asyncio.gather(*init_tasks, return_exceptions=True)
    graphe = _construire_graphe()
    print("✅ [API] Prêt.")
    yield
    print("👋 [API] Arrêt en cours...")
    await mcp_pool.close()

app = FastAPI(title="Sage ERP Agent API", lifespan=lifespan)

# CORS pour autoriser le frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En développement
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stockage des sessions en mémoire
sessions: Dict[str, Dict[str, Any]] = {}
dernieres_demandes: Dict[str, str] = {}

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    responses: List[str]
    suggestions: List[str]

@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    session_id = req.session_id
    demande = req.message.strip()

    if session_id not in sessions:
        sessions[session_id] = {
            "dernier_code_client":   "",
            "dernier_ref_article":   "",
            "dernier_quantite":      0.0,
            "dernier_nom_client":    "",
            "dernier_document":      {},
            "dernier_num_piece":     "",
            "dernier_type_doc":      "",
            "suggestion_en_attente": {},
            "attente_complements":   False,
            "pending_document":      {}
        }
        dernieres_demandes[session_id] = ""

    contexte_session = sessions[session_id]
    demande_precedente = dernieres_demandes.get(session_id, "")

    if not demande:
        return ChatResponse(responses=["Demande vide."], suggestions=[])

    sugg = contexte_session.get("suggestion_en_attente", {})
    if sugg and (_est_oui(demande) or _est_non(demande)):
        if _est_oui(demande):
            reponse_sugg = await _executer_suggestion(sugg, contexte_session)
            contexte_session["suggestion_en_attente"] = {}
            return ChatResponse(responses=[reponse_sugg], suggestions=[])
        else:
            contexte_session["suggestion_en_attente"] = {}
            return ChatResponse(responses=["🛑 Suggestion annulée."], suggestions=[])

    try:
        demande_resolue = _resoudre_references(demande, contexte_session.get("dernier_document", {}))
        sous_demandes = await decouper_demande_composite(demande_resolue)
        reponses_multi = []
        suggestions = []

        for sous_d in sous_demandes:
            demande_courante = sous_d["demande"]

            if (demande_precedente and demande_courante.lower() in ("oui","o","ok","yes","y") and not sugg):
                demande_courante = _fusionner_demandes(demande_precedente, demande_courante)

            etat = _etat_initial(demande_courante, contexte_session)
            
            if contexte_session.get("attente_complements"):
                etat["attente_complements"] = True
                etat["pending_document"] = contexte_session.get("pending_document", {})
                
            try:
                final_state = await graphe.ainvoke(etat)
            except Exception as e:
                final_state = {**etat, "reponse_finale": f"❌ Erreur système : {_safe_str(e)}"}

            reponse = final_state.get("reponse_finale", "⚠️  Aucune réponse.")

            # Mise à jour du contexte
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
            if final_state.get("nom_client_brut") and final_state.get("action","") in _ACTIONS_AVEC_CLIENT:
                contexte_session["dernier_nom_client"] = final_state["nom_client_brut"]
            elif final_state.get("action","") not in _ACTIONS_AVEC_CLIENT:
                contexte_session["dernier_nom_client"] = ""

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

        return ChatResponse(responses=reponses_multi, suggestions=suggestions)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
