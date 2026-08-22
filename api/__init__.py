import asyncio
import logging
import os
import shutil
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
load_dotenv()
from api.orchestrateur_general import (
    mcp_pool, _warmup_ollama, ENABLE_VANNA, ENABLE_GLINER, ENABLE_MEM0,
    ENABLE_SEMANTIC_CLASSIFIER,
    _get_vanna_async, _get_gliner_async, _get_mem0_async,
    _construire_graphe, _est_oui, _est_non, _executer_suggestion,
    _resoudre_references, decouper_demande_composite, _fusionner_demandes,
    _etat_initial, _extraire_dernier_document, _safe_str,
    formater_alertes_persistantes, traiter_commande_speciale,
)
from classification.semantic_classifier import warmup_semantic_classifier
from auth import (
    authentifier, create_access_token, get_current_user, CurrentUser,
)

logger = logging.getLogger("sage.erp.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

graphe = None

# ── Dossier de stockage des PDF générés (NON servi en accès public) ──
# Auparavant ce dossier était monté directement en HTTP public
# (app.mount("/static", ...)) : quiconque devinait/récupérait un nom de
# fichier pouvait télécharger n'importe quel document généré, sans la
# moindre authentification. Désormais il n'est accessible que via
# GET /api/files/pdf/{filename}, protégé par JWT (voir plus bas).
PDF_PUBLIC_DIR = "static/pdf"
os.makedirs(PDF_PUBLIC_DIR, exist_ok=True)

# nom_fichier -> username propriétaire (best-effort, en mémoire ; un admin
# garde toujours accès à tout, cf. endpoint /api/files/pdf/{filename})
_pdf_owners: Dict[str, str] = {}


def _exposer_pdf(pdf_path: str, username: str = "") -> Optional[str]:
    """Copie le PDF généré dans le dossier de stockage et renvoie l'URL de
    l'endpoint protégé permettant de le télécharger (nécessite un JWT valide)."""
    if not pdf_path or not os.path.exists(pdf_path):
        return None
    nom = os.path.basename(pdf_path)
    dest = os.path.join(PDF_PUBLIC_DIR, nom)
    try:
        shutil.copy(pdf_path, dest)
        if username:
            _pdf_owners[nom] = username
        return f"/api/files/pdf/{nom}"
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graphe
    logger.info("Chargement parallèle des composants")
    init_tasks = [mcp_pool.init(), _warmup_ollama()]
    if ENABLE_SEMANTIC_CLASSIFIER: init_tasks.append(warmup_semantic_classifier())
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

# ── Authentification : login utilisateur + JWT ────────────────────────
# L'ancien système (un seul token API partagé, recopié en clair dans le
# frontend et donc visible par quiconque ouvre les DevTools) est remplacé
# par de vrais comptes : voir auth.py.
#
#   POST /api/auth/login   { username, password }  → { access_token, ... }
#   Puis chaque requête protégée envoie : Authorization: Bearer <access_token>
#
# La logique de vérification (get_current_user) est importée depuis auth.py.


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
    role: str


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    utilisateur = authentifier(req.username, req.password)
    if utilisateur is None:
        # Message volontairement générique : ne pas révéler si c'est
        # l'identifiant ou le mot de passe qui est incorrect.
        raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")
    token, expires_in = create_access_token(utilisateur["username"], utilisateur["role"])
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        username=utilisateur["username"],
        role=utilisateur["role"],
    )


class MeResponse(BaseModel):
    username: str
    role: str


@app.get("/api/auth/me", response_model=MeResponse)
async def me(user: CurrentUser = Depends(get_current_user)):
    return MeResponse(username=user["username"], role=user["role"])


# ── PDF générés : servis via un endpoint protégé (plus de dossier public) ──
@app.get("/api/files/pdf/{filename}")
async def telecharger_pdf(filename: str, user: CurrentUser = Depends(get_current_user)):
    # Anti path-traversal : on ne garde que le nom de fichier, jamais un chemin.
    filename_safe = os.path.basename(filename)
    if filename_safe != filename or not filename_safe:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")

    proprietaire = _pdf_owners.get(filename_safe)
    if proprietaire and proprietaire != user["username"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Ce document ne vous appartient pas.")

    chemin = os.path.join(PDF_PUBLIC_DIR, filename_safe)
    chemin_reel = os.path.realpath(chemin)
    dossier_reel = os.path.realpath(PDF_PUBLIC_DIR)
    if not chemin_reel.startswith(dossier_reel + os.sep):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    if not os.path.isfile(chemin_reel):
        raise HTTPException(status_code=404, detail="Document introuvable.")

    media_type = "application/pdf" if filename_safe.lower().endswith(".pdf") else "application/octet-stream"
    return FileResponse(chemin_reel, media_type=media_type, filename=filename_safe)


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


def _get_or_create_session(session_id: str, username: str) -> tuple[str, Dict[str, Any]]:
    """Les sessions sont désormais namespacées par utilisateur : un session_id
    choisi/deviné côté client ne permet plus de lire l'historique d'un autre
    utilisateur, puisque la clé réelle inclut son identité (vérifiée par JWT)."""
    _cleanup_sessions()
    raw = (session_id or "").strip() or f"anon-{uuid.uuid4().hex[:8]}"
    normalized = f"{username}::{raw}"
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
            "dernier_action_classifiee":    "",
            "derniere_question_classifiee": "",
            "_last_access":         time.time(),
            "statut_confirmation":   "",   # ★ AJOUT
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
    confirmation_status: str = ""
    pdf_url: Optional[str] = None
    alerts: List[str] = []
    attente_complements: bool = False
    # Amélioration #7 : confiance de classification exposée à l'UI, pour
    # que l'utilisateur voie quand l'action a été devinée avec une
    # confiance moyenne et puisse la corriger facilement.
    score_confiance: float = 0.0
    origine_classification: str = ""
    action_buttons: Optional[List[str]] = None  # Nouveau champ pour boutons d'action


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    session_id, contexte_session = _get_or_create_session(req.session_id, user["username"])
    demande = req.message.strip()
    demande_precedente = dernieres_demandes.get(session_id, "")

    if not demande:
        return ChatResponse(responses=["Demande vide."], suggestions=[])

    # Traitement spécial pour la commande "reset" - doit être avant toute autre logique
    if demande.lower() == "reset":
        contexte_session.clear()
        dernieres_demandes[session_id] = ""
        return ChatResponse(
            responses=["🔄 Session réinitialisée avec succès."],
            suggestions=[],
            draft_status="",
            confirmation_status="",
            pdf_url=None,
            alerts=[],
            attente_complements=False,
            score_confiance=0.0,
            origine_classification="",
        )

    # Commandes spéciales partagées (ex: vanna_retrain)
    resp_speciale = await traiter_commande_speciale(demande)
    if resp_speciale:
        return ChatResponse(
            responses=[resp_speciale],
            suggestions=[],
            draft_status="",
            confirmation_status="",
            pdf_url=None,
            alerts=[],
            attente_complements=False,
            score_confiance=0.0,
            origine_classification="",
        )

    sugg = contexte_session.get("suggestion_en_attente", {})
    if sugg:
        piece_ref = (
            sugg.get("params", {}).get("num_br")
            or sugg.get("params", {}).get("num_bl")
            or sugg.get("params", {}).get("num_of")
            or ""
        )
        demande_norm = demande.strip().lower()
        desc_norm = sugg.get("description", "").strip().lower()
        est_confirmation_sugg = (
            _est_oui(demande)
            or demande_norm == desc_norm
            or (piece_ref and piece_ref.lower() in demande_norm)
        )
        if est_confirmation_sugg:
            contexte_session["pdf_path"] = ""
            reponse_sugg = await _executer_suggestion(sugg, contexte_session)
            contexte_session["suggestion_en_attente"] = {}
            alertes_txt = formater_alertes_persistantes(contexte_session)
            pdf_url = _exposer_pdf(contexte_session.get("pdf_path", ""), user["username"])
            return ChatResponse(
                responses=[reponse_sugg],
                suggestions=[],
                draft_status=contexte_session.get("statut_draft", ""),
                pdf_url=pdf_url,
                alerts=[alertes_txt] if alertes_txt else [],
                attente_complements=contexte_session.get("attente_complements", False),
            )
        elif _est_non(demande):
            contexte_session["suggestion_en_attente"] = {}
            return ChatResponse(responses=["🛑 Suggestion annulée."], suggestions=[])
        else:
            contexte_session["suggestion_en_attente"] = {}

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

            # Restore modification state from session
            logger.info(f"🔧 [Session] modification_en_cours in session: {bool(contexte_session.get('modification_en_cours'))}")
            if contexte_session.get("modification_en_cours"):
                etat["modification_en_cours"] = contexte_session["modification_en_cours"]
                logger.info(f"🔧 [Session] Restored modification_en_cours: {etat['modification_en_cours']}")
            if contexte_session.get("creation_article_en_cours"):
                etat["creation_article_en_cours"] = contexte_session["creation_article_en_cours"]
                logger.info(f"🔧 [Session] Restored creation_article_en_cours étape: {etat['creation_article_en_cours'].get('etape')}")
            if contexte_session.get("nomenclature_en_cours"):
                etat["nomenclature_en_cours"] = contexte_session["nomenclature_en_cours"]
                logger.info(f"🔧 [Session] Restored nomenclature_en_cours étape: {etat['nomenclature_en_cours'].get('etape')}")
            if contexte_session.get("modification_nomenclature_en_cours"):
                etat["modification_nomenclature_en_cours"] = contexte_session["modification_nomenclature_en_cours"]
                logger.info(f"🔧 [Session] Restored modification_nomenclature_en_cours étape: {etat['modification_nomenclature_en_cours'].get('etape')}")
            if contexte_session.get("attente_confirmation"):
                etat["attente_confirmation"] = contexte_session["attente_confirmation"]
                logger.info(f"🔧 [Session] Restored attente_confirmation: {etat['attente_confirmation']}")

            if contexte_session.get("document_draft"):
                etat["document_draft"] = contexte_session["document_draft"]
                etat["statut_draft"] = contexte_session.get("statut_draft", "")

            API_GRAPH_TIMEOUT = float(os.getenv("API_GRAPH_TIMEOUT", "90"))
            try:
                final_state = await asyncio.wait_for(graphe.ainvoke(etat), timeout=API_GRAPH_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Timeout lors de l'appel du graphe pour session %s", session_id)
                final_state = {**etat, "reponse_finale": "❌ Le graphe a dépassé le délai d'attente. Veuillez réessayer plus tard."}
            except Exception as e:
                logger.exception("Erreur pendant l'exécution du graphe pour session %s", session_id)
                final_state = {**etat, "reponse_finale": "❌ Une erreur système s'est produite. Veuillez réessayer."}

            reponse = final_state.get("reponse_finale", "⚠️  Aucune réponse.")

            # ── Persistance du cycle modification ──
            if final_state.get("modification_en_cours"):
                contexte_session["modification_en_cours"] = final_state["modification_en_cours"]
                logger.info(f"🔧 [Session] Saved modification_en_cours from final_state: {final_state['modification_en_cours']}")
            else:
                contexte_session["modification_en_cours"] = {}
                logger.info(f"🔧 [Session] Cleared modification_en_cours")

            # ── Persistance du cycle création article ──
            if final_state.get("creation_article_en_cours"):
                contexte_session["creation_article_en_cours"] = final_state["creation_article_en_cours"]
                logger.info(f"🔧 [Session] Saved creation_article_en_cours étape: {final_state['creation_article_en_cours'].get('etape')}")
            else:
                contexte_session["creation_article_en_cours"] = {}
                logger.info(f"🔧 [Session] Cleared creation_article_en_cours")

            # ── Persistance du cycle nomenclature ──
            if final_state.get("nomenclature_en_cours"):
                contexte_session["nomenclature_en_cours"] = final_state["nomenclature_en_cours"]
                logger.info(f"🔧 [Session] Saved nomenclature_en_cours étape: {final_state['nomenclature_en_cours'].get('etape')}")
            else:
                contexte_session["nomenclature_en_cours"] = {}
                logger.info(f"🔧 [Session] Cleared nomenclature_en_cours")

            # ── Persistance du cycle modification nomenclature ──
            if final_state.get("modification_nomenclature_en_cours"):
                contexte_session["modification_nomenclature_en_cours"] = final_state["modification_nomenclature_en_cours"]
                logger.info(f"🔧 [Session] Saved modification_nomenclature_en_cours étape: {final_state['modification_nomenclature_en_cours'].get('etape')}")
            else:
                contexte_session["modification_nomenclature_en_cours"] = {}
                logger.info(f"🔧 [Session] Cleared modification_nomenclature_en_cours")
            
            if final_state.get("attente_confirmation"):
                contexte_session["attente_confirmation"] = final_state["attente_confirmation"]
                logger.info(f"🔧 [Session] Saved attente_confirmation: {final_state['attente_confirmation']}")
            else:
                contexte_session["attente_confirmation"] = False

            # ── Persistance du cycle draft/preview/confirm ──
            contexte_session["document_draft"] = final_state.get("document_draft", {})
            contexte_session["statut_draft"] = final_state.get("statut_draft", "")
            # Persister les suggestions pour l'offre de prix
            if final_state.get("suggestions"):
                contexte_session["suggestions"] = final_state["suggestions"]
            else:
                contexte_session["suggestions"] = []
            if final_state.get("statut_confirmation") == "ATTENTE":
                contexte_session["pending_action"]      = final_state.get("pending_action", {})
                contexte_session["statut_confirmation"] = "ATTENTE"
            else:
                contexte_session["pending_action"]      = {}
                contexte_session["statut_confirmation"] = ""

            # ── PDF généré pendant ce tour ──
            pdf_url = _exposer_pdf(final_state.get("pdf_path", ""), user["username"])
            if pdf_url:
                dernier_pdf_url = pdf_url

            contexte_session["dernier_action_classifiee"] = final_state.get("dernier_action_classifiee", "")
            contexte_session["derniere_question_classifiee"] = final_state.get("derniere_question_classifiee", "")
            derniere_confiance = final_state.get("score_confiance", 0.0)
            derniere_origine = final_state.get("_origine_classification", "")

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
            
            # Ajouter les suggestions directes de l'état (pour offre_prix)
            suggestions_directes = final_state.get("suggestions", [])
            if suggestions_directes:
                suggestions.extend(suggestions_directes)

        # ── Alertes persistantes (ex : BF requis pour OF) ──
        alertes_txt = formater_alertes_persistantes(contexte_session)
        alerts = [alertes_txt] if alertes_txt else []
        
        # Ajouter les suggestions de la session (pour offre_prix)
        session_suggestions = contexte_session.get("suggestions", [])
        if session_suggestions:
            suggestions.extend(session_suggestions)
        
        # Utiliser le draft_status de l'état s'il existe (pour offre_prix)
        draft_status = final_state.get("draft_status") or contexte_session.get("statut_draft", "")
        
        # Ajouter les boutons d'action depuis l'état (pour offre_prix)
        action_buttons = final_state.get("action_buttons")

        return ChatResponse(
            responses=reponses_multi,
            suggestions=suggestions,
            draft_status=draft_status,
            confirmation_status=contexte_session.get("statut_confirmation", ""),
            pdf_url=dernier_pdf_url,
            alerts=alerts,
            attente_complements=contexte_session.get("attente_complements", False),
            score_confiance=derniere_confiance if sous_demandes else 0.0,
            origine_classification=derniere_origine if sous_demandes else "",
            action_buttons=action_buttons,
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────
# NOTES SUR L'AUTHENTIFICATION (login réel, cf. auth.py)
# ──────────────────────────────────────────────────────────────────────
# - Il n'y a plus de secret partagé unique recopié dans le frontend. Chaque
#   utilisateur se connecte avec identifiant + mot de passe via
#   POST /api/auth/login, qui renvoie un JWT à durée de vie limitée
#   (JWT_EXPIRE_MINUTES, 8h par défaut).
#
# - Toutes les routes sensibles (/api/chat, /api/files/pdf/{filename},
#   /api/auth/me) exigent Authorization: Bearer <jwt> et sont vérifiées à
#   CHAQUE requête (y compris que le compte existe encore et est actif).
#
# - Les comptes se créent uniquement côté serveur, via
#   `python -m scripts.gerer_utilisateurs creer <username> <role>` — il n'y a
#   volontairement AUCUNE route d'inscription publique.
#
# - Les PDF générés ne sont plus servis par un dossier public monté en HTTP :
#   ils passent par /api/files/pdf/{filename}, qui exige un JWT valide et
#   vérifie (best-effort) que le document appartient bien à l'utilisateur
#   qui le demande (ou qu'il est admin).
#
# - Variables d'environnement à définir en prod (.env) :
#     JWT_SECRET_KEY       (obligatoire en prod : sinon un secret temporaire
#                            est généré à chaque démarrage → tout le monde
#                            est déconnecté à chaque redéploiement)
#     JWT_EXPIRE_MINUTES    (optionnel, défaut 480)
#     ADMIN_USERNAME / ADMIN_PASSWORD  (optionnel : sinon un compte admin
#                            avec mot de passe aléatoire est généré au
#                            premier démarrage, voir data/admin_credentials_INITIAL.txt)
# ──────────────────────────────────────────────────────────────────────