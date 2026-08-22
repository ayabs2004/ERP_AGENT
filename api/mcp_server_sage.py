"""
mcp_server_sage.py — Le Traducteur API / Hub d'Orchestration
=========================================================
Rôle : Reçoit les demandes métier en langage structuré, valide
leur cohérence, et les transmet au bon serveur MCP spécialisé.
Ce serveur est le point d'entrée unique de l'architecture.

N'écrit JAMAIS en base directement → il délègue à mcp_actions_sage.
Ne fait PAS de lecture SQL complexe → il délègue à mcp_nl2sql.

──────────────────────────────────────────────────────────────────
v4.2 — CORRECTIF DE NEUTRALITÉ DB (règle d'or db_adapter.py)
       Ce fichier importait auparavant `database.schema_sage_a_effacer`
       (nom explicite : fichier obsolète, source de vérité concurrente
       de adaptation/db_config.json) et exécutait une requête SQL brute
       avec CT_Num/CT_Intitule/CT_Validite/F_COMPTET codés en dur dans
       `valider_demande_metier()`.
       Désormais :
         - les noms de TABLE et de COLONNE physiques proviennent
           exclusivement de `adaptation/db_adapter.py` (table()/col()),
           lui-même alimenté par `adaptation/db_config.json` ;
         - les CODES MÉTIER (DO_Type=3, DO_Domaine=0, etc., qui ne sont
           PAS des noms de colonnes mais des valeurs applicatives) sont
           importés depuis `database.schema_sage`, le même module de
           référence que celui utilisé par mcp_actions_sage.py — et
           non plus depuis le fichier `..._a_effacer` désormais inutile ;
         - la connexion DB passe par `db_adapter.get_connection()`
           (sqlite ou mssql selon db_config.json) au lieu d'un
           `sqlite3.connect()` figé sur le chemin du mock.
       Aucun nom de table/colonne physique Sage n'est plus écrit en
       dur dans ce fichier.
──────────────────────────────────────────────────────────────────
"""

import json
import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Codes métier (valeurs applicatives, PAS des noms physiques) ──────
# Même source que mcp_actions_sage.py — plus jamais schema_sage_a_effacer.
from database.schema_sage import DOC_DOMAINE, DOC_TYPE

# ── Mapping schéma DB centralisé (table/colonnes physiques) ──────────
from adaptation.db_adapter import table, col, get_connection

mcp = FastMCP("Sage-API-Translator")


# =====================================================================
# SCHÉMA DE RÉFÉRENCE CENTRALISÉ
# Toute modification de nommage de colonnes se fait dans
# adaptation/db_config.json UNIQUEMENT — jamais ici.
# =====================================================================
def _construire_sage_schema() -> dict:
    """
    Construit dynamiquement le schéma exposé aux autres serveurs MCP,
    à partir de adaptation.db_adapter (table()/col()), pour que ce
    dictionnaire ne puisse jamais diverger de db_config.json.
    """
    return {
        # Tables principales (noms logiques → noms physiques réels)
        "TABLE_CLIENT":      table("clients_fournisseurs"),
        "TABLE_ARTICLE":     table("articles"),
        "TABLE_STOCK":       table("stock"),
        "TABLE_NOMENCLAT":   table("nomenclature"),
        "TABLE_DOC_ENTETE":  table("doc_entete"),
        "TABLE_DOC_LIGNE":   table("doc_ligne"),

        # Colonnes clients_fournisseurs (ex F_COMPTET)
        "COL_CLIENT_ID":      col("clients_fournisseurs", "code"),
        "COL_CLIENT_NOM":     col("clients_fournisseurs", "nom"),
        "COL_CLIENT_TYPE":    col("clients_fournisseurs", "type_tiers"),
        "COL_CLIENT_STATUT":  col("clients_fournisseurs", "validite"),
        "COL_CLIENT_ENCOURS": col("clients_fournisseurs", "encours"),
        "COL_CLIENT_ENCOURS_MAX": col("clients_fournisseurs", "encours_max"),

        # Colonnes articles (ex F_ARTICLE)
        "COL_ART_REF":      col("articles", "ref"),
        "COL_ART_DESIGN":   col("articles", "designation"),
        "COL_ART_PRIX_ACH": col("articles", "prix_achat"),
        "COL_ART_PRIX_VEN": col("articles", "prix_vente"),
        "COL_ART_TYPE":     col("articles", "type_article"),

        # Colonnes stock (ex F_ARTSTOCK)
        "COL_STOCK_REF":     col("stock", "ref"),
        "COL_STOCK_QTE":     col("stock", "qte_stock"),
        "COL_STOCK_QTE_COM": col("stock", "qte_commande"),

        # Colonnes doc_entete (ex F_DOCENTETE)
        "COL_DOC_PIECE":   col("doc_entete", "piece"),
        "COL_DOC_DOMAINE": col("doc_entete", "domaine"),
        "COL_DOC_TYPE":    col("doc_entete", "type"),
        "COL_DOC_DATE":    col("doc_entete", "date"),
        "COL_DOC_TIERS":   col("doc_entete", "code_tiers"),
        "COL_DOC_REF":     col("doc_entete", "reference"),

        # Colonnes doc_ligne (ex F_DOCLIGNE)
        "COL_LIG_PIECE": col("doc_ligne", "piece"),
        "COL_LIG_ART":   col("doc_ligne", "ref_article"),
        "COL_LIG_QTE":   col("doc_ligne", "qte"),
        "COL_LIG_PU":    col("doc_ligne", "prix_unitaire"),

        # Codes types de documents Sage (valeurs applicatives — pas des
        # noms de colonnes, donc légitimement issues de database.schema_sage)
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
        # Domaines
        "DOC_DOMAINE": {
            "VENTE":       DOC_DOMAINE.get("BL", 0),
            "ACHAT":       DOC_DOMAINE.get("BL_ACHAT", 1),
            "FABRICATION": DOC_DOMAINE.get("OF", 2),
        },
    }


SAGE_SCHEMA = _construire_sage_schema()


# =====================================================================
# OUTIL 1 — Exposition du schéma aux autres serveurs
# =====================================================================
@mcp.tool()
def get_schema() -> str:
    """
    Retourne le schéma centralisé de la base Sage en JSON.
    Utilisé par mcp_nl2sql et mcp_actions_sage pour construire
    leurs requêtes de manière cohérente.

    Ce schéma est reconstruit à chaque appel à partir de
    adaptation/db_config.json : il ne peut donc jamais diverger
    de la configuration réelle.
    """
    return json.dumps(_construire_sage_schema(), ensure_ascii=False, indent=2)


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
                # row peut être un sqlite3.Row (accès par clé) ou un tuple
                # pyodbc (accès par index) selon le driver configuré.
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