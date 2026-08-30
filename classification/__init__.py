"""
classification/ — Module de classification des intentions utilisateur.
Contient :
  - _PATTERNS_PRECLASS : patterns regex de pré-classification
  - _MARQUEURS_NL2SQL_FORCE_RE : marqueurs forçant NL2SQL_LIBRE
  - _pre_classifier() : fonction de pré-classification
  - _MARQUEURS_FALLBACK_GENERIQUE : marqueurs de fallback
  - _est_fallback_generique() : détection de fallback
"""

from api.common import _safe_str
import re

# ─────────────────────────────────────────────────────────────────────
# PRÉ-CLASSIFICATION REGEX
# ─────────────────────────────────────────────────────────────────────
_PATTERNS_PRECLASS = [
    # ══════════════════════════════════════════════════════════════
    # TRANSFORMER_DOC — PRIORITÉ ABSOLUE (avant GENERER_DOC)
    # ══════════════════════════════════════════════════════════════
    (r"transform[e\s]+.{0,60}\bof\b.{0,60}\bbf\b",            "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,60}\bbl\b.{0,60}facture",           "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,30}\bbc\b.{0,20}\bbl\b",            "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,15}(?:fa|bl|bc|of|bf)\d+",          "TRANSFORMER_DOC"),
    (r"transform[e\s]+.{0,15}[a-z]{2}\d{6,}",                 "TRANSFORMER_DOC"),
    (r"(?:transform|passe|converti).{0,30}num[eé]ro.{0,60}\b(?:of|bl|bc|bf|fa)[A-Z0-9]+.{0,20}\b(?:bf|bl|facture|bc)\b", "TRANSFORMER_DOC"),
    (r"convert[i\s]+.{0,30}(?:bl|of|bc).{0,20}(?:facture|bf|bl)", "TRANSFORMER_DOC"),
    (r"facturer\s+(?:le\s+)?bl\b",                             "TRANSFORMER_DOC"),
    (r"passer\s+(?:le\s+)?(?:bl|of)\b.{0,20}en\b",            "TRANSFORMER_DOC"),
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:la\s+|une\s+|le\s+|un\s+)?bf\s+(?:pour|de|à\s+partir\s+de)\s+.{0,10}\bof[a-z0-9]*\d+", "TRANSFORMER_DOC"),
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:la\s+|une\s+|le\s+|un\s+)?facture\s+(?:pour|de|à\s+partir\s+de)\s+.{0,10}\bbl[a-z0-9]*\d+", "TRANSFORMER_DOC"),
    # ── GÉNÉRATION DOCUMENTS ─────────────────────────────────────
    (r"bl\s+achat|bon\s+de\s+r[eé]ception|r[eé]ception\s+fournisseur|livraison\s+fournisseur", "GENERER_DOC"),
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+.{0,20}bl\s+achat", "GENERER_DOC"),
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+.{0,20}r[eé]ception\s+fournisseur", "GENERER_DOC"),
    # Priorité absolue : REGLEMENT (avant TOUTES_FACTURES_CLIENT)
    (r"r[eé]gler?\s+(la\s+|une\s+|les\s+)?(?:facture|fa)\s+[A-Z0-9]+",   "REGLEMENT"),
    (r"r[eé]glement\s+(?:de\s+la\s+)?(?:facture|fa)\s+[A-Z0-9]+",        "REGLEMENT"),
    (r"change.{0,30}(?:statut|status).{0,30}(?:facture|fa)\s+[A-Z0-9]+",  "REGLEMENT"),
    (r"marquer?\s+(?:la\s+)?(?:facture|fa)\s+[A-Z0-9]+.{0,30}r[eé]gl[eé]","REGLEMENT"),
    (r"(?:facture|fa)\s+([A-Z0-9]{3,})\s+.{0,20}r[eé]gl[eé]e?",          "REGLEMENT"),
    # BL
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?bl\b", "GENERER_DOC"),
    (r"\bbl\s+(pour|client|cli|de\s+\d)",                  "GENERER_DOC"),
    # OF
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?of\b", "GENERER_DOC"),
    (r"ordre\s+de\s+fabrication",                           "GENERER_DOC"),
    # BF
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?bf\b", "GENERER_DOC"),
    # FACTURE
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t)|[eé]tabli[rs])\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?facture", "GENERER_DOC"),
    # BC
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?bc\b", "GENERER_DOC"),
    (r"bon\s+de\s+commande",                                "GENERER_DOC"),
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?bon\b", "GENERER_DOC"),
    # ── ÉCRITURE CLIENTS ──────────────────────────────────────────
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation)\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+|un\s+nouveau\s+|nouveau\s+)?client", "CREER_CLIENT"),
    (r"enregistr(?:er?|ez?)\s+(?:un\s+|le\s+)?(?:nouveau\s+)?client",   "CREER_CLIENT"),
    (r"saisi[rs]?\s+(?:un\s+|le\s+)?(?:nouveau\s+)?client",             "CREER_CLIENT"),
    (r"nouveau\s+client",                                               "CREER_CLIENT"),
    (r"ajouter?\s+(un\s+)?client",                                      "CREER_CLIENT"),
    # ── CREER_FOURNISSEUR ──
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation)\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+|un\s+nouveau\s+|nouveau\s+)?fournisseur", "CREER_FOURNISSEUR"),
    (r"enregistr(?:er?|ez?)\s+(?:un\s+|le\s+)?(?:nouveau\s+)?fournisseur", "CREER_FOURNISSEUR"),
    (r"saisi[rs]?\s+(?:un\s+|le\s+)?(?:nouveau\s+)?fournisseur",       "CREER_FOURNISSEUR"),
    (r"nouveau\s+fournisseur",                                             "CREER_FOURNISSEUR"),
    (r"ajouter?\s+(un\s+)?fournisseur",                                   "CREER_FOURNISSEUR"),
    # ── MODIFIER_CLIENT / MODIFIER_FOURNISSEUR (flux guidé) ───────────
    (r"modifier?\s+(?:le\s+|un\s+|mon\s+)?client",             "MODIFIER_CLIENT"),
    (r"(?:changer?|mettre?\s+[àa]\s+jour|actualiser?|éditer?)\s+(?:le\s+|un\s+|mon\s+)?client", "MODIFIER_CLIENT"),
    (r"modifier?\s+(?:le\s+|un\s+|mon\s+)?fournisseur",        "MODIFIER_FOURNISSEUR"),
    (r"(?:changer?|mettre?\s+[àa]\s+jour|actualiser?|éditer?)\s+(?:le\s+|un\s+|mon\s+)?fournisseur", "MODIFIER_FOURNISSEUR"),
    # ── CREER_ARTICLE ─────────────────────────────────────────────
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation)\s+(?:d['\u2019]|de\s+|un\s+|une\s+|l['\u2019]|le\s+|la\s+|un\s+nouveau\s+|nouveau\s+)?articles?", "CREER_ARTICLE"),
    (r"enregistr(?:er?|ez?)\s+(?:un\s+|l['\u2019])?(?:nouveau\s+)?articles?", "CREER_ARTICLE"),
    (r"saisi[rs]?\s+(?:un\s+|l['\u2019])?(?:nouveau\s+)?articles?",           "CREER_ARTICLE"),
    (r"nouveau\s+articles?",                                             "CREER_ARTICLE"),
    (r"ajouter?\s+(un\s+)?articles?",                                    "CREER_ARTICLE"),
    # ── MODIFIER_STATUT (priorité avant FICHE pour éviter conflit) ──
    (r"bloquer?\s+(le\s+)?fournisseur",                     "MODIFIER_STATUT"),
    (r"d[eé]bloquer?\s+(le\s+)?fournisseur",                "MODIFIER_STATUT"),
    (r"r[eé]activer?\s+(le\s+)?fournisseur",                "MODIFIER_STATUT"),
    (r"bloquer?\s+(le\s+)?client",                          "MODIFIER_STATUT"),
    (r"d[eé]bloquer?\s+(le\s+)?client",                     "MODIFIER_STATUT"),
    (r"r[eé]activer?\s+(le\s+)?client",                     "MODIFIER_STATUT"),
    (r"modifier?\s+(le\s+)?statut",                         "MODIFIER_STATUT"),
    # ── AVOIR / RÈGLEMENT ─────────────────────────────────────────
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|fai(?:s|re|t))\s+(?:d['\u2019]|de\s+|un\s+|une\s+|le\s+|la\s+)?avoir", "CREER_AVOIR"),
    (r"r[eé]gler?\s+(la\s+|une\s+|les\s+)?factures?",       "REGLEMENT"),
    (r"r[eé]glement\s+(d.une\s+|de\s+la\s+)?facture",       "REGLEMENT"),
    (r"payer?\s+(la\s+|une\s+|les\s+)?factures?",           "REGLEMENT"),
    (r"payer?\s+(?:la\s+)?(?:facture\s+)?(?:FA|BL|BC|BF)\d+","REGLEMENT"),
    (r"paiement\s+(d.une\s+|de\s+la\s+)?facture",           "REGLEMENT"),
    (r"change.{0,20}statut.{0,20}facture.{0,20}r[eé]gl[eé]","REGLEMENT"),
    # ── KB ────────────────────────────────────────────────────────
    (r"r[eé]clamations?",                                    "RECHERCHE_PROCEDURE"),
    (r"motifs?\s+de\s+r[eé]clamation",                       "RECHERCHE_PROCEDURE"),
    (r"\bd[eé]fauts?\b",                                     "RECHERCHE_PROCEDURE"),
    (r"\bpannes?\b",                                         "RECHERCHE_PROCEDURE"),
    (r"\bsav\b",                                             "RECHERCHE_PROCEDURE"),
    (r"tol[eé]rance",                                        "RECHERCHE_PROCEDURE"),
    (r"proc[eé]d[eé]\s+de\s+fabrication",                    "RECHERCHE_PROCEDURE"),
    (r"\bmati[eè]re\b",                                      "RECHERCHE_PROCEDURE"),
    (r"temp[eé]rature",                                      "RECHERCHE_PROCEDURE"),
    (r"\bprocess\b",                                         "RECHERCHE_PROCEDURE"),
    (r"pr[eé]caution",                                       "RECHERCHE_PROCEDURE"),
    (r"garantie",                                            "RECHERCHE_PROCEDURE"),
    (r"\bremise\b",                                          "RECHERCHE_PROCEDURE"),
    (r"conditions?\s+(commerciales?|n[eé]goci[eé]es?)",      "RECHERCHE_PROCEDURE"),
    (r"command[eé]e?s?\s+par\s+email",                       "RECHERCHE_PROCEDURE"),
    (r"email\s+de\s+commande",                                "RECHERCHE_PROCEDURE"),
    # ── STATUT_CLIENT ─────────────────────────────────────────────
    (r"\bclient\b.{0,25}\best[\s-]il\s+bloqu[eé]",              "STATUT_CLIENT"),
    (r"\bclient\b.{0,25}\best[\s-]il\s+(?:actif|valide|suspect)","STATUT_CLIENT"),
    (r"le\s+client\s+[A-Z0-9]+\s+est[\s-]il",                    "STATUT_CLIENT"),
    # ── DOCUMENTS PAR TYPE ────────────────────────────────────────
    (r"(?:liste|donne|affiche|montre).{0,30}bons?\s+de\s+livraison",   "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,20}\bbl\b.{0,20}client",      "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,30}bons?\s+de\s+commande",    "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,30}bons?\s+de\s+fabrication", "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,30}ordres?\s+de\s+fabrication","NL2SQL_LIBRE"),
    (r"bons?\s+de\s+livraison\s+(?:du\s+|de\s+)?client",               "NL2SQL_LIBRE"),
    (r"\bbl\b.{0,30}(?:du\s+|de\s+)?client",                           "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre).{0,20}\bbl\b.{0,40}(?:mois|p[eé]riode|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)", "NL2SQL_LIBRE"),
    (r"\bbl\b.{0,20}(?:du\s+mois|de\s+(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre))", "NL2SQL_LIBRE"),
    (r"bons?\s+de\s+livraison.{0,40}(?:mois|p[eé]riode|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)", "NL2SQL_LIBRE"),
    # ── REQUÊTES ANALYTIQUES ─────────────────────────────────────
    (r"articles?\s+(?:qui\s+)?co[uû]tent\s+(?:plus|moins)\s+(?:de|que)\s*\d+", "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec).{0,20}prix.{0,20}(?:sup[eé]r|inf[eé]r|plus|moins|>|<)\s*\d+", "NL2SQL_LIBRE"),
    (r"factures?\s+(?:sup[eé]rieure?s?\s+[àa]|plus\s+(?:de|que)|>\s*)\s*\d+",  "NL2SQL_LIBRE"),
    (r"factures?\s+(?:inf[eé]rieure?s?\s+[àa]|moins\s+(?:de|que)|<\s*)\s*\d+", "NL2SQL_LIBRE"),
    (r"factures?\s+entre\s+\d+\s+et\s+\d+",                                     "NL2SQL_LIBRE"),
    (r"clients?\s+(?:ayant|avec|qui\s+ont)\s+(?:des?\s+)?factures?",             "NL2SQL_LIBRE"),
    (r"clients?\s+(?:dont|avec)\s+(?:un\s+)?(?:ca|chiffre).{0,30}\d+",          "NL2SQL_LIBRE"),
    (r"clients?\s+(?:dont|avec)\s+(?:un\s+)?encours.{0,30}\d+",                 "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec).{0,30}stock.{0,20}\d+",                  "NL2SQL_LIBRE"),
    (r"articles?.{0,20}stock.{0,20}(?:inf[eé]r|sup[eé]r|<|>)\s*\d+",       "NL2SQL_LIBRE"),
    (r"articles?\s+(?:vendus?|achet[eé]s?)\s+(?:plus|moins)\s+(?:de|que)\s+\d+","NL2SQL_LIBRE"),
    (r"top\s+\d+\s+(?!clients?)(?:articles?|produits?|références?)",              "NL2SQL_LIBRE"),
    (r"(?:liste|donne|affiche|montre)\s+.{0,40}(?:o[ùu]|mais|dont|sauf|seulement|uniquement|filtre)", "NL2SQL_LIBRE"),
    # ── FACTURES PAR PÉRIODE ─────────────────────────────────────
    (r"factures?\s+(?:du\s+|de\s+|d['\u2019]?\s*)?(?:mois\s+(?:de\s+)?)?(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|jan|fév|mar|avr|jun|jul|aoû|sep|oct|nov|déc)", "NL2SQL_LIBRE"),
    (r"factures?\s+(?:du\s+)?mois\s+\d{1,2}",              "NL2SQL_LIBRE"),
    (r"factures?\s+(?:de\s+)?(?:l['\u2019]ann[eé]e|\d{4})", "NL2SQL_LIBRE"),
    (r"factures?\s+(?:du\s+|de\s+)?(?:trimestre|semestre)", "NL2SQL_LIBRE"),
    (r"(?:liste|affiche|montre|donne).{0,30}factures?.{0,30}(?:mois|ann[eé]e|p[eé]riode|semaine)", "NL2SQL_LIBRE"),
    (r"(?:liste|affiche|montre|donne).{0,30}factures?.{0,30}(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)", "NL2SQL_LIBRE"),
    # ── ARTICLES AVEC FILTRE PRIX / QUANTITÉ ─────────────────────
    (r"articles?\s+(?:dont|avec|au|ayant).{0,30}(?:prix|tarif|co[uû]t).{0,30}(?:sup[eé]r|inf[eé]r|d[eé]passe|plus|moins|\>|\<)", "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec).{0,30}prix.{0,30}\d+",    "NL2SQL_LIBRE"),
    (r"(?:prix|tarif)\s+(?:de\s+vente|d['\u2019]achat).{0,30}(?:sup[eé]r|inf[eé]r|d[eé]passe|plus|moins|\>|\<)", "NL2SQL_LIBRE"),
    (r"articles?\s+(?:dont|avec).{0,30}(?:marge|rentabilit)", "NL2SQL_LIBRE"),
    # ── CLIENTS AVEC FILTRE QUANTITATIF ──────────────────────────
    (r"clients?\s+(?:qui\s+ont|ayant|avec).{0,30}(?:plus\s+de|plus\s+qu[e'\u2019]|au\s+moins)\s+\d+\s+(?:commandes?|factures?|achats?)", "NL2SQL_LIBRE"),
    (r"clients?\s+(?:qui\s+ont|ayant|avec).{0,30}(?:moins\s+de|moins\s+qu[e'\u2019])\s+\d+\s+(?:commandes?|factures?|achats?)", "NL2SQL_LIBRE"),
    (r"clients?\s+(?:qui\s+n['\u2019]ont\s+pas|sans|aucune?)\s+(?:commandes?|factures?)", "NL2SQL_LIBRE"),
    (r"clients?\s+(?:pass[eé]|effectu[eé]).{0,20}(?:plus\s+de|au\s+moins)\s+\d+\s+(?:commandes?|achats?)", "NL2SQL_LIBRE"),
    # ── CLASSEMENT PAR NOMBRE DE COMMANDES ───────────────────────
    (r"clas(?:se|sement|s[eé])\s+.{0,30}clients?.{0,30}(?:nombre|nb)\s+(?:de\s+)?commandes?", "NL2SQL_LIBRE"),
    (r"clients?.{0,30}(?:tri[eé]s?|class[eé]s?|ordonn[eé]s?|rang[eé]s?).{0,30}(?:nombre|nb).{0,20}commandes?", "NL2SQL_LIBRE"),
    (r"clients?.{0,30}par\s+(?:nombre|nb)\s+(?:de\s+)?commandes?",    "NL2SQL_LIBRE"),
    (r"(?:nombre|nb)\s+(?:de\s+)?commandes?\s+(?:par\s+)?client",     "NL2SQL_LIBRE"),
    (r"qui\s+(?:commande|achète|a\s+achet[eé])\s+le\s+plus",           "NL2SQL_LIBRE"),
    (r"clas(?:se|ser|s[eé]s?)\s+les\s+clients?\s+.{0,30}(?:chiffre|ca\b)", "NL2SQL_LIBRE"),
    # ── ARTICLES STOCK SEUIL + COMMANDÉS ─────────────────────────
    (r"articles?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique).{0,40}command[eé]s?", "NL2SQL_LIBRE"),
    (r"articles?.{0,40}command[eé]s?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique)", "NL2SQL_LIBRE"),
    (r"articles?.{0,30}(?:stock\s+(?:faible|bas|insuffisant|inf[eé]r|critique)|sous.{0,10}seuil).{0,40}(?:command[eé]|achet[eé])", "NL2SQL_LIBRE"),
    (r"rupture.{0,20}command[eé]|command[eé].{0,20}rupture",           "NL2SQL_LIBRE"),
    # ── CLIENTS AVEC FILTRE QUANTITATIF SUR FACTURES + ENCOURS ───
    (r"clients?\s+(?:actifs?|avec|ayant|dont).{0,80}(?:factures?\s+impay[eé]es?|encours|ca\b)", "NL2SQL_LIBRE"),
    (r"clients?.{0,50}(?:encours\s+sup[eé]r|encours\s+>\s*\d+|encours\s+plus)", "NL2SQL_LIBRE"),
    # ── CLIENTS BLOQUÉS / INACTIFS ────────────────────────────────
    (r"clients?\s+bloqu[eé]s?",                             "NL2SQL_LIBRE"),
    (r"bloqu[eé]s?\s+clients?",                             "NL2SQL_LIBRE"),
    (r"quels?\s+clients?.{0,30}bloqu[eé]",                  "NL2SQL_LIBRE"),
    (r"clients?\s+inactifs?",                               "NL2SQL_LIBRE"),
    (r"clients?\s+sans\s+commande",                         "NL2SQL_LIBRE"),
    # ── ENCOURS CLIENT ────────────────────────────────────────────
    (r"encours\s+(du\s+|de\s+|d['\u2019]?\s*)?client",     "NL2SQL_LIBRE"),
    (r"cr[eé]dit\s+(du\s+)?client",                        "NL2SQL_LIBRE"),
    (r"solde\s+(du\s+)?client",                             "NL2SQL_LIBRE"),
    (r"limite\s+(du\s+)?client",                            "NL2SQL_LIBRE"),
    # ── ENCOURS FOURNISSEUR ──────────────────────────────────────
    (r"encours\s+(du\s+|de\s+|d['\u2019]?\s*)?fournisseur", "NL2SQL_LIBRE"),
    (r"cr[eé]dit\s+(du\s+|de\s+|d['\u2019]?\s*)?fournisseur", "NL2SQL_LIBRE"),
    (r"solde\s+(du\s+|de\s+|d['\u2019]?\s*)?fournisseur", "NL2SQL_LIBRE"),
    (r"limite\s+(du\s+|de\s+|d['\u2019]?\s*)?fournisseur", "NL2SQL_LIBRE"),
    # ── FOURNISSEURS ──────────────────────────────────────────────
    (r"liste\s+(tous\s+)?(les\s+)?fournisseurs?",   "LISTE_FOURNISSEURS"),
    (r"(tous|toutes)\s+(les\s+)?fournisseurs?",      "LISTE_FOURNISSEURS"),
    (r"affiche\s+(les\s+)?fournisseurs?",            "LISTE_FOURNISSEURS"),
    (r"montre\s+(moi\s+)?(les\s+)?fournisseurs?",    "LISTE_FOURNISSEURS"),
    (r"donne\s+(moi\s+)?(les\s+)?fournisseurs?",     "LISTE_FOURNISSEURS"),
    (r"fiche\s+(du\s+|de\s+)?fournisseur",           "FICHE_FOURNISSEUR"),
    (r"info\w*\s+(sur\s+)?(le\s+)?fournisseur",      "FICHE_FOURNISSEUR"),
    (r"fournisseurs?\s+actifs?",                     "LISTE_FOURNISSEURS"),
    (r"quels?\s+fournisseurs?",                      "LISTE_FOURNISSEURS"),
    (r"top\s*\d*\s*fournisseurs?",                   "TOP_FOURNISSEURS"),
    (r"meilleurs?\s+fournisseurs?",                  "TOP_FOURNISSEURS"),
    (r"achats?\s+(par\s+)?fournisseur",              "TOP_FOURNISSEURS"),
    (r"commandes?\s+(chez|aupres|auprès)\s+",        "NL2SQL_LIBRE"),
    (r"bons?\s+de\s+commande\s+(du\s+|de\s+)?fournisseur", "NL2SQL_LIBRE"),
    # ── LISTE_CLIENTS ─────────────────────────────────────────────
    (r"liste\s+(tous\s+)?(les\s+|des\s+)?clients?",         "LISTE_CLIENTS"),
    (r"(tous|toutes)\s+(les\s+)?clients?",                  "LISTE_CLIENTS"),
    (r"affiche\s+(les\s+)?clients?",                        "LISTE_CLIENTS"),
    (r"montre\s+(moi\s+)?(les\s+)?clients?",                "LISTE_CLIENTS"),
    (r"donne\s+(moi\s+)?(les\s+)?clients?",                 "LISTE_CLIENTS"),
    (r"clients?\s+actifs?",                                 "LISTE_CLIENTS"),
    (r"quels?\s+clients?",                                  "LISTE_CLIENTS"),
    # ── TOP_CLIENTS ───────────────────────────────────────────────
    (r"top\s*\d*\s*clients?",                               "TOP_CLIENTS"),
    (r"meilleurs?\s+clients?",                              "TOP_CLIENTS"),
    (r"clients?\s+(par\s+)?ca\b",                           "TOP_CLIENTS"),
    # ── FICHE_CLIENT ──────────────────────────────────────────────
    (r"fiche\s+(du\s+|de\s+|d['\u2019]?\s*)?client",       "FICHE_CLIENT"),
    (r"info\w*\s+(sur\s+)?(le\s+)?client",                 "FICHE_CLIENT"),
    (r"d[eé]tail\s+(du\s+)?client",                        "FICHE_CLIENT"),
    (r"profil\s+(du\s+)?client",                           "FICHE_CLIENT"),
    # ── STATUT_CLIENT ─────────────────────────────────────────────
    (r"statut\s+(du\s+|de\s+)?client",                     "STATUT_CLIENT"),
    (r"client\s+est.il\s+bloqu[eé]",                       "STATUT_CLIENT"),
    # ── MODIFIER ENTITÉS ──────────────────────────────────────────
    (r"(?:modifier?\s+(?:le\s+|la\s+|l['\u2019]?\s*|l\s+|un\s+|une\s+)?(?:client|fournisseur|article|produit))", "MODIFIER_ENTITE"),
    (r"(?:changer?\s+(?:le\s+|la\s+|l['\u2019]?\s*|l\s+|un\s+|une\s+)?(?:client|fournisseur|article|produit))", "MODIFIER_ENTITE"),
    (r"(?:mettre\s+à\s+jour\s+(?:le\s+|la\s+|l['\u2019]?\s*|l\s+|un\s+|une\s+)?(?:client|fournisseur|article|produit))", "MODIFIER_ENTITE"),
    (r"(?:modifier?\s+(?:le\s+|la\s+|l['\u2019]?\s*|l\s+|un\s+|une\s+)?infos?\s+(?:du\s+|de\s+|d['\u2019]?)?(?:client|fournisseur|article|produit))", "MODIFIER_ENTITE"),
    (r"(?:modifier?\s+(?:le\s+|la\s+|l['\u2019]?\s*|l\s+|un\s+|une\s+)?fiche\s+(?:du\s+|de\s+|d['\u2019]?)?(?:client|fournisseur|article|produit))", "MODIFIER_ENTITE"),
    # ── LISTE_ARTICLES ────────────────────────────────────────────
    (r"produits?\s+finis?|articles?\s+finis?",              "NL2SQL_LIBRE"),
    (r"mati[èe]res?\s+premi[eè]res?|mati[èe]re\s+premi[eè]re", "NL2SQL_LIBRE"),
    (r"liste\s+(tous\s+)?(les\s+)?articles?",              "LISTE_ARTICLES"),
    (r"(tous|toutes)\s+(les\s+)?articles?",                "LISTE_ARTICLES"),
    (r"catalogue\s*(articles?|produits?)?",                "LISTE_ARTICLES"),
    (r"tous\s+(les\s+)?produits?",                         "LISTE_ARTICLES"),
    (r"affiche\s+(les\s+)?articles?",                      "LISTE_ARTICLES"),
    (r"liste\s+(les\s+)?produits?",                        "LISTE_ARTICLES"),
    # ── VERIFIER_STOCK ────────────────────────────────────────────
    (r"articles?\s+en\s+rupture",                              "VERIFIER_STOCK"),
    (r"rupture\s+de\s+stock",                                  "VERIFIER_STOCK"),
    (r"stock\s+(?:disponible|actuel|restant)\s+de\s+l['\u2019]article", "VERIFIER_STOCK"),
    (r"stock\s+de\s+l['\u2019]article",                        "VERIFIER_STOCK"),
    (r"quel\s+est\s+le\s+stock",                               "VERIFIER_STOCK"),
    (r"stock\s+(?:disponible|actuel|restant)",                  "VERIFIER_STOCK"),
    (r"combien\s+(?:de\s+)?stock",                             "VERIFIER_STOCK"),
    (r"anomalies?\s+.{0,20}stocks?",                           "NL2SQL_LIBRE"),
    (r"stock\s+n[eé]gatif",                                    "NL2SQL_LIBRE"),
    # ── CLIENTS AVEC FILTRE TEMPOREL ─────────────────────────────
    (r"clients?.{0,50}n['\u2019]ont\s+pas\s+command[eé]",     "NL2SQL_LIBRE"),
    (r"clients?.{0,30}(?:pas\s+command[eé]|pas\s+achet[eé]).{0,30}(?:depuis|\d+\s+mois)", "NL2SQL_LIBRE"),
    (r"quels?\s+clients?.{0,50}(?:depuis\s+\d+|depuis\s+(?:un|une|deux|trois|\d+)\s+mois)", "NL2SQL_LIBRE"),
    (r"clients?.{0,20}inactifs?.{0,20}(?:depuis|mois|\d+)",   "NL2SQL_LIBRE"),
    (r"ca\s+(global|total)",                               "CA_GLOBAL"),
    (r"chiffre\s+d.affaires?\s+(global|total)",            "CA_GLOBAL"),
    (r"chiffre\s+d.affaires?\s+global",                    "CA_GLOBAL"),
    # ── SAISONNALITE ──────────────────────────────────────────────
    (r"ca\s+(par\s+)?mois",                                "SAISONNALITE"),
    (r"ca\s+mensuel",                                      "SAISONNALITE"),
    (r"chiffre\s+d.affaires?\s+(par\s+)?mois",             "SAISONNALITE"),
    # ── FACTURES_NON_REGLEES_FOURN ─────────────────────────────────
    (r"factures?\s+(non\s+r[eé]gl[eé]es?|impay[eé]es?|en\s+attente).{0,30}fournisseur", "FACTURES_NON_REGLEES_FOURN"),
    (r"fournisseur.{0,30}factures?\s+(non\s+r[eé]gl[eé]es?|impay[eé]es?|en\s+attente)", "FACTURES_NON_REGLEES_FOURN"),
    (r"impay[eé]es?.{0,20}fournisseur",  "FACTURES_NON_REGLEES_FOURN"),
    (r"fournisseur.{0,20}impay[eé]es?",  "FACTURES_NON_REGLEES_FOURN"),
    (r"achats?\s+(non\s+r[eé]gl[eé]s?|impay[eé]s?)", "FACTURES_NON_REGLEES_FOURN"),
    # ── FACTURES_NON_REGLEES ──────────────────────────────────────
    (r"factures?\s+(non\s+r[eé]gl|impay|en\s+attente)",    "FACTURES_NON_REGLEES"),
    (r"(impay[eé]es?|non\s+r[eé]gl[eé]es?)",               "FACTURES_NON_REGLEES"),
    # ── LISTE GLOBALE FACTURES ────────────────────────────────────
    (r"listes?\s+(toutes?\s+)?(des\s+|les\s+)?factures?(?:\s+compl[eè]tes?)?\s*$", "NL2SQL_LIBRE"),
    (r"(?:affiche|montre|donne)\s+(toutes?\s+)?(des\s+|les\s+)?factures?(?:\s+compl[eè]tes?)?$", "NL2SQL_LIBRE"),
    (r"toutes?\s+(des\s+|les\s+)?factures?(?:\s+compl[eè]tes?)?$", "NL2SQL_LIBRE"),
    (r"listes?\s+(des\s+|les\s+)?factures?\s+d[\s']un\s+fournisseur\s+pr[eé]cis", "NL2SQL_LIBRE"),
    (r"toutes?\s+les?\s+factures?\s+(du\s+|de\s+)?fournisseur", "NL2SQL_LIBRE"),
    (r"factures?\s+(du\s+|de\s+)?fournisseur",                  "NL2SQL_LIBRE"),
    # ── TOUTES_FACTURES_CLIENT ────────────────────────────────────
    (r"toutes?\s+les?\s+factures?\s+(du\s+|de\s+)?client", "TOUTES_FACTURES_CLIENT"),
    (r"factures?\s+du\s+client",                           "TOUTES_FACTURES_CLIENT"),
    # ── DSO ───────────────────────────────────────────────────────
    (r"(d[eé]lai|dso|retard)\s+(de\s+)?paiement",         "DSO"),
    (r"\bdso\b",                                           "DSO"),
    # ── RFM ───────────────────────────────────────────────────────
    (r"\brfm\b",                                           "RFM"),
    (r"analyse\s+rfm",                                     "RFM"),
    (r"segmentation\s+clients?",                           "RFM"),
    # ── DÉCLARATION ───────────────────────────────────────────────
    (r"d[eé]claration\s*(fiscale|tva|mensuelle)?", "DECLARATION_EXCEL"),
    (r"(?:cr[eé][eé]?(?:r|er|z)?|cr[eé]ation|g[ée]n[ée]r\w*|exporte?(?:r|z)?)\s+.{0,15}d[eé]claration", "DECLARATION_EXCEL"),
    # ── DASHBOARD_EXCEL ───────────────────────────────────────────
    (r"tableau\s+de\s+bord",                               "DASHBOARD_EXCEL"),
    (r"\bdashboard\b",                                     "DASHBOARD_EXCEL"),
    (r"\bkpi\b",                                           "DASHBOARD_EXCEL"),
    (r"r[eé]sum[eé]\s+(g[eé]n[eé]ral|global)?",            "DASHBOARD_EXCEL"),
    # ── PALMARES_ARTICLES ─────────────────────────────────────────
    (r"palm[aà]r[eè]s",                                    "PALMARES_ARTICLES"),
    (r"articles?\s+les?\s+plus?\s+vendus?",                "PALMARES_ARTICLES"),
    (r"meilleurs?\s+articles?",                            "PALMARES_ARTICLES"),
    # ── RENTABILITE ───────────────────────────────────────────────
    (r"marge\s+(brute\s+)?par\s+article",                  "RENTABILITE"),
    (r"rentabilit[eé]\s+(des?\s+)?articles?",              "RENTABILITE"),
    (r"taux\s+de\s+marge",                                 "RENTABILITE"),
    # ── CLIENTS_BAISSE ────────────────────────────────────────────
    (r"clients?\s+en\s+baisse",                            "CLIENTS_BAISSE"),
    (r"clients?\s+baisse\s+ca",                            "CLIENTS_BAISSE"),
    # ── DOCS_PERIODE ──────────────────────────────────────────────
    (r"documents?\s+entre\s+\d{4}",                       "DOCS_PERIODE"),
    (r"documents?\s+du\s+\d{4}",                          "DOCS_PERIODE"),
    # ── BON DE LIVRAISON / FABRICATION générique ────────────────
    (r"bon\s+de\s+livraison",                               "GENERER_DOC"),
    (r"bon\s+de\s+fabrication",                             "GENERER_DOC"),
]


_MARQUEURS_NL2SQL_FORCE = {
    "mois par mois", "par mois", "évolution", "tendance",
    "uniquement", "seulement", "n'ont pas", "aucune commande",
    "depuis plus de", "inactifs", "croisement", "en commun",
    "meilleurs clients", "top.*client.*fourni", "vendus à un seul",
    "having", "ratio", "panier moyen", "taux de",
    "par nombre de commandes", "nombre de commandes",
    "commandés ce mois", "commandé ce mois",
    "inférieur au seuil", "stock insuffisant", "trier par commandes",
    "classement", "classé",
    "classe", "classer", "classés", "classee", "classees",
}

_MARQUEURS_NL2SQL_FORCE_RE = [
    re.compile(r"\b" + re.escape(m) + r"\b", re.IGNORECASE)
    for m in _MARQUEURS_NL2SQL_FORCE
]


_RX_ARTICLES_VENDUS_PERIODE = re.compile(
    r"(articles?\s+les?\s+plus?\s+vendus?|meilleurs?\s+articles?|palmar[eè]s)"
    r".{0,40}(ce\s+mois|cette\s+semaine|cette\s+ann[eé]e|en\s+\d{4}|du\s+mois)"
    r"|(ce\s+mois|cette\s+semaine|cette\s+ann[eé]e|en\s+\d{4}|du\s+mois)"
    r".{0,40}(articles?\s+les?\s+plus?\s+vendus?|meilleurs?\s+articles?|palmar[eè]s)",
    re.IGNORECASE,
)

_RX_CA_AVEC_PERIODE = re.compile(
    r"(chiffre\s+d.affaires?|\bca\b|\bfactures?\b)"
    r".{0,60}(mois\s+dernier|semaine\s+derni[eè]re|ann[eé]e\s+derni[eè]re|"
    r"cette\s+semaine|ce\s+mois|cette\s+ann[eé]e|trimestre|semestre|"
    r"compar[eé]|par\s+rapport|\bvs\b|entre\s+\d{4}-\d{2}-\d{2})"
    r"|(mois\s+dernier|semaine\s+derni[eè]re|ann[eé]e\s+derni[eè]re|"
    r"cette\s+semaine|ce\s+mois|cette\s+ann[eé]e|trimestre|semestre|"
    r"compar[eé]|par\s+rapport)"
    r".{0,60}(chiffre\s+d.affaires?|\bca\b|\bfactures?\b)",
    re.IGNORECASE,
)

def _pre_classifier(question: str) -> str | None:
    """Pré-classification rapide par regex (0ms)."""
    q = question.lower().strip()
    if _RX_ARTICLES_VENDUS_PERIODE.search(q):
        return "NL2SQL_LIBRE"
    if _RX_CA_AVEC_PERIODE.search(q):
        return "NL2SQL_LIBRE"
    if any(p.search(q) for p in _MARQUEURS_NL2SQL_FORCE_RE):
        return "NL2SQL_LIBRE"
    for pattern, action in _PATTERNS_PRECLASS:
        if re.search(pattern, q, re.IGNORECASE):
            return action
    if any(p.search(q) for p in _MARQUEURS_NL2SQL_FORCE_RE):
        return "NL2SQL_LIBRE"
    return None


# ─────────────────────────────────────────────────────────────────────
# FALLBACK
# ─────────────────────────────────────────────────────────────────────
_MARQUEURS_FALLBACK_GENERIQUE = (
    "aucun pattern sql trouvé", "résumé général", "resume general",
)


def _est_fallback_generique(rb: str) -> bool:
    """Détecte si la réponse est un fallback générique."""
    if not rb:
        return False
    return any(m in rb.lower() for m in _MARQUEURS_FALLBACK_GENERIQUE)