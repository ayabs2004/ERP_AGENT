import os

filepath = 'api/orchestrateur_general.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

idx_start = content.find("def noeud_collecte_draft")
idx_end = content.find("async def noeud_preview_draft", idx_start)

sub = content[idx_start:idx_end]

old_str = "    state['statut_draft'] = 'PREVIEW'\n    return state"

new_str = """    draft = state['document_draft']
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
    return state"""

if old_str in sub:
    new_sub = sub.replace(old_str, new_str)
    content = content[:idx_start] + new_sub + content[idx_end:]
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patch successful!")
else:
    print("Failed again.")
