import os
import re

filepath = 'api/orchestrateur_general.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add ATTENTE_MULTI_LIGNES bypass in classifier
# Around: if state.get('document_draft') and (statut_draft_bypass in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE')
content = content.replace(
    "statut_draft_bypass in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE')",
    "statut_draft_bypass in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE', 'ATTENTE_MULTI_LIGNES')"
)
content = content.replace(
    "statut_draft_session in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE')",
    "statut_draft_session in ('PREVIEW', 'COLLECTE', 'ATTENTE_REMISE', 'ATTENTE_MULTI_LIGNES')"
)

# 2. Add logic in noeud_collecte_draft
block_to_find = "if draft_existant and state.get('statut_draft') == 'COLLECTE':"

multi_lignes_block = """
    if draft_existant and state.get('statut_draft') == 'ATTENTE_MULTI_LIGNES':
        d_lower = demande.strip().lower()
        if est_confirmation_stricte(demande) or _est_non(demande) or d_lower in ("c'est tout", "confirmer", "non merci", "non"):
            state['statut_draft'] = 'PREVIEW'
            return state
        if est_annulation_stricte(demande):
            state['statut_draft'] = ''
            state['document_draft'] = {}
            state['reponse_finale'] = '🛑 Document annulé.'
            return state
        
        state['statut_draft'] = 'COLLECTE'
        if _est_oui(demande) or d_lower in ("ajouter", "autre", "autre article", "oui", "oui merci", "oui je veux", "ajoute"):
            state['reponse_finale'] = "Quelle est la référence de l'article à ajouter ?"
            return state
        # Sinon, on le laisse couler dans COLLECTE pour essayer d'absorber la réponse
        
    if draft_existant and state.get('statut_draft') == 'COLLECTE':
"""
content = content.replace(block_to_find, multi_lignes_block.strip())

# 3. Add transition to ATTENTE_MULTI_LIGNES at the end of COLLECTE
# The original code has:
#       state['statut_draft'] = 'PREVIEW'
#       return state
# But we must be careful to replace only the one inside noeud_collecte_draft.
# Let's use regex to find it inside noeud_collecte_draft.
# Around: 
#           state['document_draft'] = {}
#           return state
#       state['statut_draft'] = 'PREVIEW'
#       return state

collecte_end_find = """
        state['document_draft'] = {}
        return state
    state['statut_draft'] = 'PREVIEW'
    return state
"""

collecte_end_replace = """
        state['document_draft'] = {}
        return state
        
    draft = state['document_draft']
    if draft.get('type_doc') in ('BL', 'FACTURE', 'BL_ACHAT', 'FA_ACHAT'):
        panier = draft.get('lignes_panier', [])
        if draft.get('ref_article'):
            panier.append({
                "ref_article": draft.pop('ref_article', ''),
                "quantite": draft.pop('quantite', 0.0),
                "prix_unitaire": draft.pop('prix_unitaire', 0.0),
            })
            draft['lignes_panier'] = panier
        state['statut_draft'] = 'ATTENTE_MULTI_LIGNES'
        state['reponse_finale'] = "📦 Article ajouté au panier.\\n\\nVoulez-vous **ajouter un autre article** ou **confirmer** le document ?"
        return state
        
    state['statut_draft'] = 'PREVIEW'
    return state
"""

# Only replace the first occurrence after noeud_collecte_draft
idx = content.find('def noeud_collecte_draft')
if idx != -1:
    idx_end = content.find("state['statut_draft'] = 'PREVIEW'", idx)
    if idx_end != -1:
        # replace the specific block
        sub_content = content[idx:idx_end+100]
        new_sub = sub_content.replace("    state['statut_draft'] = 'PREVIEW'\n    return state", collecte_end_replace.strip())
        content = content[:idx] + new_sub + content[idx_end+100:]

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch applied.")
