"""schema_sage module defines the canonical Sage 100 ERP schema constants used throughout MCP servers, including document type codes, column name mappings, and business rule parameters."""

DOC_CODES = {
    "OF": (25, 2),
    "BL": (3, 0),
    "BF": (26, 2),
    "FACTURE": (6, 0),
    "FA": (6, 0),
    "FC": (6, 0),
    "BC": (1, 0),
    "AV": (5, 0),
    "AVOIR": (5, 0),
    "BC_ACHAT": (11, 1),
    "BL_ACHAT": (13, 1),
    "FA_ACHAT": (16, 1),
}

DOC_TYPE = {k: v[0] for k, v in DOC_CODES.items()}
DOC_DOMAINE = {k: v[1] for k, v in DOC_CODES.items()}

DOC_DESTOCKANTS = {"BL", "FACTURE", "FA", "FC"}

DOC_STOCKANTS = {"BF", "BL_ACHAT"}

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
    "BC_ACHAT": "PC",
    "BL_ACHAT": "FBL",
    "FA_ACHAT": "FF",
}

COL = {
    "CT_NUM": "CT_Num",
    "CT_INTITULE": "CT_Intitule",
    "CT_TYPE": "CT_Type",
    "CT_VALIDITE": "CT_Validite",
    "CT_ENCOURS_MAX": "CT_EncoursMax",
    "CT_ENCOURS": "CT_Encours",
    "AR_REF": "AR_Ref",
    "AR_DESIGN": "AR_Design",
    "AR_PRIXACH": "AR_PrixAch",
    "AR_PRIXVEN": "AR_PrixVen",
    "AS_QTESTO": "AS_QteSto",
    "AS_QTECOM": "AS_QteCom",
    "DO_PIECE": "DO_Piece",
    "DO_DOMAINE": "DO_Domaine",
    "DO_TYPE": "DO_Type",
    "DO_DATE": "DO_Date",
    "DO_REF": "DO_Ref",
    "DL_LIGNE": "DL_Ligne",
    "DL_QTE": "DL_Qte",
    "DL_PU": "DL_PrixUnitaire",
}

TVA_TAUX = 0.19
MARGE_ESTIMEE = 0.22
CURRENCY_CODE = "TND"
CURRENCY_SYMBOL = "TND"
CURRENCY_LABEL = "TND"

SQL_FILTRE_FACTURES = "DO_Type = 6 AND DO_Domaine = 0"
SQL_FILTRE_CLIENTS = "CT_Type = 0"