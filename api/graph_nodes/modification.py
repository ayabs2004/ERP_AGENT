"""
Modification node for the orchestrator.
Handles modification of clients, suppliers, and articles with confirmation.
"""

import logging
import re
from api.mcp_pool import pool as mcp_pool

logger = logging.getLogger(__name__)


def _get_help_examples(entity_type):
    """Generate help examples based on entity type."""
    if entity_type == "article":
        return "ex: 'designation: Nouvel écran' ou 'prix_vente: 260' ou 'prix_achat: 85'"
    elif entity_type == "client":
        return "ex: 'nom: Nouveau nom' ou 'validite: BLOQUE' ou 'encours: 15000'"
    elif entity_type == "fournisseur":
        return "ex: 'nom: Nouveau nom' ou 'validite: BLOQUE' ou 'encours: 15000'"
    else:
        return "ex: 'nom: Nouveau nom'"


def _get_entity_identifier_hint(entity_type):
    """Generate hint about how to identify the entity."""
    if entity_type == "article":
        return "Vous pouvez spécifier l'article par sa référence (ex: ECRAN4K) ou par son nom (ex: écran 4K)"
    elif entity_type == "client":
        return "Vous pouvez spécifier le client par son code (ex: CLI001) ou par son nom (ex: Dupont)"
    elif entity_type == "fournisseur":
        return "Vous pouvez spécifier le fournisseur par son code (ex: FOUR01) ou par son nom (ex: Supplier)"
    else:
        return "Vous pouvez spécifier l'entité par son code ou par son nom"


def _normalize_field_name(field_text):
    """Normalize field name to snake_case and map synonyms."""
    # Remove accents and convert to lowercase
    import unicodedata
    normalized = unicodedata.normalize('NFKD', field_text.lower())
    normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
    
    # Replace spaces and apostrophes with underscores
    normalized = re.sub(r"[\s'\-]", "_", normalized)
    
    # Map synonyms to MCP field names
    synonym_map = {
        # Article fields
        "nom": "designation",
        "designation": "designation",
        "prix_achat": "prix_achat",
        "prix_d_achat": "prix_achat",
        "prix_vente": "prix_vente",
        "prix_de_vente": "prix_vente",
        "type": "type_article",
        "type_article": "type_article",
        # Client/Supplier fields
        "intitule": "intitule",
        "validite": "validite",
        "statut": "validite",
        "encours": "encours_max",
        "encours_max": "encours_max",
    }
    
    return synonym_map.get(normalized, normalized)


async def noeud_modification(state, _parse_mcp_response):
    """
    Handles modification operations for clients, suppliers, and articles.
    Workflow:
    1. Extract entity type and identifier from user request
    2. Read current entity details using MCP
    3. Display current fields (excluding ID)
    4. Ask user what to modify
    5. Apply changes after confirmation
    """
    logger.info("⚡ [Agent Modification]...")
    
    action = state["action"]
    question = state.get("demande_brute", "")
    
    # Extract entity type from action
    entity_type = None
    if action == "MODIFIER_CLIENT":
        entity_type = "client"
    elif action == "MODIFIER_FOURNISSEUR":
        entity_type = "fournisseur"
    elif action == "MODIFIER_ARTICLE":
        entity_type = "article"
    elif action == "MODIFIER_ENTITE":
        # Try to extract from question
        if "client" in question.lower():
            entity_type = "client"
        elif "fournisseur" in question.lower() or "supplier" in question.lower():
            entity_type = "fournisseur"
        elif "article" in question.lower() or "produit" in question.lower():
            entity_type = "article"
    
    if not entity_type:
        state["reponse"] = "❌ Impossible d'identifier le type d'entité à modifier."
        state["fini"] = True
        return state
    
    # Extract entity ID from state (already extracted by classifier)
    entity_id = None
    if entity_type == "client":
        entity_id = state.get("code_client") or state.get("nom_client_brut") or state.get("nom_client")
    elif entity_type == "fournisseur":
        entity_id = state.get("code_fournisseur") or state.get("code_client") or state.get("nom_client_brut") or state.get("nom_client")  # Fallback to code_client or name
    elif entity_type == "article":
        entity_id = state.get("ref_article") or state.get("nom_article_brut") or state.get("nom_article")
    
    logger.info(f"  → entity_id extrait du state: {entity_id}")
    
    # Fallback to regex extraction if not in state
    if not entity_id:
        import re
        if entity_type == "client":
            match = re.search(r'(CLI\d+|[A-Z]{3,}\d+)', question.upper())
        elif entity_type == "fournisseur":
            match = re.search(r'(FOUR\d+|[A-Z]{3,}\d+)', question.upper())
        else:  # article
            match = re.search(r'([A-Z0-9-]+)', question.upper())
        if match:
            entity_id = match.group(1)
            logger.info(f"  → entity_id extrait par regex: {entity_id}")
    
    if not entity_id:
        identifier_hint = _get_entity_identifier_hint(entity_type)
        state["reponse_finale"] = f"❌ Impossible d'identifier le {entity_type} à modifier. {identifier_hint}."
        state["fini"] = True
        return state
    
    # Step 1: Read current entity details
    tool_name = f"lire_{entity_type}"
    param_name = f"code_{entity_type}" if entity_type != "article" else "ref_article"
    
    logger.info(f"  → Appel MCP: {tool_name} avec {param_name}={entity_id}")
    
    try:
        result = await mcp_pool.call("actions", tool_name, {param_name: entity_id})
        logger.info(f"  → Résultat MCP brut: {result}")
        data = _parse_mcp_response(result)
        logger.info(f"  → Données parsées: {data}")
        
        if data.get("statut") == "ERREUR":
            identifier_hint = _get_entity_identifier_hint(entity_type)
            state["reponse_finale"] = f"❌ {data.get('message', 'Erreur lors de la lecture')}\n\n{identifier_hint}."
            state["fini"] = True
            return state
        
        # Step 2: Display current fields
        if entity_type == "client":
            fields = {
                "Code": data.get("CT_Num"),
                "Nom": data.get("CT_Intitule"),
                "Validité": data.get("CT_Validite"),
                "Encours max": data.get("CT_EncoursMax"),
            }
        elif entity_type == "fournisseur":
            fields = {
                "Code": data.get("CT_Num"),
                "Nom": data.get("CT_Intitule"),
                "Validité": data.get("CT_Validite"),
                "Encours max": data.get("CT_EncoursMax"),
            }
        else:  # article
            fields = {
                "Référence": data.get("AR_Ref"),
                "Désignation": data.get("AR_Design"),
                "Prix achat": data.get("AR_PrixAch"),
                "Prix vente": data.get("AR_PrixVen"),
                "Stock": data.get("AS_QteSto"),
            }
        
        # Format display
        field_display = "\n".join(f"  • {k}: {v}" for k, v in fields.items() if v is not None)
        
        help_examples = _get_help_examples(entity_type)
        identifier_hint = _get_entity_identifier_hint(entity_type)
        
        state["reponse_finale"] = f"""📋 **{entity_type.capitalize()} actuel** ({entity_id}):
{field_display}

🔧 **Que souhaitez-vous modifier ?**
Veuillez spécifier le champ et la nouvelle valeur ({help_examples}).

💡 **Note:** {identifier_hint}."""
        
        state["modification_en_cours"] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "current_data": data,
        }
        # Set attente_confirmation to True so next message goes to confirmation node
        state["attente_confirmation"] = True
        
    except Exception as e:
        logger.error(f"Erreur lors de la lecture: {e}")
        state["reponse"] = f"❌ Erreur lors de la lecture de l'entité: {e}"
        state["fini"] = True
    
    return state


async def noeud_modification_confirmation(state, _parse_mcp_response):
    """
    Handles the confirmation step for modification.
    Applies the changes after user confirmation.
    """
    logger.info("⚡ [Agent Modification Confirmation]...")
    
    # Check if this is the first call (just displaying fields) or actual modification
    if not state.get("modification_en_cours"):
        state["reponse_finale"] = "❌ Aucune modification en cours."
        state["fini"] = True
        return state
    
    # If the user just sent the initial request, display fields and wait
    # We detect this by checking if the question is the original modification request
    question = state.get("demande_brute", "").lower()
    mod_info = state["modification_en_cours"]
    entity_type = mod_info["entity_type"]
    entity_id = mod_info["entity_id"]
    
    # Simple heuristic: if the question contains "modifier" and no field:value pattern,
    # this is likely the initial request, so we should have displayed fields already
    # This node is called when attente_confirmation is True, so we need to handle both cases
    
    # Check if user provided modifications (field:value pattern)
    has_modifications = (
        ":" in question and 
        any(k in question.lower() for k in ["nom", "intitule", "validite", "statut", "encours", "prix", "designation", "type"])
    )
    
    if not has_modifications:
        # No modifications provided - this might be a follow-up without clear format
        # Just ask again for clarification
        help_examples = _get_help_examples(entity_type)
        state["reponse_finale"] = f"❌ Impossible de comprendre la modification. Veuillez spécifier le champ et la valeur ({help_examples})."
        state["attente_confirmation"] = True
        return state
    
    # Parse and apply modifications
    
    # Extract field and value using flexible parsing
    modifications = {}
    
    # Split by colon and normalize field names
    for part in question.split(","):
        if ":" not in part:
            continue
        field_part, value_part = part.split(":", 1)
        field_name = _normalize_field_name(field_part.strip())
        value = value_part.strip()
        
        # Convert numeric values
        if field_name in ["encours_max", "prix_achat", "prix_vente"]:
            try:
                value = float(value.replace(",", "."))
            except ValueError:
                pass  # Keep as string if conversion fails
        
        modifications[field_name] = value
    
    if not modifications:
        help_examples = _get_help_examples(entity_type)
        state["reponse_finale"] = f"❌ Impossible de comprendre la modification. Veuillez spécifier le champ et la valeur ({help_examples})."
        state["attente_confirmation"] = True
        return state
    
    # Apply modifications
    tool_name = f"modifier_{entity_type}"
    param_name = f"code_{entity_type}" if entity_type != "article" else "ref_article"
    
    try:
        result = await mcp_pool.call("actions", tool_name, {param_name: entity_id, **modifications})
        data = _parse_mcp_response(result)
        
        if data.get("statut") == "SUCCES":
            state["reponse_finale"] = f"✅ {data.get('message', 'Modification réussie')}"
            state["fini"] = True
            state["modification_en_cours"] = None
        else:
            state["reponse_finale"] = f"❌ {data.get('message', 'Erreur lors de la modification')}"
            state["attente_confirmation"] = True
            
    except Exception as e:
        logger.error(f"Erreur lors de la modification: {e}")
        state["reponse_finale"] = f"❌ Erreur lors de la modification: {e}"
        state["attente_confirmation"] = True
    
    return state
