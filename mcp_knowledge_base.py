"""
mcp_knowledge_base.py — La Base de Connaissance (RAG) — v2 vectorielle
"""

import json
from mcp.server.fastmcp import FastMCP
from indexer_kb import rechercher, KB_COLLECTION, KB_QDRANT_PATH
from pathlib import Path
_qdrant_dir = Path(KB_QDRANT_PATH)
if not _qdrant_dir.exists():
    from indexer_kb import indexer
    print("📚 [KB] Base vectorielle absente → indexation initiale...")
    indexer(reset=False)
    print("✅ [KB] Indexation terminée.")
mcp = FastMCP("Sage-Knowledge-Base-RAG")


def _formater_resultats(resultats: list[dict], entete: str) -> str:
    if not resultats:
        return (
            "🔍 Aucune procédure correspondante trouvée dans la base de connaissance.\n"
            "💡 Suggestion : Contactez le service qualité ou consultez le manuel de procédures."
        )
    blocs = [entete, "─" * 55]
    for r in resultats:
        blocs.append(
            f"📚 [{r['doc_type']}] {r['source_file']} (score={r['score']:.2f})\n"
            f"{r['texte']}\n"
        )
    return "\n".join(blocs)


@mcp.tool()
def rechercher_procedure(requete: str, doc_type: str = "") -> str:
    """
    Recherche vectorielle d'une procédure interne à partir d'une question
    en langage naturel (RAG sur kb_docs/ via Qdrant + nomic-embed-text).

    Args:
        requete:  Question en langage naturel
        doc_type: Filtre optionnel (ex: "relance_commerciale", "recouvrement")
    """
    resultats = rechercher(requete, doc_type=doc_type or None, top_k=3)
    return _formater_resultats(resultats, f"📚 RÉSULTATS — « {requete} »")


@mcp.tool()
def generer_recommandation_commerciale(
    contexte: str, code_client: str = "", indicateur: str = ""
) -> str:
    """Génère une recommandation basée sur le contexte détecté (churn, impayés, etc.)."""
    resultats = rechercher(
    contexte,
    code_client=code_client or None,
    top_k=3,
    doc_type_boost={"relance_commerciale": 1.15, "recouvrement": 1.1},
    freshness_halflife_days=90,  # un doc de 90j pèse ~moitié moins qu'un doc du jour
)

    intro = "🤖 RECOMMANDATION AUTOMATIQUE"
    if code_client:
        intro += f" — Client {code_client}"

    if not resultats:
        return (
            f"{intro}\n{'─'*55}\n📊 Contexte : {contexte}\n\n"
            f"ℹ️  Aucune procédure standard trouvée pour ce cas.\n"
            f"   Recommandation générale : escalader au responsable commercial."
        )

    meilleur = resultats[0]
    return (
        f"{intro}\n{'─'*55}\n"
        f"📊 Contexte détecté : {contexte}\n"
        f"📋 Indicateur clé   : {indicateur or 'Non précisé'}\n\n"
        f"✅ Procédure applicable ({meilleur['doc_type']}, score={meilleur['score']:.2f}) :\n\n"
        f"{meilleur['texte']}\n\n"
        f"⚡ Action prioritaire : Consulter la procédure complète et agir sous 48h."
    )


@mcp.tool()
def lister_procedures_disponibles() -> str:
    """Liste les types de documents (doc_type) présents dans la base vectorielle."""
    from qdrant_client import QdrantClient
    client = QdrantClient(path=KB_QDRANT_PATH)
    # scroll léger pour lister les doc_type distincts
    points, _ = client.scroll(collection_name=KB_COLLECTION, limit=1000, with_payload=True)
    types = {}
    for p in points:
        dt = p.payload.get("doc_type", "inconnu")
        types[dt] = types.get(dt, 0) + 1
    if not types:
        return "📚 Base de connaissance vide. Lancez : python indexer_kb.py --reset"
    lignes = ["📚 Types de documents indexés :\n"]
    for dt, nb in types.items():
        lignes.append(f"  • {dt} : {nb} chunk(s)")
    return "\n".join(lignes)


@mcp.tool()
def obtenir_seuil_alerte_stock(ref_article: str) -> str:
    """Cherche la procédure de réapprovisionnement liée à un article (RAG filtré)."""
    resultats = rechercher(
        f"seuil alerte stock réapprovisionnement {ref_article}",
        ref_article=ref_article, top_k=2,
    )
    if not resultats:
        resultats = rechercher("guide réapprovisionnement fournisseurs seuil stock", top_k=2)
    return _formater_resultats(resultats, f"⚙️  PROCÉDURE STOCK — {ref_article}")


@mcp.tool()
def classifier_document(requete: str) -> str:
    """Retourne le type de document le plus pertinent sous forme de JSON."""
    resultats = rechercher(requete, top_k=1)
    if not resultats:
        return json.dumps({"doc_type": "inconnu", "score": 0.0})
    meilleur = resultats[0]
    return json.dumps({"doc_type": meilleur["doc_type"], "score": float(meilleur["score"])})

if __name__ == "__main__":
    mcp.run()