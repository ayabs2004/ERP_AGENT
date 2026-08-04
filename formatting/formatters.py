"""
formatters.py — Formatteurs de réponses ERP
Contient EXACTEMENT les versions "clés logiques" utilisées par l'orchestrateur
(c.get("code", c.get("CT_Num", "?")), data.get('code',''), data.get('nom',''), ...)
"""
import json


def _safe_str(obj) -> str:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj).encode("utf-8", errors="replace").decode("utf-8")


_STATUTS_ACTIONS_V3_OK = {
    "GENERE", "TRANSFORME", "CREE", "MODIFIE",
    "REGLE", "MOUVEMENT_ENREGISTRE", "INCHANGE",
}
_STATUTS_ERREUR_MCP = {
    "CLIENT_NON_TROUVE", "ARTICLE_NON_TROUVE", "STOCK_INSUFFISANT",
    "CLIENT_BLOQUE", "COMPOSANTS_INSUFFISANTS", "NON_TROUVE",
    "EXISTE_DEJA", "ERREUR",
}
_ACTIONS_DEJA_TEXTE: set[str] = {
    "VERIFIER_STOCK", "STATUT_CLIENT",
}


def _formater_liste_articles(data: dict) -> str:
    articles = data.get("articles", [])
    nb       = data.get("nb_articles", len(articles))
    if not articles:
        return "📦 Aucun article dans le catalogue."
    lignes = [f"📦 Catalogue articles — {nb} référence(s) :\n", "─" * 55]
    for a in articles:
        stock  = a.get("stock", 0)
        alerte = " ⚠️ RUPTURE" if stock <= 0 else (" ⚠️ FAIBLE" if stock < 5 else "")
        lignes.append(
            f"  • {a['ref']:<14} │ {a['designation']:<30} │ "
            f"Prix: {a.get('prix_vente', 0):>8.2f} € │ Stock: {stock:>5.0f} u{alerte}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_liste_clients(data: dict) -> str:
    clients = data.get("clients", [])
    nb      = data.get("nb_clients", len(clients))
    if not clients:
        return "👥 Aucun client actif."
    lignes = [f"👥 Clients actifs — {nb} client(s) :\n", "─" * 55]
    for c in clients:
        # Clés logiques ('code', 'nom', 'validite') renvoyées par lister_clients_actifs()
        # via db_adapter — fallback CT_* conservé pour compat avec d'anciens appelants
        code   = c.get("code", c.get("CT_Num", "?"))
        nom    = c.get("nom", c.get("CT_Intitule", "?"))
        statut = (c.get("validite") or c.get("CT_Validite") or c.get("statut") or "VALIDE").upper()
        icone  = "🔴" if statut == "BLOQUE" else "🟡" if statut == "SUSPECT" else "🟢"
        lignes.append(
            f"  {icone} {code:<10} │ {nom:<30} │ "
            f"CA: {c.get('ca_total', 0):>10.2f} € │ Fct: {c.get('nb_factures', 0)}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_top_clients(data: dict) -> str:
    clients = data.get("clients", [])
    top_n   = data.get("top_n", len(clients))
    if not clients:
        return "📊 Aucune donnée clients."
    lignes = [f"🏆 Top {top_n} clients par CA :\n", "─" * 55]
    for c in clients:
        lignes.append(
            f"  #{c.get('rang', '?'):<3} {c['code_client']:<10} │ "
            f"{c.get('nom_client', ''):<30} │ "
            f"CA: {c.get('ca_total', 0):>10.2f} € │ Fct: {c.get('nb_factures', 0)}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_factures(data: dict) -> str:
    factures  = data.get("factures", [])
    nb        = data.get("nb_factures", len(factures))
    intitule  = data.get("nom", data.get("code", ""))
    total_ht  = data.get("total_ht", 0)
    total_att = data.get("total_en_attente", 0)
    total_reg = data.get("total_regle", 0)
    if not factures:
        return f"🧾 Aucune facture pour '{intitule}'."
    lignes = [
        f"🧾 Factures de '{intitule}' — {nb} facture(s) :",
        f"   Total HT        : {total_ht:,.2f} €",
        f"   Total réglé     : {total_reg:,.2f} €",
        f"   Total en attente: {total_att:,.2f} €",
        "─" * 55,
    ]
    for f in factures:
        regle = f.get("regle", False) or f.get("statut", "") == "RÉGLÉE"
        icone = "✅" if regle else "⏳"
        mnt   = f.get("montant_ht", 0) or 0
        lignes.append(
            f"  {icone} {f['piece']:<16} │ "
            f"{f.get('date', ''):<12} │ "
            f"{mnt:>8.2f} € │ {'RÉGLÉE' if regle else 'EN ATTENTE'}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_factures_fourn_impayees(data: dict) -> str:
    factures = data.get("factures", [])
    nb       = data.get("nb_factures", len(factures))
    total_du = data.get("total_du", 0)
    ct_num   = data.get("code", "")
    titre    = f"Factures fournisseur {ct_num}" if ct_num else "Toutes factures fournisseurs"
    if not factures:
        return f"✅ Aucune facture fournisseur impayée{' pour ' + ct_num if ct_num else ''}."
    lignes = [
        f"🧾  {titre} — non réglées : {nb} facture(s)",
        f"   Total dû : {total_du:,.2f} €",
        "─" * 65,
    ]
    for f in factures:
        mnt = f.get("montant_ht", 0) or 0
        lignes.append(
            f"  ⏳ {f['piece']:<16} │ "
            f"{f.get('nom', f.get('code', '')):<25} │ "
            f"{f.get('date', ''):<12} │ {mnt:>10.2f} €"
        )
    lignes.append("─" * 65)
    return "\n".join(lignes)


def _formater_factures_impayees(data: dict) -> str:
    factures = data.get("factures", [])
    nb       = data.get("nb_factures", len(factures))
    total_du = data.get("total_du", 0)
    if not factures:
        return "✅ Aucune facture impayée."
    lignes = [
        f"⚠️  Factures impayées — {nb} facture(s)",
        f"   Total dû : {total_du:,.2f} €",
        "─" * 55,
    ]
    for f in factures:
        mnt = f.get("montant_ht", 0) or 0
        lignes.append(
            f"  ⏳ {f['piece']:<16} │ "
            f"{f.get('nom', f.get('code', '')):<25} │ "
            f"{f.get('date', ''):<12} │ {mnt:>8.2f} €"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_fiche_client(data: dict) -> str:
    if data.get("statut") == "NON_TROUVE":
        return f"❌ Client introuvable : {data.get('message', '')}"
    validite = data.get("statut_client", data.get("validite", "VALIDE"))
    icone    = "🔴" if validite in ("BLOQUÉ", "BLOQUE") else "🟡" if validite == "SUSPECT" else "🟢"
    return (
        f"👤 Fiche Client\n{'─' * 45}\n"
        f"  Code          : {data.get('code', '')}\n"
        f"  Raison sociale: {data.get('nom', '')}\n"
        f"  Statut        : {icone} {validite}\n"
        f"  Encours       : {data.get('encours', 0):,.2f} € / max {data.get('encours_max', 0):,.2f} €\n"
        f"{'─' * 45}\n"
        f"  CA Total      : {data.get('ca_total', 0):,.2f} €\n"
        f"  Nb Factures   : {data.get('nb_factures', 0)}\n"
        f"  Encours Fct   : {data.get('encours_factures', 0):,.2f} €"
    )


def _formater_palmares(data: dict) -> str:
    palmares = data.get("palmares", [])
    top_n    = data.get("top_n", len(palmares))
    if not palmares:
        return "📊 Aucune donnée de ventes."
    lignes = [f"🏆 Top {top_n} articles par CA :\n", "─" * 55]
    for a in palmares:
        lignes.append(
            f"  #{a.get('rang', '?'):<3} {a['ref']:<14} │ "
            f"{a.get('designation', ''):<28} │ "
            f"CA: {a.get('ca_article', 0):>8.2f} € │ Qté: {a.get('qte_vendue', 0):.0f}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_ca_global(data: dict) -> str:
    return (
        f"💰 Chiffre d'Affaires Global\n{'─' * 40}\n"
        f"  CA HT         : {data.get('ca_ht', 0):>12,.2f} €\n"
        f"  TVA (19%)     : {data.get('tva_19', 0):>12,.2f} €\n"
        f"  CA TTC        : {data.get('ca_ttc', 0):>12,.2f} €\n"
        f"{'─' * 40}\n"
        f"  Nb factures   : {data.get('nb_factures', 0)}\n"
        f"  Nb clients    : {data.get('nb_clients', 0)}\n"
        f"  Période       : {data.get('date_debut', '?')} → {data.get('date_fin', '?')}"
    )


def _formater_kpi(data: dict) -> str:
    return (
        f"📊 Dashboard KPI\n{'─' * 40}\n"
        f"  CA Total      : {data.get('ca_total', 0):>12,.2f} €\n"
        f"  Marge (22%)   : {data.get('marge_22', 0):>12,.2f} €\n"
        f"  Panier moyen  : {data.get('panier_moy', 0):>12,.2f} €\n"
        f"{'─' * 40}\n"
        f"  Clients       : {data.get('nb_clients', 0)}\n"
        f"  Factures      : {data.get('nb_factures', 0)}\n"
        f"  Documents     : {data.get('nb_docs', 0)}"
    )


def _formater_rentabilite(data: dict) -> str:
    articles = data.get("articles", [])
    if not articles:
        return "📊 Aucune donnée de rentabilité."
    lignes = [f"📊 Rentabilité par article — {len(articles)} ligne(s) :\n", "─" * 65]
    for a in articles:
        taux  = a.get("taux_marge", 0)
        icone = "🟢" if taux >= 30 else "🟡" if taux >= 15 else "🔴"
        lignes.append(
            f"  {icone} {a['ref']:<14} │ {a.get('designation', ''):<25} │ "
            f"CA: {a.get('ca_vente', 0):>8.2f} € │ "
            f"Marge: {a.get('marge_brute', 0):>8.2f} € │ Taux: {taux:>5.1f}%"
        )
    lignes.append("─" * 65)
    return "\n".join(lignes)


def _formater_saisonnalite(data: dict) -> str:
    mois_list = data.get("mois", [])
    if not mois_list:
        return "📅 Aucune donnée mensuelle."
    lignes = [f"📅 CA Mensuel — {len(mois_list)} mois :\n", "─" * 50]
    max_ca = max((x.get("ca_mensuel", 0) for x in mois_list), default=1) or 1
    for m in mois_list:
        ca    = m.get("ca_mensuel", 0)
        barre = "█" * int(ca / max_ca * 15)
        lignes.append(
            f"  {m.get('mois', ''):<8} │ {barre:<15} │ "
            f"{ca:>10,.2f} € │ {m.get('nb_factures', 0)} fct"
        )
    lignes.append("─" * 50)
    return "\n".join(lignes)


def _formater_dso(data: dict) -> str:
    clients  = data.get("clients", [])
    dso_glob = data.get("dso_global", 0)
    lignes   = [f"⏱️  DSO Global : {dso_glob:.1f} jours\n", "─" * 50]
    for c in clients:
        dso_c = c.get("dso_jours", 0)
        icone = "🔴" if dso_c > 60 else "🟡" if dso_c > 30 else "🟢"
        lignes.append(
            f"  {icone} {c.get('code', ''):<10} │ "
            f"{c.get('nom', ''):<28} │ "
            f"DSO: {dso_c:>5.1f} j │ Fct: {c.get('nb_factures', 0)}"
        )
    lignes.append("─" * 50)
    return "\n".join(lignes)


def _formater_rfm(data: dict) -> str:
    clients = data.get("clients", [])
    nb      = data.get("nb_clients", len(clients))
    lignes  = [f"🎯 Analyse RFM — {nb} client(s) :\n", "─" * 65]
    for c in clients:
        statut  = (c.get("statut") or "VALIDE").upper()
        icone   = "🔴" if statut in ("BLOQUÉ", "BLOQUE") else "🟡" if statut == "SUSPECT" else "🟢"
        dernier = c.get("derniere_commande", "Jamais")
        lignes.append(
            f"  {icone} {c['code']:<10} │ {c.get('nom', ''):<28} │ "
            f"CA: {c.get('ca_total', 0):>8.2f} € │ Dernier: {dernier}"
        )
    lignes.append("─" * 65)
    return "\n".join(lignes)


def _formater_clients_baisse(data: dict) -> str:
    clients = data.get("clients", [])
    nb      = data.get("nb", len(clients))
    if not clients:
        return "✅ Aucun client en baisse de CA détecté."
    lignes = [f"📉 Clients en baisse CA — {nb} client(s) :\n", "─" * 60]
    for c in clients:
        var = c.get("variation_pct", 0)
        lignes.append(
            f"  📉 {c['code']:<10} │ {c.get('nom', ''):<28} │ "
            f"Récent: {c.get('ca_recent', 0):>8.2f} € │ "
            f"Ancien: {c.get('ca_ancien', 0):>8.2f} € │ Var: {var:>+.1f}%"
        )
    lignes.append("─" * 60)
    return "\n".join(lignes)


def _formater_declaration(data: dict) -> str:
    import os
    a = data.get("achat", {})
    v = data.get("vente", {})
    fichier = data.get("fichier", "")
    nom_f = os.path.basename(fichier) if fichier else ""
    lien_md = f"[{nom_f}](/static/pdf/{nom_f})" if nom_f else "Non généré"
    return (
        f"📄 Déclaration {data.get('mois','')} {data.get('annee','')}\n"
        f"{'─'*45}\n"
        f"  🛒 Achats  : {a.get('nb',0)} doc(s) │ "
        f"HT: {a.get('total_ht',0):,.2f} € │ TVA: {a.get('total_tva',0):,.2f} € │ TTC: {a.get('total_ttc',0):,.2f} €\n"
        f"  💰 Ventes  : {v.get('nb',0)} doc(s) │ "
        f"HT: {v.get('total_ht',0):,.2f} € │ TVA: {v.get('total_tva',0):,.2f} € │ TTC: {v.get('total_ttc',0):,.2f} €\n"
        f"{'─'*45}\n"
        f"📎 Fichier Excel : {lien_md}"
    )


def _formater_liste_fournisseurs(data: dict) -> str:
    fournisseurs = data.get("fournisseurs", [])
    nb           = data.get("nb_fournisseurs", len(fournisseurs))
    if not fournisseurs:
        return "🏭 Aucun fournisseur enregistré."
    lignes = [f"🏭 Fournisseurs — {nb} fournisseur(s) :\n", "─" * 55]
    for f in fournisseurs:
        statut = (f.get("validite") or "VALIDE").upper()
        icone  = "🔴" if statut in ("BLOQUÉ", "BLOQUE") else "🟢"
        lignes.append(
            f"  {icone} {f.get('code', ''):<10} │ {f.get('nom', ''):<30} │ {statut}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_top_fournisseurs(data: dict) -> str:
    fournisseurs = data.get("fournisseurs", [])
    top_n        = data.get("top_n", len(fournisseurs))
    if not fournisseurs:
        return "📊 Aucune donnée fournisseurs."
    lignes = [f"🏆 Top {top_n} fournisseurs par volume d'achat :\n", "─" * 55]
    for f in fournisseurs:
        lignes.append(
            f"  #{f.get('rang', '?'):<3} {f.get('code', '?'):<10} │ "
            f"{f.get('nom', ''):<28} │ "
            f"Volume: {f.get('volume_achat', 0):>10.2f} € │ Cmd: {f.get('nb_commandes', 0)}"
        )
    lignes.append("─" * 55)
    return "\n".join(lignes)


def _formater_fiche_fournisseur(data: dict) -> str:
    if data.get("statut") == "NON_TROUVE":
        return f"❌ Fournisseur introuvable : {data.get('message', '')}"
    validite = (data.get("validite") or "VALIDE").upper()
    icone    = "🔴" if validite == "BLOQUE" else "🟢"
    return (
        f"🏭 Fiche Fournisseur\n{'─' * 45}\n"
        f"  Code          : {data.get('code', '')}\n"
        f"  Raison sociale: {data.get('nom', '')}\n"
        f"  Statut        : {icone} {validite}\n"
        f"  Encours       : {data.get('encours', 0):,.2f} € / max {data.get('encours_max', 0):,.2f} €\n"
        f"{'─' * 45}\n"
        f"  Nb commandes  : {data.get('nb_commandes', 0)}\n"
        f"  Volume total  : {data.get('volume_total', 0):,.2f} €"
    )


# ──────────────────────────────────────────────────────────────
# Table de dispatch — construite localement (source de vérité unique)
# ──────────────────────────────────────────────────────────────
_FORMATEURS_JSON: dict[str, callable] = {
    "LISTE_ARTICLES":             _formater_liste_articles,
    "LISTE_CLIENTS":              _formater_liste_clients,
    "TOP_CLIENTS":                _formater_top_clients,
    "PALMARES_ARTICLES":          _formater_palmares,
    "CA_GLOBAL":                  _formater_ca_global,
    "CLIENTS_BAISSE":             _formater_clients_baisse,
    "FACTURES_NON_REGLEES":       _formater_factures_impayees,
    "FACTURES_NON_REGLEES_FOURN": _formater_factures_fourn_impayees,
    "TOUTES_FACTURES_CLIENT":     _formater_factures,
    "FICHE_CLIENT":               _formater_fiche_client,
    "RENTABILITE":                _formater_rentabilite,
    "SAISONNALITE":               _formater_saisonnalite,
    "DSO":                        _formater_dso,
    "RFM":                        _formater_rfm,
    "DASHBOARD_EXCEL":            _formater_kpi,
    "DECLARATION_EXCEL":          _formater_declaration,
    "LISTE_FOURNISSEURS":         _formater_liste_fournisseurs,
    "TOP_FOURNISSEURS":           _formater_top_fournisseurs,
    "FICHE_FOURNISSEUR":          _formater_fiche_fournisseur,
}


def _formater_reponse_directe(action: str, reponse_brute: str) -> str | None:
    if not reponse_brute or reponse_brute.startswith("__"):
        return None
    if action in _ACTIONS_DEJA_TEXTE:
        return reponse_brute
    try:
        data = json.loads(reponse_brute)
    except (json.JSONDecodeError, ValueError):
        if reponse_brute.startswith(("📊", "Question :", "─", "👥", "📦", "🏆")):
            return reponse_brute
        return None
    if not isinstance(data, dict):
        return None
    statut = data.get("statut", "")
    if statut in _STATUTS_ERREUR_MCP:
        return data.get("message", f"❌ Erreur : {statut}")
    if statut == "OK" and action in _FORMATEURS_JSON:
        try:
            return _FORMATEURS_JSON[action](data)
        except Exception as e:
            print(f"   ⚠️  [Formateur] {action} : {_safe_str(e)}")
            return None
    if statut in _STATUTS_ACTIONS_V3_OK:
        msg = data.get("message", "")
        if msg:
            alertes = data.get("alertes", [])
            if alertes:
                msg += "\n\n⚠️ Alertes :\n" + "\n".join(f"   {a}" for a in alertes)
            return msg
    return None


def _formater_nl2sql_brut(rb: str, question: str) -> str:
    if not rb:
        return "⚠️  Aucun résultat trouvé."
    if rb.startswith(("📊", "✅", "❌", "⚠️", "─", "👥", "📦", "🏆", "⏳", "Question :")):
        return rb
    try:
        data = json.loads(rb)
        if isinstance(data, dict):
            if "erreur" in data:
                return f"❌ Erreur SQL : {data['erreur']}"
            statut = data.get("statut", "")
            if statut == "OK":
                for _act, _fmt in _FORMATEURS_JSON.items():
                    try:
                        r = _fmt(data)
                        if r:
                            return r
                    except Exception:
                        continue
                for key in ("clients", "factures", "articles", "resultats", "rows", "data", "lignes"):
                    items = data.get(key)
                    if items and isinstance(items, list) and items:
                        lignes = [f"📊 {question} — {len(items)} résultat(s) :", "─" * 60]
                        for i, row in enumerate(items[:30], 1):
                            parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                            lignes.append(f"  {i:>3}. " + " │ ".join(parts))
                        if len(items) > 30:
                            lignes.append(f"  ... et {len(items) - 30} ligne(s) supplémentaire(s)")
                        lignes.append("─" * 60)
                        return "\n".join(lignes)
                parts = [f"{k}: {v}" for k, v in data.items()
                         if v is not None and not isinstance(v, (dict, list))]
                if parts:
                    return f"📊 {question} :\n  " + "\n  ".join(parts)
            if data.get("message"):
                return data["message"]
        elif isinstance(data, list):
            if not data:
                return f"📊 Résultat de « {question} » : Aucun résultat."
            cols = list(data[0].keys()) if isinstance(data[0], dict) else []
            if not cols:
                return str(data)
            lignes = [f"📊 Résultat de « {question} » — {len(data)} ligne(s) :", "─" * 60]
            for i, row in enumerate(data[:30], 1):
                parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                lignes.append(f"  {i:>3}. " + " │ ".join(parts))
            if len(data) > 30:
                lignes.append(f"  ... et {len(data) - 30} ligne(s) supplémentaire(s)")
            lignes.append("─" * 60)
            return "\n".join(lignes)
    except (json.JSONDecodeError, ValueError):
        pass
    if "│" in rb or "─" in rb or ":" in rb:
        return rb
    return f"📊 Résultat de « {question} » :\n{rb}"