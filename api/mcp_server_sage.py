"""mcp_server_sage module.

Provides the API translation hub for Sage. Receives structured business requests,
validates them, resolves document codes, and exposes the centralized Sage schema
to other MCP services. It delegates database writes to mcp_actions_sage and
SQL generation to mcp_nl2sql, ensuring all physical table/column names are
derived from adaptation.db_adapter based on db_config.json.
"""

import json
import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.schema_sage import DOC_DOMAINE, DOC_TYPE
from adaptation.db_adapter import table, col, get_connection

mcp = FastMCP("Sage-API-Translator")


def _construire_sage_schema() -> dict:
    """Construct the centralized Sage schema dictionary.

    Uses adaptation.db_adapter to retrieve physical table and column names,
    ensuring the schema stays synchronized with db_config.json.
    """
    return {
        "TABLE_CLIENT":      table("clients_fournisseurs"),
        "TABLE_ARTICLE":     table("articles"),
        "TABLE_STOCK":       table("stock"),
        "TABLE_NOMENCLAT":   table("nomenclature"),
        "TABLE_DOC_ENTETE":  table("doc_entete"),
        "TABLE_DOC_LIGNE":   table("doc_ligne"),
        "COL_CLIENT_ID":      col("clients_fournisseurs", "code"),
        "COL_CLIENT_NOM":     col("clients_fournisseurs", "nom"),
        "COL_CLIENT_TYPE":    col("clients_fournisseurs", "type_tiers"),
        "COL_CLIENT_STATUT":  col("clients_fournisseurs", "validite"),
        "COL_CLIENT_ENCOURS": col("clients_fournisseurs", "encours"),
        "COL_ART_REF":      col("articles", "ref"),
        "COL_ART_DESIGN":   col("articles", "designation"),
        "COL_ART_PRIX_ACH": col("articles", "prix_achat"),
        "COL_ART_PRIX_VEN": col("articles", "prix_vente"),
        "COL_ART_TYPE":     col("articles", "type_article"),
        "COL_STOCK_REF":     col("stock", "ref"),
        "COL_STOCK_QTE":     col("stock", "qte_stock"),
        "COL_STOCK_QTE_COM": col("stock", "qte_commande"),
        "COL_DOC_PIECE":   col("doc_entete", "piece"),
        "COL_DOC_DOMAINE": col("doc_entete", "domaine"),
        "COL_DOC_TYPE":    col("doc_entete", "type"),
        "COL_DOC_DATE":    col("doc_entete", "date"),
        "COL_DOC_TIERS":   col("doc_entete", "code_tiers"),
        "COL_DOC_REF":     col("doc_entete", "reference"),
        "COL_LIG_PIECE": col("doc_ligne", "piece"),
        "COL_LIG_ART":   col("doc_ligne", "ref_article"),
        "COL_LIG_QTE":   col("doc_ligne", "qte"),
        "COL_LIG_PU":    col("doc_ligne", "prix_unitaire"),
        "DOC_TYPE": {
            "OF":       DOC_TYPE.get("OF", 1),
            "BF":       DOC_TYPE.get("BF", 4),
            "BL":       DOC_TYPE.get("BL", 2),
            "FACTURE":  DOC_TYPE.get("FACTURE", 3),
            "AVOIR":    DOC_TYPE.get("AVOIR", 9),
            "BC":       DOC_TYPE.get("BC", 6),
            "BL_ACHAT": DOC_TYPE.get("BL_ACHAT", 13),
            "FA_ACHAT": DOC_TYPE.get("FA_ACHAT", 16),
        },
        "DOC_DOMAINE": {
            "VENTE":       DOC_DOMAINE.get("BL", 0),
            "ACHAT":       DOC_DOMAINE.get("BL_ACHAT", 1),
            "FABRICATION": DOC_DOMAINE.get("OF", 2),
        },
    }


SAGE_SCHEMA = _construire_sage_schema()


@mcp.tool()
def get_schema() -> str:
    """Return the centralized Sage schema as a JSON string.

    Used by other MCP services to build consistent queries.
    """
    return json.dumps(_construire_sage_schema(), ensure_ascii=False, indent=2)


@mcp.tool()
def valider_demande_metier(
    type_action: str,
    payload: str
) -> str:
    """Validate a business request before execution.

    Returns a JSON object with a boolean 'valide' field and a descriptive
    'message'. The payload is also normalized and returned when validation
    succeeds.

    Args:
        type_action: Category of the action (e.g., 'LECTURE', 'ECRITURE',
            'EXPORT', 'WORKFLOW').
        payload: JSON‑encoded string containing the request data.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return json.dumps({"valide": False, "message": "Payload JSON invalide."})

    actions_connues = {"LECTURE", "ECRITURE", "EXPORT", "WORKFLOW"}
    if type_action.upper() not in actions_connues:
        return json.dumps({
            "valide": False,
            "message": f"Type d'action '{type_action}' non reconnu. Options: {actions_connues}"
        })

    if type_action.upper() == "ECRITURE":
        champs_requis = data.get("champs_requis", [])
        manquants = [c for c in champs_requis if not data.get(c)]
        if manquants:
            return json.dumps({
                "valide": False,
                "message": f"Champs obligatoires manquants : {manquants}"
            })

        action_demandee = data.get("action", "").upper()
        code_client = data.get("code_client", "").strip()
        if code_client and action_demandee not in ("CREER_CLIENT", "CREER_FOURNISSEUR"):
            try:
                clients_table = table("clients_fournisseurs")
                code_col      = col("clients_fournisseurs", "code")
                nom_col       = col("clients_fournisseurs", "nom")
                validite_col  = col("clients_fournisseurs", "validite")

                conn = get_connection()
                try:
                    row = conn.execute(
                        f"SELECT {nom_col} AS nom, {validite_col} AS validite "
                        f"FROM {clients_table} WHERE {code_col} = ?",
                        (code_client,)
                    ).fetchone()
                finally:
                    conn.close()

                if row is None:
                    return json.dumps({
                        "valide": False,
                        "message": f"Client '{code_client}' introuvable en base."
                    })
                try:
                    nom = row["nom"]
                    validite = row["validite"]
                except (TypeError, IndexError, KeyError):
                    nom, validite = row[0], row[1]

                if str(validite or "").upper() == "BLOQUE":
                    return json.dumps({
                        "valide": False,
                        "message": (
                            f"🚫 Client '{code_client}' ({nom}) est BLOQUÉ. "
                            f"Aucune facturation possible. Contactez la direction commerciale."
                        )
                    })
            except Exception:
                pass

    return json.dumps({
        "valide": True,
        "message": f"Demande de type '{type_action}' validée avec succès.",
        "payload_normalise": data
    })


@mcp.tool()
def resoudre_type_document(libelle: str) -> str:
    """Resolve a business label to Sage document code and domain.

    Translates labels such as 'facture' or 'bon de livraison' into the
    corresponding Sage type code and domain identifier.

    Args:
        libelle: Human‑readable document label.
    """
    correspondances = {
        "facture":           ("FACTURE", DOC_TYPE.get("FACTURE", 3), DOC_DOMAINE.get("FACTURE", 0)),
        "fa":                ("FACTURE", DOC_TYPE.get("FACTURE", 3), DOC_DOMAINE.get("FACTURE", 0)),
        "avoir":             ("AVOIR",   DOC_TYPE.get("AVOIR", 9), DOC_DOMAINE.get("AVOIR", 0)),
        "av":                ("AVOIR",   DOC_TYPE.get("AVOIR", 9), DOC_DOMAINE.get("AVOIR", 0)),
        "bon de livraison":  ("BL",      DOC_TYPE.get("BL", 2), DOC_DOMAINE.get("BL", 0)),
        "bl":                ("BL",      DOC_TYPE.get("BL", 2), DOC_DOMAINE.get("BL", 0)),
        "bon de commande":   ("BC",      DOC_TYPE.get("BC", 6), DOC_DOMAINE.get("BC", 1)),
        "bc":                ("BC",      DOC_TYPE.get("BC", 6), DOC_DOMAINE.get("BC", 1)),
        "ordre de fab":      ("OF",      DOC_TYPE.get("OF", 1), DOC_DOMAINE.get("OF", 2)),
        "of":                ("OF",      DOC_TYPE.get("OF", 1), DOC_DOMAINE.get("OF", 2)),
        "bon de fab":        ("BF",      DOC_TYPE.get("BF", 4), DOC_DOMAINE.get("BF", 2)),
        "bf":                ("BF",      DOC_TYPE.get("BF", 4), DOC_DOMAINE.get("BF", 2)),
    }

    key = libelle.lower().strip()
    if key in correspondances:
        code, type_num, domaine = correspondances[key]
        return json.dumps({
            "libelle_entree": libelle,
            "code_sage":      code,
            "DO_Type":        type_num,
            "DO_Domaine":     domaine
        })

    return json.dumps({
        "erreur": f"Libellé '{libelle}' non reconnu. Utilisez: facture, avoir, bl, bc, of, bf."
    })


@mcp.tool()
def construire_contexte_client(
    code_client: str,
    statut: str,
    stock_disponible: float,
    quantite_demandee: float
) -> str:
    """Build a decision context for a client.

    Returns a JSON structure describing the client’s status, stock situation,
    and the recommended action.

    Args:
        code_client: Identifier of the client.
        statut: Current status of the client (e.g., 'BLOQUE', 'SUSPECT',
            'NON_TROUVE', or other).
        stock_disponible: Quantity of product currently in stock.
        quantite_demandee: Quantity requested by the client.
    """
    decision = "VALIDER"
    alertes = []

    if statut == "BLOQUE":
        decision = "BLOQUER"
        alertes.append("Client bloqué pour risque financier.")
    elif statut == "SUSPECT":
        decision = "ESCALADER"
        alertes.append("Client en surveillance. Validation manuelle requise.")
    elif statut == "NON_TROUVE":
        decision = "CREER_CLIENT"
        alertes.append("Nouveau client détecté. Création de fiche requise.")

    if stock_disponible < quantite_demandee and decision == "VALIDER":
        decision = "LANCER_PRODUCTION"
        alertes.append(
            f"Stock insuffisant : {stock_disponible} dispo / {quantite_demandee