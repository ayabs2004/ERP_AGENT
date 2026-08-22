"""
formatters.py — Formatteurs de réponses ERP
============================================
Rendu Markdown harmonisé pour affichage dans le chat (tableaux natifs,
badges de statut cohérents, montants formatés uniformément).

API PUBLIQUE INCHANGÉE — aucune modification côté orchestrateur nécessaire :
  - Mêmes noms de fonctions (_formater_xxx)
  - Même dict de dispatch _FORMATEURS_JSON (mêmes clés d'action)
  - Mêmes signatures _formater_reponse_directe(action, reponse_brute)
    et _formater_nl2sql_brut(rb, question)
  - Mêmes clés lues dans les dict `data` (aucun champ renommé côté backend)
"""
import json

# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION D'AFFICHAGE
# ─────────────────────────────────────────────────────────────────────
CURRENCY = "DT"          # ← changez ici si besoin (€, TND, MAD...)
MAX_LIGNES_TABLE = 80     # nb de lignes affichées avant troncature
                           # (mis à 60 en attendant un vrai "voir plus"
                           # côté frontend — voir _note_troncature ci-dessous)

# Le frontend actuel n'interprète pas le Markdown (les tableaux/`###`/`**`
# s'affichent en texte brut) et n'a pas de white-space:pre-wrap (les \n sont
# écrasés). Tant que ce n'est pas corrigé côté UI, passez RENDU="texte" :
# le rendu dégrade proprement (auto-descriptif, lisible même sur une seule
# ligne aplatie) au lieu de produire du Markdown cassé. Dès que le frontend
# rend le Markdown correctement (react-markdown + remark-gfm par ex.),
# repassez à "markdown" pour retrouver les vrais tableaux/titres/gras.
RENDU = "markdown"        # "markdown" | "texte"


def _safe_str(obj) -> str:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj).encode("utf-8", errors="replace").decode("utf-8")


def _parse_validite(raw_statut) -> str:
    if isinstance(raw_statut, (int, float)):
        return "BLOQUE" if int(raw_statut) == 1 else "VALIDE"
    v = str(raw_statut or "VALIDE").upper()
    return "BLOQUE" if v in ("BLOQUÉ", "BLOQUE") else v


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

_BADGES_STATUT = {
    "VALIDE":  "🟢 Valide",
    "ACTIF":   "🟢 Actif",
    "SUSPECT": "🟡 Suspect",
    "BLOQUE":  "🔴 Bloqué",
}


# ─────────────────────────────────────────────────────────────────────
# HELPERS DE MISE EN FORME — utilisés par tous les formatteurs ci-dessous
# ─────────────────────────────────────────────────────────────────────
def _montant(valeur, devise: str = CURRENCY) -> str:
    """Formate un montant : séparateur de milliers ' ', virgule décimale, devise."""
    try:
        v = float(valeur or 0)
    except (TypeError, ValueError):
        v = 0.0
    entier, frac = f"{v:,.2f}".split(".")
    entier = entier.replace(",", " ")
    signe = "-" if v < 0 and not entier.startswith("-") else ""
    return f"{signe}{entier},{frac} {devise}"


def _qte(valeur) -> str:
    try:
        v = float(valeur or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{v:g}"


def _pct(valeur, decimales: int = 1) -> str:
    try:
        v = float(valeur or 0)
    except (TypeError, ValueError):
        v = 0.0
    signe = "+" if v > 0 else ""
    return f"{signe}{v:.{decimales}f}%"


def _badge_statut(valeur) -> str:
    v = _parse_validite(valeur)
    return _BADGES_STATUT.get(v, f"⚪ {v or 'Inconnu'}")


def _medaille(rang) -> str:
    try:
        r = int(rang)
    except (TypeError, ValueError):
        return "•"
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(r, f"#{r}")


def _sans_gras(texte: str) -> str:
    """Retire la syntaxe Markdown ** ** (inutile/visible en mode texte brut)."""
    return texte.replace("**", "")


def _entete(icone: str, titre: str, sous_titre: str = "") -> str:
    if RENDU == "texte":
        ligne = f"{icone} {titre}"
        if sous_titre:
            ligne += f" ({sous_titre})"
        return ligne
    ligne = f"### {icone} {titre}"
    if sous_titre:
        ligne += f"\n*{sous_titre}*"
    return ligne


def _kv_block(paires: list[tuple[str, object]]) -> str:
    """Bloc « Libellé : valeur », pour les fiches (client, fournisseur, KPI...)."""
    if RENDU == "texte":
        # Séparateur " • " entre champs : reste lisible même si les \n
        # sont écrasés par le frontend (une seule ligne continue).
        parts = [f"{label} : {valeur if valeur not in (None, '') else '—'}" for label, valeur in paires]
        return " • ".join(parts)
    lignes = []
    for label, valeur in paires:
        v = valeur if valeur not in (None, "") else "—"
        lignes.append(f"**{label}** : {v}")
    return "\n".join(lignes)


def _table_md(colonnes: list[str], lignes: list[list], alignement: list[str] | None = None) -> str:
    """Tableau Markdown (ou liste texte auto-descriptive si RENDU='texte')."""
    if not lignes:
        return ""
    if RENDU == "texte":
        # Chaque enregistrement s'auto-décrit ("Colonne: valeur") et les
        # enregistrements sont séparés par " │ " — reste compréhensible
        # même aplati sur une seule ligne par le frontend.
        blocs = []
        for ligne in lignes:
            champs = [f"{col}: {_sans_gras(str(val))}" for col, val in zip(colonnes, ligne)
                      if val not in (None, "")]
            blocs.append(", ".join(champs))
        return "\n".join(f"• {b}" for b in blocs)
    align = alignement or ["g"] * len(colonnes)
    sep_map = {"g": "---", "d": "---:", "c": ":---:"}
    entete = "| " + " | ".join(colonnes) + " |"
    separateur = "|" + "|".join(sep_map.get(a, "---") for a in align) + "|"
    corps = "\n".join(
        "| " + " | ".join(str(c) if c not in (None, "") else "—" for c in ligne) + " |"
        for ligne in lignes
    )
    return f"{entete}\n{separateur}\n{corps}"


def _tronquer(lignes: list, maximum: int = MAX_LIGNES_TABLE) -> tuple[list, int]:
    if len(lignes) <= maximum:
        return lignes, 0
    return lignes[:maximum], len(lignes) - maximum


def _note_troncature(reste: int, unite: str) -> str:
    # NB : ce message ne peut pas être "cliquable" depuis le backend seul.
    # Pour un vrai bouton "voir plus" : le frontend doit renvoyer un message
    # de suivi (ex. "affiche les résultats suivants") que l'orchestrateur
    # interprète comme une pagination (offset/limit) sur la même requête —
    # c'est une fonctionnalité à construire des deux côtés, pas un simple
    # changement de formatage.
    return f"\n\n… et {reste} {unite} supplémentaire(s) — affinez votre recherche (ex. par nom, statut) pour réduire la liste."


# ─────────────────────────────────────────────────────────────────────
# ARTICLES
# ─────────────────────────────────────────────────────────────────────
def _formater_liste_articles(data: dict) -> str:
    articles = data.get("articles", [])
    nb = data.get("nb_articles", len(articles))
    if not articles:
        return "ℹ️ Aucun article dans le catalogue."

    affiches, reste = _tronquer(articles)
    lignes = []
    for a in affiches:
        stock = a.get("stock", 0) or 0
        if stock <= 0:
            etat = "🔴 Rupture"
        elif stock < 5:
            etat = "🟠 Faible"
        else:
            etat = "🟢 Disponible"
        lignes.append([
            f"**{a.get('ref', '')}**",
            a.get("designation", "") or "—",
            _montant(a.get("prix_vente", 0)),
            _qte(stock),
            etat,
        ])
    table = _table_md(
        ["Référence", "Désignation", "Prix vente", "Stock", "État"],
        lignes, ["g", "g", "d", "d", "g"],
    )
    out = _entete("📦", "Catalogue articles", f"{nb} référence(s)") + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "article(s)")
    return out


def _formater_nomenclature(data: dict) -> str:
    if data.get("statut") == "NON_TROUVE":
        return f"❌ {data.get('message', 'Article introuvable.')}"
    composants = data.get("composants", [])
    entete = _entete("🧩", f"Nomenclature — {data.get('ref_parent', '')}", data.get("design_parent", ""))
    if not composants:
        return entete + "\n\nAucun composant défini dans la nomenclature de cet article."
    lignes = [
        [f"**{c.get('ref', '')}**", c.get("designation", "") or "—", _qte(c.get("qte", 0))]
        for c in composants
    ]
    table = _table_md(["Référence", "Désignation", "Quantité"], lignes, ["g", "g", "d"])
    return entete + "\n\n" + table


def _formater_palmares(data: dict) -> str:
    palmares = data.get("palmares", [])
    top_n = data.get("top_n", len(palmares))
    if not palmares:
        return "ℹ️ Aucune donnée de ventes disponible."
    lignes = [
        [_medaille(a.get("rang")), f"**{a.get('ref', '')}**", a.get("designation", "") or "—",
         _montant(a.get("ca_article", 0)), _qte(a.get("qte_vendue", 0))]
        for a in palmares
    ]
    table = _table_md(["Rang", "Référence", "Désignation", "CA", "Qté vendue"],
                       lignes, ["c", "g", "g", "d", "d"])
    return _entete("🏆", f"Top {top_n} articles par chiffre d'affaires") + "\n\n" + table


def _formater_rentabilite(data: dict) -> str:
    articles = data.get("articles", [])
    if not articles:
        return "ℹ️ Aucune donnée de rentabilité disponible."
    lignes = []
    for a in articles:
        taux = a.get("taux_marge", 0) or 0
        icone = "🟢" if taux >= 30 else "🟡" if taux >= 15 else "🔴"
        lignes.append([
            f"**{a.get('ref', '')}**", a.get("designation", "") or "—",
            _montant(a.get("ca_vente", 0)), _montant(a.get("marge_brute", 0)),
            f"{icone} {taux:.1f}%",
        ])
    table = _table_md(["Référence", "Désignation", "CA vente", "Marge brute", "Taux"],
                       lignes, ["g", "g", "d", "d", "d"])
    return _entete("📊", "Rentabilité par article", f"{len(articles)} ligne(s)") + "\n\n" + table


# ─────────────────────────────────────────────────────────────────────
# CLIENTS
# ─────────────────────────────────────────────────────────────────────
def _formater_liste_clients(data: dict) -> str:
    clients = data.get("clients", [])
    nb = data.get("nb_clients", len(clients))
    if not clients:
        return "ℹ️ Aucun client actif."

    affiches, reste = _tronquer(clients)
    lignes = []
    for c in affiches:
        code = c.get("code", c.get("CT_Num", "?"))
        nom = c.get("nom", c.get("CT_Intitule", "?"))
        statut = c.get("validite") or c.get("CT_Validite") or c.get("statut")
        lignes.append([
            f"**{code}**", nom or "—", _badge_statut(statut),
            _montant(c.get("ca_total", 0)), str(c.get("nb_factures", 0)),
        ])
    table = _table_md(["Code", "Client", "Statut", "CA total", "Factures"],
                       lignes, ["g", "g", "g", "d", "d"])
    out = _entete("👥", "Clients actifs", f"{nb} client(s)") + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "client(s)")
    return out


def _formater_clients_inactifs(data: dict) -> str:
    clients = data.get("clients", [])
    nb = data.get("nb_clients", len(clients))
    if not clients:
        return "✅ Aucun client inactif détecté sur la période spécifiée."

    affiches, reste = _tronquer(clients)
    lignes = []
    for c in affiches:
        lignes.append([
            f"**{c.get('code', '')}**", c.get("nom", "") or "—",
            _badge_statut(c.get("validite")),
            c.get("derniere_commande", "Jamais"),
            _montant(c.get("ca_total", 0)),
        ])
    table = _table_md(["Code", "Client", "Statut", "Dernière commande", "CA total"],
                       lignes, ["g", "g", "g", "g", "d"])
    out = _entete("👥", "Clients inactifs", f"{nb} client(s)") + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "client(s)")
    return out


def _formater_top_clients(data: dict) -> str:
    clients = data.get("clients", [])
    top_n = data.get("top_n", len(clients))
    if not clients:
        return "ℹ️ Aucune donnée client disponible."
    lignes = [
        [_medaille(c.get("rang")), f"**{c.get('code_client', '')}**", c.get("nom_client", "") or "—",
         _montant(c.get("ca_total", 0)), str(c.get("nb_factures", 0))]
        for c in clients
    ]
    table = _table_md(["Rang", "Code", "Client", "CA total", "Factures"],
                       lignes, ["c", "g", "g", "d", "d"])
    return _entete("🏆", f"Top {top_n} clients par chiffre d'affaires") + "\n\n" + table


def _formater_fiche_client(data: dict) -> str:
    if data.get("statut") == "NON_TROUVE":
        return f"❌ Client introuvable — {data.get('message', '')}"
    validite = data.get("statut_client", data.get("validite", "VALIDE"))
    entete = _entete("👤", "Fiche client", data.get("nom", ""))
    corps = _kv_block([
        ("Code", data.get("code", "")),
        ("Statut", _badge_statut(validite)),
        ("Encours", f"{_montant(data.get('encours', 0))} / max {_montant(data.get('encours_max', 0))}"),
        ("Chiffre d'affaires", _montant(data.get("ca_total", 0))),
        ("Nombre de factures", data.get("nb_factures", 0)),
        ("Encours factures", _montant(data.get("encours_factures", 0))),
    ])
    return f"{entete}\n\n{corps}"


def _formater_clients_baisse(data: dict) -> str:
    clients = data.get("clients", [])
    nb = data.get("nb", len(clients))
    if not clients:
        return "✅ Aucun client en baisse de chiffre d'affaires détecté."
    lignes = [
        [f"**{c.get('code', '')}**", c.get("nom", "") or "—",
         _montant(c.get("ca_recent", 0)), _montant(c.get("ca_ancien", 0)),
         f"📉 {_pct(c.get('variation_pct', 0))}"]
        for c in clients
    ]
    table = _table_md(["Code", "Client", "CA récent", "CA ancien", "Variation"],
                       lignes, ["g", "g", "d", "d", "d"])
    return _entete("📉", "Clients en baisse de CA", f"{nb} client(s)") + "\n\n" + table


def _formater_rfm(data: dict) -> str:
    clients = data.get("clients", [])
    nb = data.get("nb_clients", len(clients))
    if not clients:
        return "ℹ️ Aucune donnée RFM disponible."
    lignes = [
        [f"**{c.get('code', '')}**", c.get("nom", "") or "—", _badge_statut(c.get("statut")),
         _montant(c.get("ca_total", 0)), c.get("derniere_commande", "Jamais")]
        for c in clients
    ]
    table = _table_md(["Code", "Client", "Statut", "CA total", "Dernière commande"],
                       lignes, ["g", "g", "g", "d", "g"])
    return _entete("🎯", "Analyse RFM", f"{nb} client(s)") + "\n\n" + table


def _formater_dso(data: dict) -> str:
    clients = data.get("clients", [])
    dso_glob = data.get("dso_global", 0)
    entete = _entete("⏱️", "Délai moyen de paiement (DSO)", f"Global : {dso_glob:.1f} jours")
    if not clients:
        return entete + "\n\nAucune donnée client disponible."
    lignes = []
    for c in clients:
        dso_c = c.get("dso_jours", 0) or 0
        icone = "🔴" if dso_c > 60 else "🟡" if dso_c > 30 else "🟢"
        lignes.append([
            f"**{c.get('code', '')}**", c.get("nom", "") or "—",
            f"{icone} {dso_c:.1f} j", str(c.get("nb_factures", 0)),
        ])
    table = _table_md(["Code", "Client", "DSO", "Factures"], lignes, ["g", "g", "d", "d"])
    return f"{entete}\n\n{table}"


# ─────────────────────────────────────────────────────────────────────
# FACTURES
# ─────────────────────────────────────────────────────────────────────
def _formater_factures(data: dict) -> str:
    factures = data.get("factures", [])
    nb = data.get("nb_factures", len(factures))
    intitule = data.get("nom", data.get("code", ""))
    if not factures:
        return f"ℹ️ Aucune facture pour **{intitule}**."

    resume = _kv_block([
        ("Total HT", _montant(data.get("total_ht", 0))),
        ("Total réglé", _montant(data.get("total_regle", 0))),
        ("Total en attente", _montant(data.get("total_en_attente", 0))),
    ])
    affiches, reste = _tronquer(factures)
    lignes = []
    for f in affiches:
        regle = f.get("regle", False) or f.get("statut", "") == "RÉGLÉE"
        badge = "✅ Réglée" if regle else "⏳ En attente"
        lignes.append([f"**{f.get('piece', '')}**", f.get("date", "") or "—",
                        _montant(f.get("montant_ht", 0)), badge])
    table = _table_md(["Pièce", "Date", "Montant HT", "Statut"], lignes, ["g", "g", "d", "g"])
    out = _entete("🧾", f"Factures — {intitule}", f"{nb} facture(s)") + "\n\n" + resume + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "facture(s)")
    return out


def _formater_factures_impayees(data: dict) -> str:
    factures = data.get("factures", [])
    nb = data.get("nb_factures", len(factures))
    if not factures:
        return "✅ Aucune facture impayée."
    affiches, reste = _tronquer(factures)
    lignes = [
        [f"**{f.get('piece', '')}**", f.get("nom", f.get("code", "")) or "—",
         f.get("date", "") or "—", _montant(f.get("montant_ht", 0))]
        for f in affiches
    ]
    table = _table_md(["Pièce", "Client", "Date", "Montant HT"], lignes, ["g", "g", "g", "d"])
    out = (_entete("⚠️", "Factures impayées", f"{nb} facture(s) · Total dû : {_montant(data.get('total_du', 0))}")
           + "\n\n" + table)
    if reste:
        out += _note_troncature(reste, "facture(s)")
    return out


def _formater_factures_fourn_impayees(data: dict) -> str:
    factures = data.get("factures", [])
    nb = data.get("nb_factures", len(factures))
    ct_num = data.get("code", "")
    titre = f"Factures fournisseur impayées — {ct_num}" if ct_num else "Factures fournisseurs impayées"
    if not factures:
        return f"✅ Aucune facture fournisseur impayée{' pour ' + ct_num if ct_num else ''}."
    affiches, reste = _tronquer(factures)
    lignes = [
        [f"**{f.get('piece', '')}**", f.get("nom", f.get("code", "")) or "—",
         f.get("date", "") or "—", _montant(f.get("montant_ht", 0))]
        for f in affiches
    ]
    table = _table_md(["Pièce", "Fournisseur", "Date", "Montant HT"], lignes, ["g", "g", "g", "d"])
    out = _entete("🧾", titre, f"{nb} facture(s) · Total dû : {_montant(data.get('total_du', 0))}") + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "facture(s)")
    return out


# ─────────────────────────────────────────────────────────────────────
# FOURNISSEURS
# ─────────────────────────────────────────────────────────────────────
def _formater_liste_fournisseurs(data: dict) -> str:
    fournisseurs = data.get("fournisseurs", [])
    nb = data.get("nb_fournisseurs", len(fournisseurs))
    if not fournisseurs:
        return "ℹ️ Aucun fournisseur enregistré."
    affiches, reste = _tronquer(fournisseurs)
    lignes = [
        [f"**{f.get('code', '')}**", f.get("nom", "") or "—", _badge_statut(f.get("validite"))]
        for f in affiches
    ]
    table = _table_md(["Code", "Fournisseur", "Statut"], lignes, ["g", "g", "g"])
    out = _entete("🏭", "Fournisseurs", f"{nb} fournisseur(s)") + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "fournisseur(s)")
    return out


def _formater_top_fournisseurs(data: dict) -> str:
    fournisseurs = data.get("fournisseurs", [])
    top_n = data.get("top_n", len(fournisseurs))
    if not fournisseurs:
        return "ℹ️ Aucune donnée fournisseur disponible."
    lignes = [
        [_medaille(f.get("rang")), f"**{f.get('code', '?')}**", f.get("nom", "") or "—",
         _montant(f.get("volume_achat", 0)), str(f.get("nb_commandes", 0))]
        for f in fournisseurs
    ]
    table = _table_md(["Rang", "Code", "Fournisseur", "Volume d'achat", "Commandes"],
                       lignes, ["c", "g", "g", "d", "d"])
    return _entete("🏆", f"Top {top_n} fournisseurs par volume d'achat") + "\n\n" + table


def _formater_fiche_fournisseur(data: dict) -> str:
    if data.get("statut") == "NON_TROUVE":
        return f"❌ Fournisseur introuvable — {data.get('message', '')}"
    entete = _entete("🏭", "Fiche fournisseur", data.get("nom", ""))
    corps = _kv_block([
        ("Code", data.get("code", "")),
        ("Statut", _badge_statut(data.get("validite"))),
        ("Encours", f"{_montant(data.get('encours', 0))} / max {_montant(data.get('encours_max', 0))}"),
        ("Nombre de commandes", data.get("nb_commandes", 0)),
        ("Volume total", _montant(data.get("volume_total", 0))),
    ])
    return f"{entete}\n\n{corps}"


# ─────────────────────────────────────────────────────────────────────
# TABLEAUX DE BORD / FINANCE
# ─────────────────────────────────────────────────────────────────────
def _formater_ca_global(data: dict) -> str:
    entete = _entete("💰", "Chiffre d'affaires global",
                      f"{data.get('date_debut', '?')} → {data.get('date_fin', '?')}")
    corps = _kv_block([
        ("CA HT", _montant(data.get("ca_ht", 0))),
        ("TVA (19%)", _montant(data.get("tva_19", 0))),
        ("CA TTC", _montant(data.get("ca_ttc", 0))),
        ("Nombre de factures", data.get("nb_factures", 0)),
        ("Nombre de clients", data.get("nb_clients", 0)),
    ])
    return f"{entete}\n\n{corps}"


def _formater_kpi(data: dict) -> str:
    entete = _entete("📊", "Tableau de bord — KPI")
    corps = _kv_block([
        ("Chiffre d'affaires total", _montant(data.get("ca_total", 0))),
        ("Marge (22%)", _montant(data.get("marge_22", 0))),
        ("Panier moyen", _montant(data.get("panier_moy", 0))),
        ("Clients", data.get("nb_clients", 0)),
        ("Factures", data.get("nb_factures", 0)),
        ("Documents", data.get("nb_docs", 0)),
    ])
    return f"{entete}\n\n{corps}"


def _formater_saisonnalite(data: dict) -> str:
    mois_list = data.get("mois", [])
    if not mois_list:
        return "ℹ️ Aucune donnée mensuelle disponible."
    # NB : pas de mini-graphique en caractères (█, ▮...) ici — sans police
    # monospace ni alignement de tableau côté frontend, ça produit des blocs
    # noirs illisibles hors contexte. On préfère un pourcentage explicite.
    max_ca = max((m.get("ca_mensuel", 0) for m in mois_list), default=1) or 1
    lignes = []
    for m in mois_list:
        ca = m.get("ca_mensuel", 0) or 0
        part = f"{(ca / max_ca * 100):.0f}% du meilleur mois" if ca else "—"
        lignes.append([f"**{m.get('mois', '')}**", _montant(ca), str(m.get("nb_factures", 0)), part])
    table = _table_md(["Mois", "CA", "Factures", "Part du meilleur mois"], lignes, ["g", "d", "d", "d"])
    return _entete("📅", "Chiffre d'affaires mensuel", f"{len(mois_list)} mois") + "\n\n" + table


def _formater_declaration(data: dict) -> str:
    import os
    a = data.get("achat", {})
    v = data.get("vente", {})
    fichier = data.get("fichier", "")
    nom_f = os.path.basename(fichier) if fichier else ""
    lien = f"[📎 {nom_f}](/static/pdf/{nom_f})" if nom_f else "*Fichier non généré*"

    entete = _entete("📄", f"Déclaration — {data.get('mois', '')} {data.get('annee', '')}")
    bloc_achats = _kv_block([
        ("Documents", a.get("nb", 0)),
        ("Total HT", _montant(a.get("total_ht", 0))),
        ("Total TVA", _montant(a.get("total_tva", 0))),
        ("Total TTC", _montant(a.get("total_ttc", 0))),
    ])
    bloc_ventes = _kv_block([
        ("Documents", v.get("nb", 0)),
        ("Total HT", _montant(v.get("total_ht", 0))),
        ("Total TVA", _montant(v.get("total_tva", 0))),
        ("Total TTC", _montant(v.get("total_ttc", 0))),
    ])
    return (
        f"{entete}\n\n"
        f"**🛒 Achats**\n{bloc_achats}\n\n"
        f"**💰 Ventes**\n{bloc_ventes}\n\n"
        f"{lien}"
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
    "CLIENTS_INACTIFS":           _formater_clients_inactifs,
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
    "AFFICHER_NOMENCLATURE":      _formater_nomenclature,
}

# Préfixes reconnus comme "déjà formatés" (émis par d'autres modules :
# mcp_knowledge_base.py, graph_nodes/lecture.py, etc.) — on les laisse
# passer tels quels plutôt que de les re-formater/casser.
_PREFIXES_DEJA_FORMATES = (
    "#", "**", "ℹ️", "❌", "✅", "⚠️", "🔍", "📚", "⚙️", "─", "Question :",
    "👥", "📦", "🏆", "🧾", "🏭", "📅", "⏱️", "🎯", "📉", "🧩", "💰", "📊", "📄", "👤",
)


def _formater_reponse_directe(action: str, reponse_brute: str) -> str | None:
    if not reponse_brute or reponse_brute.startswith("__"):
        return None
    if action in _ACTIONS_DEJA_TEXTE:
        return reponse_brute
    try:
        data = json.loads(reponse_brute)
    except (json.JSONDecodeError, ValueError):
        if reponse_brute.startswith(_PREFIXES_DEJA_FORMATES):
            return reponse_brute
        return None
    if not isinstance(data, dict):
        return None
    statut = data.get("statut", "")
    if statut in _STATUTS_ERREUR_MCP:
        return f"❌ {data.get('message', f'Erreur : {statut}')}"
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
                msg += "\n\n**⚠️ Alertes**\n" + "\n".join(f"- {a}" for a in alertes)
            return msg
    return None


def _formater_nl2sql_brut(rb: str, question: str) -> str:
    if not rb:
        return "ℹ️ Aucun résultat trouvé."
    if rb.startswith(_PREFIXES_DEJA_FORMATES):
        return rb
    try:
        data = json.loads(rb)
        if isinstance(data, dict):
            if "erreur" in data:
                return f"❌ Erreur SQL : {data['erreur']}"
            statut = data.get("statut", "")
            if statut == "OK":
                # Les réponses génériques (interpreter_et_analyser_via_sql /
                # executer_sql_vanna) utilisent toujours "resultats" ou
                # "message" — jamais les clés spécifiques ("articles",
                # "clients"...) attendues par les formatteurs dédiés à une
                # action précise. On les traite donc AVANT tout essai de
                # formatteur dédié, pour ne pas se faire piéger par un
                # formatteur qui matche à tort sur une clé absente
                # (ex: LISTE_ARTICLES → "Aucun article dans le catalogue"
                # alors que les données sont dans "resultats").
                if "resultats" in data:
                    items = data["resultats"]
                    desc = data.get("description", question)
                    if items:
                        return _formater_resultats_generiques(items, desc)
                    return f"ℹ️ Aucun résultat pour « {desc} »."
                if data.get("message"):
                    return data["message"]
                for key in ("clients", "factures", "articles", "rows", "data", "lignes"):
                    items = data.get(key)
                    if items and isinstance(items, list) and items:
                        return _formater_resultats_generiques(items, question)
                parts = [(k, v) for k, v in data.items()
                         if v is not None and not isinstance(v, (dict, list))]
                if parts:
                    return _entete("📊", question) + "\n\n" + _kv_block(parts)
            if data.get("message"):
                return data["message"]
        elif isinstance(data, list):
            if not data:
                return f"ℹ️ Aucun résultat pour « {question} »."
            return _formater_resultats_generiques(data, question)
    except (json.JSONDecodeError, ValueError):
        pass
    if any(m in rb for m in ("|", "─", "**", ":")):
        return rb
    return _entete("📊", question) + f"\n\n{rb}"
def _formater_resultats_generiques(items: list, question: str) -> str:
    """Rendu générique en tableau Markdown pour des résultats NL2SQL non
    couverts par un formatteur dédié (colonnes dynamiques et variables)."""
    if not items or not isinstance(items[0], dict):
        return _entete("📊", question) + "\n\n" + "\n".join(f"- {x}" for x in items)
    affiches, reste = _tronquer(items)
    colonnes = list(affiches[0].keys())
    lignes = [[row.get(c) for c in colonnes] for row in affiches]
    table = _table_md(colonnes, lignes)
    out = _entete("📊", question, f"{len(items)} résultat(s)") + "\n\n" + table
    if reste:
        out += _note_troncature(reste, "résultat(s)")
    return out