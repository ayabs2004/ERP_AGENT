"""Module providing FastMCP tools for querying the knowledge base using vector search.
It ensures the vector index exists, defines helper functions for cleaning text and
formatting results, and registers several FastMCP tools for searching procedures,
generating commercial recommendations, listing available document types, obtaining
stock alert thresholds, and classifying documents."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP
from kb.indexer_kb import rechercher, KB_COLLECTION, KB_QDRANT_PATH

_qdrant_dir = Path(KB_QDRANT_PATH)
if not _qdrant_dir.exists():
    from kb.indexer_kb import indexer
    print("📚 [KB] Base vectorielle absente → indexation initiale...")
    indexer(reset=False)
    print("✅ [KB] Indexation terminée.")
mcp = FastMCP("Sage-Knowledge-Base-RAG")

_RX_BANDEAU_TEST = re.compile(
    r"[⚠I]?\s*SECTION SUIVANTE\s*:\s*DONNÉES DE TEST GÉNÉRÉES AUTOMATIQUEMENT\s*—\s*AUCUNE VALEUR RÉELLE\s*",
    re.IGNORECASE,
)


def _nettoyer_texte(texte: str | None) -> str:
    """Remove the test data banner from the provided text to avoid biasing the LLM synthesis."""
    return _RX_BANDEAU_TEST.sub("", texte or "").strip()


def _formater_resultats(resultats: list[dict], entete: str) -> str:
    """Format a list of search result dictionaries into a human‑readable string, including a header and cleaned text for each result."""
    if not resultats:
        return (
            "🔍 Aucune procédure correspondante trouvée dans la base de connaissance.\n"
            "💡 Suggestion : Contactez le service qualité ou consultez le manuel de procédures."
        )
    blocs = [entete, "─" * 55]
    for r in resultats:
        r = {**r, "texte": _nettoyer_texte(r.get("texte", ""))}
        blocs.append(
            f"📚 [{r['doc_type']}] {r['source_file']} (score={r['score']:.2f})\n"
            f"{r['texte']}\n"
        )
    return "\n".join(blocs)


@mcp.tool()
def rechercher_procedure(requete: str, doc_type: str = "") -> str:
    """FastMCP tool that performs a vector search for internal procedures matching a natural‑language query, optionally filtered by document type, and returns formatted results."""
    resultats = rechercher(requete, doc_type=doc_type or None, top_k=3)
    return _formater_resultats(resultats, f"📚 RÉSULTATS — « {requete} »")


@mcp.tool()
def generer_recommandation_commerciale(
    contexte: str, code_client: str = "", indicateur: str = ""
) -> str:
    """FastMCP tool that generates a commercial recommendation based on the provided context, client code, and indicator by searching relevant procedures and formatting the best match."""
    resultats = rechercher(
        contexte,
        code_client=code_client or None,
        top_k=3,
        doc_type_boost={"relance_commerciale": 1.15, "recouvrement": 1.1},
        freshness_halflife_days=90,
    )

    intro = "🤖 RECOMMANDATION AUTOMATIQUE"
    if code_client:
        intro += f" — Client {code_client}"

    if not resultats:
        return (
            f"{intro}\n{'─'*55}\n📊 Contexte : {contexte}\n\n"
            f"ℹ️  Aucune procédure standard trouvée pour ce cas.\n"
            f"   Recommandation générale : escaladar au responsable commercial."
        )

    meilleur = resultats[0]
    return (
        f"{intro}\n{'─'*55}\n"
        f"📊 Contexte détecté : {contexte}\n"
        f"📋 Indicateur clé   : {indicateur or 'Non précisé'}\n\n"
        f"✅ Procédure applicable ({meilleur['doc_type']}, score={meilleur['score']:.2f}) :\n\n"
        f"{_nettoyer_texte(meilleur['texte'])}\n\n"
        f"⚡ Action prioritaire : Consulter la procédure complète et agir sous 48h."
    )


@mcp.tool()
def lister_procedures_disponibles() -> str:
    """FastMCP tool that lists distinct document types stored in the Qdrant vector collection, returning a formatted summary."""
    from qdrant_client import QdrantClient
    client = QdrantClient(path=KB_QDRANT_PATH)
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
    """FastMCP tool that retrieves stock replenishment procedures for a given article reference, falling back to a generic guide if none are found, and returns formatted results."""
    resultats = rechercher(
        f"seuil alerte stock réapprovisionnement {ref_article}",
        ref_article=ref_article, top_k=2,
    )
    if not resultats:
        resultats = rechercher("guide réapprovisionnement fournisseurs seuil stock", top_k=2)
    return _formater_resultats(resultats, f"⚙️  PROCÉDURE STOCK — {ref_article}")


@mcp.tool()
def classifier_document(requete: str) -> str:
    """FastMCP tool that classifies a document based on a query, returning the most relevant document type and its score as a JSON string."""
    resultats = rechercher(requete, top_k=1)
    if not resultats:
        return json.dumps({"doc_type": "inconnu", "score": 0.0})
    meilleur = resultats[0]
    return json.dumps({"doc_type": meilleur["doc_type"], "score": float(meilleur["score"])})

if __name__ == "__main__":
    mcp.run()