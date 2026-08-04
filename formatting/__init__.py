"""
formatting/ — Module de formatage des réponses.
Contient :
  - _formater_reponse_directe() : formatage selon action
  - _formater_nl2sql_brut() : formatage résultats NL2SQL
  - _formater_*() : formateurs spécialisés par action
  - _FORMATEURS_JSON : mapping action → formateur
"""

import json
import logging
from typing import Any

from api.common import (
    _safe_str, _ACTIONS_DEJA_TEXTE, _STATUTS_ERREUR_MCP,
    _STATUTS_ACTIONS_V3_OK, _FORMATEURS_JSON,
)

logger = logging.getLogger("sage.erp.formatting")


# ─────────────────────────────────────────────────────────────────────
# FORMATAGE GÉNÉRIQUE
# ─────────────────────────────────────────────────────────────────────
def _formater_reponse_directe(action: str, reponse_brute: str) -> str | None:
    """Formate la réponse selon l'action. Retourne None si pas de formatage."""
    if not reponse_brute or reponse_brute.startswith("__"):
        return None
    
    if action in _ACTIONS_DEJA_TEXTE:
        return reponse_brute
    
    if action in _FORMATEURS_JSON:
        try:
            data = json.loads(reponse_brute)
            formatter = _FORMATEURS_JSON[action]
            result = formatter(data)
            if result:
                return result
        except Exception as e:
            logger.warning("Formatting failed for %s: %s", action, _safe_str(e))
    
    return None


def _formater_nl2sql_brut(reponse_brute: str, question: str) -> str:
    """Formate une réponse NL2SQL brute en texte lisible."""
    try:
        data = json.loads(reponse_brute)
        if isinstance(data, list):
            if not data:
                return "Aucun résultat trouvé."
            if "erreur" in data[0]:
                return f"Erreur SQL : {data[0]['erreur']}"
            
            lignes = [f"📊 {len(data)} résultat(s) :", "─" * 50]
            for i, row in enumerate(data[:20], 1):
                parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                lignes.append(f"  {i}. " + " | ".join(parts))
            if len(data) > 20:
                lignes.append(f"  ... et {len(data) - 20} résultat(s) supplémentaire(s)")
            return "\n".join(lignes)
    except Exception:
        pass
    return reponse_brute


# ─────────────────────────────────────────────────────────────────────
# FORMATEURS SPÉCIALISÉS
# ─────────────────────────────────────────────────────────────────────
def _formater_liste_clients(data: dict) -> str:
    """Formate une liste de clients."""
    clients = data.get("clients", [])
    if not clients:
        return "Aucun client trouvé."
    
    lignes = [f"👥 {len(clients)} client(s) :", "─" * 50]
    for i, c in enumerate(clients[:20], 1):
        nom = c.get("CT_Intitule", c.get("CT_Num", "?"))
        encours = c.get("CT_Encours", 0)
        lignes.append(f"  {i}. {c.get('CT_Num', '?')} — {nom} (encours: {encours:.2f} €)")
    return "\n".join(lignes)


def _formater_liste_articles(data: dict) -> str:
    """Formate une liste d'articles."""
    articles = data.get("articles", [])
    if not articles:
        return "Aucun article trouvé."
    
    lignes = [f"📦 {len(articles)} article(s) :", "─" * 50]
    for i, a in enumerate(articles[:20], 1):
        ref = a.get("AR_Ref", "?")
        design = a.get("AR_Design", "")
        stock = a.get("stock", 0)
        prix = a.get("AR_PrixVen", 0)
        lignes.append(f"  {i}. {ref} — {design} (stock: {stock:.0f}, prix: {prix:.2f} €)")
    return "\n".join(lignes)


def _formater_top_clients(data: dict) -> str:
    """Formate le top clients par CA."""
    clients = data.get("clients", [])
    if not clients:
        return "Aucun client trouvé."
    
    lignes = [f"🏆 Top {len(clients)} clients par CA :", "─" * 50]
    for i, c in enumerate(clients, 1):
        nom = c.get("CT_Intitule", c.get("code_client", "?"))
        ca = c.get("ca_total", 0)
        nb = c.get("nb_factures", 0)
        lignes.append(f"  {i}. {nom} — CA: {ca:.2f} € ({nb} factures)")
    return "\n".join(lignes)


def _formater_factures(data: dict) -> str:
    """Formate une liste de factures."""
    factures = data.get("factures", [])
    if not factures:
        return "Aucune facture trouvée."
    
    lignes = [f"🧾 {len(factures)} facture(s) :", "─" * 50]
    for i, f in enumerate(factures[:20], 1):
        piece = f.get("DO_Piece", "?")
        date = f.get("DO_Date", "")
        montant = f.get("montant_ht", 0)
        statut = f.get("statut", "?")
        lignes.append(f"  {i}. {piece} — {date} — {montant:.2f} € [{statut}]")
    return "\n".join(lignes)


def _formater_fiche_client(data: dict) -> str:
    """Formate une fiche client."""
    if data.get("statut") == "NON_TROUVE":
        return f"❌ Client '{data.get('CT_Num', '')}' introuvable."
    
    lignes = [
        f"📋 Fiche client : {data.get('CT_Num', '?')}",
        f"   Intitulé : {data.get('CT_Intitule', '?')}",
        f"   Statut : {data.get('CT_Validite', '?')}",
        f"   Encours : {data.get('CT_Encours', 0):.2f} € / {data.get('CT_EncoursMax', 0):.2f} €",
        f"   CA Total : {data.get('CA_Total', 0):.2f} €",
        f"   Nb Factures : {data.get('NB_Factures', 0)}",
    ]
    return "\n".join(lignes)


def _formater_palmares(data: dict) -> str:
    """Formate le palmarès des articles."""
    palmares = data.get("palmares", [])
    if not palmares:
        return "Aucun article trouvé."
    
    lignes = [f"🏆 Palmarès des {len(palmares)} articles :", "─" * 50]
    for i, p in enumerate(palmares, 1):
        ref = p.get("AR_Ref", "?")
        design = p.get("AR_Design", "")
        qte = p.get("qte_vendue", 0)
        ca = p.get("ca_article", 0)
        lignes.append(f"  {i}. {ref} — {design} (qte: {qte:.0f}, CA: {ca:.2f} €)")
    return "\n".join(lignes)


def _formater_ca_global(data: dict) -> str:
    """Formate le CA global."""
    return (
        f"📊 Chiffre d'affaires global :\n"
        f"   CA HT : {data.get('ca_ht', 0):.2f} €\n"
        f"   TVA 19% : {data.get('tva_19', 0):.2f} €\n"
        f"   CA TTC : {data.get('ca_ttc', 0):.2f} €\n"
        f"   Nb factures : {data.get('nb_fa', 0)}\n"
        f"   Nb clients : {data.get('nb_clients', 0)}"
    )


def _formater_kpi(data: dict) -> str:
    """Formate les KPI."""
    return (
        f"📊 Dashboard KPI :\n"
        f"   CA Total : {data.get('ca_total', 0):.2f} €\n"
        f"   Marge 22% : {data.get('marge_22', 0):.2f} €\n"
        f"   Panier moyen : {data.get('panier_moy', 0):.2f} €\n"
        f"   Nb clients : {data.get('nb_clients', 0)}\n"
        f"   Nb documents : {data.get('nb_docs', 0)}\n"
        f"   Nb factures : {data.get('nb_factures', 0)}"
    )


def _formater_rentabilite(data: dict) -> str:
    """Formate l'analyse de rentabilité."""
    articles = data.get("articles", [])
    if not articles:
        return "Aucun article trouvé."
    
    lignes = [f"💰 Rentabilité des {len(articles)} articles :", "─" * 50]
    for i, a in enumerate(articles[:20], 1):
        ref = a.get("AR_Ref", "?")
        marge = a.get("marge_brute", 0)
        taux = a.get("taux_marge", 0)
        ca = a.get("ca_vente", 0)
        lignes.append(f"  {i}. {ref} — CA: {ca:.2f} €, Marge: {marge:.2f} € ({taux:.1f}%)")
    return "\n".join(lignes)


def _formater_saisonnalite(data: dict) -> str:
    """Formate la saisonnalité."""
    mois = data.get("mois", [])
    if not mois:
        return "Aucune donnée de saisonnalité."
    
    lignes = [f"📅 Saisonnalité sur {len(mois)} mois :", "─" * 50]
    for m in mois[:12]:
        mois_str = m.get("mois", "?")
        ca = m.get("ca_mensuel", 0)
        nb = m.get("nb_factures", 0)
        lignes.append(f"  {mois_str} : {ca:.2f} € ({nb} factures)")
    return "\n".join(lignes)


def _formater_dso(data: dict) -> str:
    """Formate le DSO."""
    return (
        f"⏱️  Délai de paiement (DSO) :\n"
        f"   DSO global : {data.get('dso_global', 0):.1f} jours\n"
        f"   Nb factures : {data.get('nb_factures', 0)}"
    )


def _formater_rfm(data: dict) -> str:
    """Formate l'analyse RFM."""
    clients = data.get("clients", [])
    if not clients:
        return "Aucun client trouvé."
    
    lignes = [f"📊 Analyse RFM ({len(clients)} clients) :", "─" * 50]
    for i, c in enumerate(clients[:20], 1):
        nom = c.get("CT_Intitule", c.get("CT_Num", "?"))
        ca = c.get("ca_total", 0)
        nb = c.get("nb_factures", 0)
        derniere = c.get("derniere_commande", "?")
        lignes.append(f"  {i}. {nom} — CA: {ca:.2f} €, Nb: {nb}, Dernière: {derniere}")
    return "\n".join(lignes)


def _formater_clients_baisse(data: dict) -> str:
    """Formate les clients en baisse."""
    clients = data.get("clients", [])
    if not clients:
        return "Aucun client en baisse détecté."
    
    lignes = [f"📉 {len(clients)} client(s) en baisse :", "─" * 50]
    for i, c in enumerate(clients[:20], 1):
        nom = c.get("CT_Intitule", c.get("CT_Num", "?"))
        variation = c.get("variation_pct", 0)
        ca_rec = c.get("ca_recent", 0)
        ca_anc = c.get("ca_ancien", 0)
        lignes.append(f"  {i}. {nom} — {variation:.1f}% ({ca_anc:.2f} € → {ca_rec:.2f} €)")
    return "\n".join(lignes)


def _formater_declaration(data: dict) -> str:
    """Formate la déclaration fiscale."""
    return (
        f"📄 Déclaration fiscale :\n"
        f"   CA HT : {data.get('ca_ht', 0):.2f} €\n"
        f"   TVA 19% : {data.get('tva_19', 0):.2f} €\n"
        f"   CA TTC : {data.get('ca_ttc', 0):.2f} €\n"
        f"   Nb factures : {data.get('nb_fa', 0)}\n"
        f"   Année : {data.get('annee', '?')}"
    )


def _formater_factures_impayees(data: dict) -> str:
    """Formate les factures impayées."""
    factures = data.get("factures", [])
    if not factures:
        return "Aucune facture impayée."
    
    total = data.get("total_du", 0)
    lignes = [f"⚠️  {len(factures)} facture(s) impayée(s) — Total : {total:.2f} €", "─" * 50]
    for i, f in enumerate(factures[:20], 1):
        piece = f.get("DO_Piece", "?")
        client = f.get("CT_Intitule", f.get("CT_Num", "?"))
        montant = f.get("montant_ht", 0)
        date = f.get("DO_Date", "")
        lignes.append(f"  {i}. {piece} — {client} — {montant:.2f} € ({date})")
    return "\n".join(lignes)


def _formater_factures_fourn_impayees(data: dict) -> str:
    """Formate les factures fournisseurs impayées."""
    factures = data.get("factures", [])
    if not factures:
        return "Aucune facture fournisseur impayée."
    
    total = data.get("total_du", 0)
    lignes = [f"⚠️  {len(factures)} facture(s) fournisseur impayée(s) — Total : {total:.2f} €", "─" * 50]
    for i, f in enumerate(factures[:20], 1):
        piece = f.get("DO_Piece", "?")
        fourn = f.get("CT_Intitule", f.get("CT_Num", "?"))
        montant = f.get("montant_ht", 0)
        date = f.get("DO_Date", "")
        lignes.append(f"  {i}. {piece} — {fourn} — {montant:.2f} € ({date})")
    return "\n".join(lignes)


def _formater_liste_fournisseurs(data: dict) -> str:
    """Formate la liste des fournisseurs."""
    fournisseurs = data.get("fournisseurs", [])
    if not fournisseurs:
        return "Aucun fournisseur trouvé."
    
    lignes = [f"🏭 {len(fournisseurs)} fournisseur(s) :", "─" * 50]
    for i, f in enumerate(fournisseurs[:20], 1):
        code = f.get("CT_Num", "?")
        nom = f.get("CT_Intitule", "")
        encours = f.get("encours", 0)
        maxi = f.get("encours_max", 0)
        lignes.append(f"  {i}. {code} — {nom} (encours: {encours:.2f} / {maxi:.2f} €)")
    return "\n".join(lignes)


# ─────────────────────────────────────────────────────────────────────
# REGISTRE DES FORMATEURS
# ─────────────────────────────────────────────────────────────────────
_FORMATEURS_JSON = {
    "LISTE_ARTICLES": _formater_liste_articles,
    "LISTE_CLIENTS": _formater_liste_clients,
    "TOP_CLIENTS": _formater_top_clients,
    "PALMARES_ARTICLES": _formater_palmares,
    "CA_GLOBAL": _formater_ca_global,
    "CLIENTS_BAISSE": _formater_clients_baisse,
    "FACTURES_NON_REGLEES": _formater_factures_impayees,
    "FACTURES_NON_REGLEES_FOURN": _formater_factures_fourn_impayees,
    "TOUTES_FACTURES_CLIENT": _formater_factures,
    "FICHE_CLIENT": _formater_fiche_client,
    "RENTABILITE": _formater_rentabilite,
    "SAISONNALITE": _formater_saisonnalite,
    "DSO": _formater_dso,
    "RFM": _formater_rfm,
    "DASHBOARD_EXCEL": _formater_kpi,
    "DECLARATION_EXCEL": _formater_declaration,
    "LISTE_FOURNISSEURS": _formater_liste_fournisseurs,
}