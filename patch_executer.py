import re

filepath_graph = 'graph/draft_flow.py'
with open(filepath_graph, 'r', encoding='utf-8') as f:
    cg = f.read()

# Update executer_draft_confirme to pass draft.get('lignes_panier')
cg = cg.replace(
    "return await mcp_workflow_bl(draft.get('code_client', ''), draft.get('ref_article', ''), float(draft.get('quantite', 0)), float(draft.get('prix_unitaire', 0) or 0))",
    "return await mcp_workflow_bl(draft.get('code_client', ''), draft.get('ref_article', ''), float(draft.get('quantite', 0)), float(draft.get('prix_unitaire', 0) or 0), lignes=draft.get('lignes_panier'))"
)
cg = cg.replace(
    "return await mcp_workflow_facture(draft.get('code_client', ''), draft.get('ref_article', ''), float(draft.get('quantite', 0)), float(draft.get('prix_unitaire', 0) or 0))",
    "return await mcp_workflow_facture(draft.get('code_client', ''), draft.get('ref_article', ''), float(draft.get('quantite', 0)), float(draft.get('prix_unitaire', 0) or 0), lignes=draft.get('lignes_panier'))"
)

with open(filepath_graph, 'w', encoding='utf-8') as f:
    f.write(cg)


filepath_api = 'api/orchestrateur_general.py'
with open(filepath_api, 'r', encoding='utf-8') as f:
    ca = f.read()

# Update _mcp_workflow_bl signature and payload
ca = ca.replace(
    "async def _mcp_workflow_bl(code_client: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None) -> dict:",
    "async def _mcp_workflow_bl(code_client: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None, lignes: list=None) -> dict:"
)
ca = ca.replace(
    "async def _mcp_workflow_facture(code_client: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None) -> dict:",
    "async def _mcp_workflow_facture(code_client: str, ref_article: str, quantite: float, prix_unitaire: float=0.0, date_doc: str | None=None, lignes: list=None) -> dict:"
)

# For both functions, inject `lignes` if it exists.
# We'll use a regex sub to catch the payload dictionary.
ca = re.sub(
    r"(payload = \{'code_client': code_client, 'ref_article': ref_article, 'quantite': quantite, 'prix_unitaire': prix_unitaire\})",
    r"\1\n    if lignes:\n        payload['lignes'] = lignes",
    ca
)

with open(filepath_api, 'w', encoding='utf-8') as f:
    f.write(ca)

print("Patch executer applied.")
