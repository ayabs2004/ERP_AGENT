"""
schema_sage.py — Source de vérité unique pour le schéma ERP Sage 100
=====================================================================
Importé par tous les serveurs MCP pour garantir la cohérence des
codes DO_Type, DO_Domaine et des noms de colonnes.

RÈGLE : toute modification de schéma se fait ICI uniquement.
"""

# ─────────────────────────────────────────────────────────────────────
# CODES DO_TYPE + DO_DOMAINE (schéma réel entreprise_mock.db)
# Tuple : (DO_Type, DO_Domaine)
# ─────────────────────────────────────────────────────────────────────
DOC_CODES = {
    "OF":       (25, 2),  # Ordre de Fabrication
    "BL":       (3, 0),   # Bon de Livraison (vente)
    "BF":       (26, 2),  # Bon de Fabrication
    "FACTURE":  (6, 0),   # Facture de vente
    "FA":       (6, 0),
    "FC":       (6, 0),
    "BC":       (1, 0),   # Bon de Commande CLIENT
    "AV":       (5, 0),   # Avoir vente (BA)
    "AVOIR":    (5, 0),
    "BC_ACHAT": (11, 1),  # Bon de Commande FOURNISSEUR (PC)
    "BL_ACHAT": (13, 1),  # Bon de Livraison / Réception ACHAT
    "FA_ACHAT": (16, 1),  # Facture fournisseur (FF)
}


# Raccourcis utiles
DOC_TYPE    = {k: v[0] for k, v in DOC_CODES.items()}
DOC_DOMAINE = {k: v[1] for k, v in DOC_CODES.items()}

# Types dont la création déclenche une sortie de stock
DOC_DESTOCKANTS = {"BL", "FACTURE", "FA", "FC"}

# Types dont la création alimente le stock
DOC_STOCKANTS = {"BF", "BL_ACHAT"}

# Préfixes de pièces utilisés par le moteur d'insertion
DOC_PREFIXES = {
    "BL": "BL",
    "FACTURE": "FA",
    "FA": "FA",
    "FC": "FA",
    "BC": "BC",
    "OF": "OF",
    "BF": "BF",
    "FF": "FF",
    "AV": "AV",
    "AVOIR": "AV",
    "BC_ACHAT": "PC",   # ← ajouté, cohérent avec la commande fournisseur
    "BL_ACHAT": "FBL",
    "FA_ACHAT": "FF",   # ← corrigé, était "AF" (le préfixe réel est FF)
}
# ─────────────────────────────────────────────────────────────────────
# COLONNES (référence pour éviter les typos)
# ─────────────────────────────────────────────────────────────────────
COL = {
    # F_COMPTET
    "CT_NUM":        "CT_Num",
    "CT_INTITULE":   "CT_Intitule",
    "CT_TYPE":       "CT_Type",        # 0=client, 1=fournisseur
    "CT_VALIDITE":   "CT_Validite",    # VALIDE | BLOQUE | SUSPECT
    "CT_ENCOURS_MAX":"CT_EncoursMax",  # Plafond crédit
    "CT_ENCOURS":    "CT_Encours",     # Encours courant
    # F_ARTICLE
    "AR_REF":        "AR_Ref",
    "AR_DESIGN":     "AR_Design",
    "AR_PRIXACH":    "AR_PrixAch",
    "AR_PRIXVEN":    "AR_PrixVen",
    # F_ARTSTOCK
    "AS_QTESTO":     "AS_QteSto",
    "AS_QTECOM":     "AS_QteCom",
    # F_DOCENTETE
    "DO_PIECE":      "DO_Piece",
    "DO_DOMAINE":    "DO_Domaine",
    "DO_TYPE":       "DO_Type",
    "DO_DATE":       "DO_Date",
    "DO_REF":        "DO_Ref",
    # F_DOCLIGNE
    "DL_LIGNE":      "DL_Ligne",
    "DL_QTE":        "DL_Qte",
    "DL_PU":         "DL_PrixUnitaire",
}

# ─────────────────────────────────────────────────────────────────────
# RÈGLES MÉTIER
# ─────────────────────────────────────────────────────────────────────
TVA_TAUX    = 0.19        # 19%
MARGE_ESTIMEE = 0.22      # 22% marge brute estimée
CURRENCY_CODE = "TND"
CURRENCY_SYMBOL = "TND"
CURRENCY_LABEL = "TND"

# Filtre standard factures de vente
SQL_FILTRE_FACTURES = "DO_Type = 6 AND DO_Domaine = 0"

# Filtre clients uniquement
SQL_FILTRE_CLIENTS = "CT_Type = 0"