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
    "OF":      (1, 2),   # Ordre de Fabrication
    "BL":      (2, 0),   # Bon de Livraison (vente)
    "BF":      (2, 2),   # Bon de Fabrication
    "FACTURE": (3, 0),   # Facture de vente  ← CA et chiffre d'affaires
    "BC":      (4, 1),   # Bon de Commande fournisseur
    "AVOIR":   (5, 1),   # Facture d'avoir
}

# Raccourcis utiles
DOC_TYPE    = {k: v[0] for k, v in DOC_CODES.items()}
DOC_DOMAINE = {k: v[1] for k, v in DOC_CODES.items()}

# Types dont la création déclenche une sortie de stock
DOC_DESTOCKANTS = {"BL", "FACTURE"}

# Types dont la création alimente le stock
DOC_STOCKANTS = {"BF"}

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

# Filtre standard factures de vente
SQL_FILTRE_FACTURES = "DO_Type = 3 AND DO_Domaine = 0"

# Filtre clients uniquement
SQL_FILTRE_CLIENTS = "CT_Type = 0"