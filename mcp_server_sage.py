"""
mcp_server_sage.py — Le Traducteur API / Hub d'Orchestration
=========================================================
Rôle : Reçoit les demandes métier en langage structuré, valide
leur cohérence, et les transmet au bon serveur MCP spécialisé.
Ce serveur est le point d'entrée unique de l'architecture.

N'écrit JAMAIS en base directement → il délègue à mcp_actions_sage.
Ne fait PAS de lecture SQL complexe → il délègue à mcp_nl2sql.
"""

import json
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Sage-API-Translator")
DB_PATH = str(Path(__file__).parent / "entreprise_mock.db")

# =====================================================================
# SCHÉMA DE RÉFÉRENCE CENTRALISÉ
# Toute modification de nommage de colonnes se fait ici uniquement.
# =====================================================================
SAGE_SCHEMA = {
    # Tables principales
    "TABLE_CLIENT":      "F_COMPTET",
    "TABLE_ARTICLE":     "F_ARTICLE",
    "TABLE_STOCK":       "F_ARTSTOCK",
    "TABLE_NOMENCLAT":   "F_NOMENCLAT",
    "TABLE_DOC_ENTETE":  "F_DOCENTETE",
    "TABLE_DOC_LIGNE":   "F_DOCLIGNE",

    # Colonnes F_COMPTET
    "COL_CLIENT_ID":     "CT_Num",
    "COL_CLIENT_NOM":    "CT_Intitule",
    "COL_CLIENT_TYPE":   "CT_Type",
    "COL_CLIENT_STATUT": "CT_Validite",
    "COL_CLIENT_ENCOURS":"CT_Encours",

    # Colonnes F_ARTICLE
    "COL_ART_REF":       "AR_Ref",
    "COL_ART_DESIGN":    "AR_Design",
    "COL_ART_PRIX_ACH":  "AR_PrixAch",
    "COL_ART_PRIX_VEN":  "AR_PrixVen",
    "COL_ART_TYPE":      "AR_Type",

    # Colonnes F_ARTSTOCK
    "COL_STOCK_REF":     "AR_Ref",
    "COL_STOCK_QTE":     "AS_QteSto",
    "COL_STOCK_QTE_COM": "AS_QteCom",
    "COL_STOCK_QTE_AHA": "AS_QteAchaCom",

    # Colonnes F_DOCENTETE
    "COL_DOC_PIECE":     "DO_Piece",
    "COL_DOC_DOMAINE":   "DO_Domaine",
    "COL_DOC_TYPE":      "DO_Type",
    "COL_DOC_DATE":      "DO_Date",
    "COL_DOC_TIERS":     "CT_Num",
    "COL_DOC_REF":       "DO_Ref",

    # Colonnes F_DOCLIGNE
    "COL_LIG_PIECE":     "DO_Piece",
    "COL_LIG_ART":       "AR_Ref",
    "COL_LIG_QTE":       "DL_Qte",
    "COL_LIG_PU":        "DL_PrixUnitaire",

    # Codes types de documents Sage
    "DOC_TYPE": {
        "OF":      1,
        "BF":      2,
        "BL":      3,
        "FACTURE": 4,
        "AVOIR":   5,
        "BC":      6,
    },
    # Domaines
    "DOC_DOMAINE": {
        "VENTE":       0,
        "ACHAT":       1,
        "FABRICATION": 2,
    }
}


# =====================================================================
# OUTIL 1 — Exposition du schéma aux autres serveurs
# =====================================================================
@mcp.tool()
def get_schema() -> str:
    """
    Retourne le schéma centralisé de la base Sage en JSON.
    Utilisé par mcp_nl2sql et mcp_actions_sage pour construire
    leurs requêtes de manière cohérente.
    """
    return json.dumps(SAGE_SCHEMA, ensure_ascii=False, indent=2)


# =====================================================================
# OUTIL 2 — Validation et normalisation d'une demande métier
# =====================================================================
@mcp.tool()
def valider_demande_metier(
    type_action: str,
    payload: str
) -> str:
    """
    Valide la cohérence d'une demande métier avant exécution.
    Retourne un JSON avec 'valide': True/False et 'message' explicatif.

    Args:
        type_action: Catégorie de l'action (ex: 'LECTURE', 'ECRITURE', 'EXPORT')
        payload: Données de la demande en JSON stringifié
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

    # Validations spécifiques par type
    if type_action.upper() == "ECRITURE":
        champs_requis = data.get("champs_requis", [])
        manquants = [c for c in champs_requis if not data.get(c)]
        if manquants:
            return json.dumps({
                "valide": False,
                "message": f"Champs obligatoires manquants : {manquants}"
            })

        # Vérification statut client AVANT toute écriture de vente
        # CREER_CLIENT : le client n'existe pas encore → on skip la vérification
        action_demandee = data.get("action", "").upper()
        code_client = data.get("code_client", "").strip()
        if code_client and action_demandee not in ("CREER_CLIENT", "CREER_FOURNISSEUR"):
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT CT_Intitule, CT_Validite FROM F_COMPTET WHERE CT_Num = ?",
                    (code_client,)
                ).fetchone()
                conn.close()
                if row is None:
                    return json.dumps({
                        "valide": False,
                        "message": f"Client '{code_client}' introuvable en base."
                    })
                if row["CT_Validite"] == "BLOQUE":
                    return json.dumps({
                        "valide": False,
                        "message": (
                            f"🚫 Client '{code_client}' ({row['CT_Intitule']}) est BLOQUÉ. "
                            f"Aucune facturation possible. Contactez la direction commerciale."
                        )
                    })
            except Exception as e:
                # Ne pas bloquer si la DB est inaccessible depuis le hub
                pass

    return json.dumps({
        "valide": True,
        "message": f"Demande de type '{type_action}' validée avec succès.",
        "payload_normalise": data
    })


# =====================================================================
# OUTIL 3 — Résolution du code document Sage
# =====================================================================
@mcp.tool()
def resoudre_type_document(libelle: str) -> str:
    """
    Traduit un libellé métier (ex: 'facture', 'bon de livraison')
    en code numérique Sage et en domaine (vente/achat/fabrication).

    Utile pour que l'orchestrateur n'ait pas à connaître les codes internes.
    """
    correspondances = {
        "facture":           ("FACTURE", 4, 0),
        "fa":                ("FACTURE", 4, 0),
        "avoir":             ("AVOIR",   5, 0),
        "av":                ("AVOIR",   5, 0),
        "bon de livraison":  ("BL",      3, 0),
        "bl":                ("BL",      3, 0),
        "bon de commande":   ("BC",      6, 0),
        "bc":                ("BC",      6, 0),
        "ordre de fab":      ("OF",      1, 2),
        "of":                ("OF",      1, 2),
        "bon de fab":        ("BF",      2, 2),
        "bf":                ("BF",      2, 2),
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


# =====================================================================
# OUTIL 4 — Construction d'un rapport de diagnostic client
# =====================================================================
@mcp.tool()
def construire_contexte_client(
    code_client: str,
    statut: str,
    stock_disponible: float,
    quantite_demandee: float
) -> str:
    """
    Construit un contexte décisionnel structuré pour un client donné.
    Retourne un JSON décrivant l'état de la commande et la décision à prendre.
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
            f"Stock insuffisant : {stock_disponible} dispo / {quantite_demandee} demandées. OF requis."
        )

    return json.dumps({
        "code_client":         code_client,
        "statut_client":       statut,
        "stock_disponible":    stock_disponible,
        "quantite_demandee":   quantite_demandee,
        "decision":            decision,
        "alertes":             alertes,
        "pret_pour_livraison": decision == "VALIDER"
    }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()