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

from schema_sage import COL, DOC_DOMAINE, DOC_TYPE

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
    "COL_CLIENT_ID":     COL["CT_NUM"],
    "COL_CLIENT_NOM":    COL["CT_INTITULE"],
    "COL_CLIENT_TYPE":   COL["CT_TYPE"],
    "COL_CLIENT_STATUT": COL["CT_VALIDITE"],
    "COL_CLIENT_ENCOURS": COL["CT_ENCOURS"],

    # Colonnes F_ARTICLE
    "COL_ART_REF":       COL["AR_REF"],
    "COL_ART_DESIGN":    COL["AR_DESIGN"],
    "COL_ART_PRIX_ACH":  COL["AR_PRIXACH"],
    "COL_ART_PRIX_VEN":  COL["AR_PRIXVEN"],
    "COL_ART_TYPE":      "AR_Type",

    # Colonnes F_ARTSTOCK
    "COL_STOCK_REF":     COL["AR_REF"],
    "COL_STOCK_QTE":     COL["AS_QTESTO"],
    "COL_STOCK_QTE_COM": COL["AS_QTECOM"],
    "COL_STOCK_QTE_AHA": "AS_QteAchaCom",

    # Colonnes F_DOCENTETE
    "COL_DOC_PIECE":     COL["DO_PIECE"],
    "COL_DOC_DOMAINE":   COL["DO_DOMAINE"],
    "COL_DOC_TYPE":      COL["DO_TYPE"],
    "COL_DOC_DATE":      COL["DO_DATE"],
    "COL_DOC_TIERS":     COL["CT_NUM"],
    "COL_DOC_REF":       COL["DO_REF"],

    # Colonnes F_DOCLIGNE
    "COL_LIG_PIECE":     COL["DO_PIECE"],
    "COL_LIG_ART":       COL["AR_REF"],
    "COL_LIG_QTE":       COL["DL_QTE"],
    "COL_LIG_PU":        COL["DL_PU"],

    # Codes types de documents Sage
    "DOC_TYPE": {
        "OF":      DOC_TYPE.get("OF", 1),
        "BF":      DOC_TYPE.get("BF", 2),
        "BL":      DOC_TYPE.get("BL", 2),
        "FACTURE": DOC_TYPE.get("FACTURE", 3),
        "AVOIR":   DOC_TYPE.get("AVOIR", 9),
        "BC":      DOC_TYPE.get("BC", 6),
    },
    # Domaines
    "DOC_DOMAINE": {
        "VENTE":       DOC_DOMAINE.get("BL", 0),
        "ACHAT":       DOC_DOMAINE.get("BL_ACHAT", 1),
        "FABRICATION": DOC_DOMAINE.get("OF", 2),
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