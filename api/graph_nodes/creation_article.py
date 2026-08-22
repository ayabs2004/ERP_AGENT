"""
Création article — flux conversationnel guidé (machine d'état).

Champs SMALLINT Sage 100 :
  AR_Nature    : 0=Marchandise, 1=Nomenclature (composé), 2=Gamme, 3=Gamme+Nomenclature
  AR_SuiviStock: 0=Aucun, 1=CMUP, 2=Sérialisé, 3=Lot, 4=CMUP+Lot
  AR_UniteVen  : N° de l'unité de vente (1=Unité, 2=Kg, 3=Gramme, 4=Litre, 5=Heure...)
"""

import logging
import json
from api.mcp_pool import pool as mcp_pool
from api.mcp_actions_sage import _get_conn, T_FAMILLE, C_FA_CODE, C_FA_INTITULE, T_ARTICLE, C_AR_REF, C_AR_DESIGN

logger = logging.getLogger(__name__)

# ─── Mappings des SMALLINT Sage 100 ───────────────────────────────────────────

AR_NATURE_OPTIONS = {
    0: "Marchandise (standard)",
    1: "Nomenclature / Article composé",
    2: "Gamme",
    3: "Gamme + Nomenclature",
}

AR_SUIVI_STOCK_OPTIONS = {
    0: "Aucun suivi",
    1: "CMUP (Coût Moyen Unitaire Pondéré)",
    2: "Sérialisé (numéro de série)",
    3: "Lot",
    4: "CMUP + Lot",
}

AR_UNITE_VEN_OPTIONS = {
    1:  "Unité",
    2:  "Kg",
    3:  "Gramme",
    4:  "Litre",
    5:  "Heure",
    6:  "Mètre",
    7:  "m²",
    8:  "m³",
    9:  "Tonne",
    10: "Boîte",
}

def _formater_enum(options: dict) -> str:
    return "\n".join(f"  🔸 `{k}` — {v}" for k, v in options.items())

def _valider_smallint(question: str, options: dict):
    """Retourne (valeur_int, None) si valide, (None, message_erreur) sinon."""
    try:
        val = int(question.strip())
    except ValueError:
        cles = ", ".join(str(k) for k in options)
        return None, f"⚠️ Saisissez un entier parmi : {cles}"
    if val not in options:
        cles = ", ".join(str(k) for k in options)
        return None, f"⚠️ Valeur invalide. Choisissez parmi : {cles}"
    return val, None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _est_non(texte: str) -> bool:
    t = texte.lower().strip()
    return t in ("non", "n", "annuler", "stop", "non merci", "no")


def _est_oui(texte: str) -> bool:
    t = texte.lower().strip()
    return t in ("oui", "o", "ok", "yes", "y", "confirmer", "valider")


def _recuperer_familles() -> list[tuple[str, str]]:
    """Retourne la liste (code, intitule) des familles d'articles disponibles
    (feuilles uniquement — FA_Nature=2 — exclut les racines/totaux non attachables)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            f"SELECT {C_FA_CODE}, {C_FA_INTITULE} FROM {T_FAMILLE} "
            f"WHERE FA_Nature = 2 ORDER BY {C_FA_CODE}"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        logger.warning(f"[creation_article] Impossible de lire les familles : {e}")
        return []




def _article_designation_existe(designation: str) -> bool:
    """Retourne True si un article avec cette désignation (insensible à la casse) existe déjà."""
    try:
        conn = _get_conn()
        row = conn.execute(
            f"SELECT {C_AR_REF} FROM {T_ARTICLE} WHERE UPPER({C_AR_DESIGN}) = UPPER(?)",
            (designation,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _traiter_retry(state: dict, c: dict, etape: str, message: str) -> bool:
    """Retourne True si le flux doit être interrompu après 2 tentatives."""
    tentatives = c.setdefault("tentatives_champ", {})
    nb = int(tentatives.get(etape, 0)) + 1
    tentatives[etape] = nb
    if nb >= 2:
        state["creation_article_en_cours"] = {}
        state["reponse_finale"] = "❌ Création annulée après 2 essais infructueux."
        return True
    state["creation_article_en_cours"] = c
    state["reponse_finale"] = f"{message}\n\nTentative {nb}/2."
    return False


def _formater_recapitulatif(c: dict) -> str:
    """Formate un récapitulatif lisible des champs saisis."""
    nature_label    = AR_NATURE_OPTIONS.get(c.get("nature"),     f"Code {c.get('nature')}")
    unite_label     = AR_UNITE_VEN_OPTIONS.get(c.get("unite_vente"), f"Code {c.get('unite_vente')}")
    suivi_label     = AR_SUIVI_STOCK_OPTIONS.get(c.get("suivi_stock"), f"Code {c.get('suivi_stock')}")

    return (
        "📋 **Récapitulatif de l'article à créer** :\n\n"
        f"🔸 **Référence** : {c.get('ref_article', '—')}\n"
        f"🔸 **Désignation** : {c.get('designation', '—')}\n"
        f"🔸 **Prix d'achat** : {c.get('prix_achat', 0)}\n"
        f"🔸 **Prix de vente** : {c.get('prix_vente', 0)}\n"
        f"🔸 **Nature** : `{c.get('nature', '—')}` — {nature_label}\n"
        f"🔸 **Famille** : {c.get('code_famille', '—')}\n"
        f"🔸 **Unité de vente** : `{c.get('unite_vente', '—')}` — {unite_label}\n"
        f"🔸 **Suivi de stock** : `{c.get('suivi_stock', '—')}` — {suivi_label}\n"
    )


# ─── Nœud principal ───────────────────────────────────────────────────────────

async def noeud_creation_article(state: dict) -> dict:
    c = state.get("creation_article_en_cours", {})
    etape = c.get("etape", "INIT")
    question = state.get("demande_brute", "").strip()

    # ── INIT ──────────────────────────────────────────────────────────────────
    if not c or etape == "INIT":
        c = {
            "etape": "ATTENTE_REF",
            "ref_article": "",
            "designation": "",
            "prix_achat": 0.0,
            "prix_vente": 0.0,
            "nature": None,
            "code_famille": "",
            "unite_vente": None,
            "suivi_stock": None,
        }
        state["creation_article_en_cours"] = c
        state["reponse_finale"] = (
            "🆕 **Création d'un nouvel article**\n\n"
            "Veuillez saisir la **Référence** de l'article (ex: `ECRAN-27`) :"
        )
        return state

    # ── ATTENTE_REF ────────────────────────────────────────────────────────────
    if etape == "ATTENTE_REF":
        if not question:
            state["reponse_finale"] = "⚠️ La référence est obligatoire. Saisissez la référence :"
            return state
        ref = question.strip().upper()
        try:
            raw = await mcp_pool.call("actions", "lire_article", {"ref_article": ref})
            data = json.loads(str(raw[0].text if isinstance(raw, list) else raw)) if raw else {}
        except Exception:
            data = {}
        if data.get("statut") == "SUCCES":
            if _traiter_retry(state, c, "ATTENTE_REF", f"❌ La référence **{ref}** existe déjà dans la base. Utilisez une autre référence."):
                return state
            return state
        c["ref_article"] = ref
        c["etape"] = "ATTENTE_DESIGN"
        state["creation_article_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Référence : `{c['ref_article']}`\n\n"
            "Veuillez saisir la **Désignation** de l'article :"
        )
        return state

    # ── ATTENTE_DESIGN ─────────────────────────────────────────────────────────
    if etape == "ATTENTE_DESIGN":
        if not question:
            if _traiter_retry(state, c, "ATTENTE_DESIGN", "⚠️ La désignation est obligatoire. Saisissez la désignation :"):
                return state
            return state
        # Vérifier si la désignation existe déjà en base (prob4)
        if _article_designation_existe(question):
            if _traiter_retry(state, c, "ATTENTE_DESIGN",
                              f"❌ Un article avec la désignation **'{question}'** existe déjà en base.\n\nVeuillez saisir une autre désignation :"):
                return state
            return state
        c["designation"] = question
        c["etape"] = "ATTENTE_PRIX_ACHAT"
        state["creation_article_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Désignation : `{c['designation']}`\n\n"
            "Veuillez saisir le **Prix d'achat** (ou `0` si aucun) :"
        )
        return state

    # ── ATTENTE_PRIX_ACHAT ─────────────────────────────────────────────────────
    if etape == "ATTENTE_PRIX_ACHAT":
        try:
            val = float(question.replace(",", "."))
        except ValueError:
            if _traiter_retry(state, c, "ATTENTE_PRIX_ACHAT", "⚠️ Montant invalide. Saisissez un nombre (ex: `15.5`) :"):
                return state
            return state
        c["prix_achat"] = val
        c["etape"] = "ATTENTE_PRIX_VENTE"
        state["creation_article_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Prix d'achat : `{val}`\n\n"
            "Veuillez saisir le **Prix de vente** (ou `0` si aucun) :"
        )
        return state

    # ── ATTENTE_PRIX_VENTE ─────────────────────────────────────────────────────
    if etape == "ATTENTE_PRIX_VENTE":
        try:
            val = float(question.replace(",", "."))
        except ValueError:
            if _traiter_retry(state, c, "ATTENTE_PRIX_VENTE", "⚠️ Montant invalide. Saisissez un nombre (ex: `35.0`) :"):
                return state
            return state
        c["prix_vente"] = val
        c["etape"] = "ATTENTE_NATURE"
        state["creation_article_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Prix de vente : `{val}`\n\n"
            "**Nature de l'article** (`AR_Nature`) — Saisissez le numéro correspondant :\n\n"
            + _formater_enum(AR_NATURE_OPTIONS)
        )
        return state

    # ── ATTENTE_NATURE ─────────────────────────────────────────────────────────
    if etape == "ATTENTE_NATURE":
        val, err = _valider_smallint(question, AR_NATURE_OPTIONS)
        if err:
            if _traiter_retry(state, c, "ATTENTE_NATURE", f"{err}\n\n**Nature de l'article** (`AR_Nature`) :\n\n" + _formater_enum(AR_NATURE_OPTIONS)):
                return state
            return state
        c["nature"] = val
        c["etape"] = "ATTENTE_FAMILLE"
        state["creation_article_en_cours"] = c

        familles = _recuperer_familles()
        if familles:
            lignes = ["Voici les **familles disponibles** :\n"]
            for f_code, f_int in familles:
                lignes.append(f"  🔸 `{f_code}` — {f_int}")
            lignes.append("\nVeuillez saisir le **Code de la famille** :")
            msg = "\n".join(lignes)
        else:
            msg = "Aucune famille trouvée en base. Saisissez un code de famille manuellement :"

        state["reponse_finale"] = (
            f"✅ Nature : `{val}` — {AR_NATURE_OPTIONS[val]}\n\n{msg}"
        )
        return state

    # ── ATTENTE_FAMILLE ────────────────────────────────────────────────────────
    if etape == "ATTENTE_FAMILLE":
        code_saisi = question.strip().upper()
        familles_valides = {f[0] for f in _recuperer_familles()}
        if code_saisi not in familles_valides:
            if _traiter_retry(state, c, "ATTENTE_FAMILLE", f"⚠️ '{code_saisi}' n'est pas un code de famille valide.\n\nVeuillez choisir un code parmi la liste proposée :"):
                return state
            return state
        c["code_famille"] = code_saisi
        c["etape"] = "ATTENTE_UNITE"
        state["creation_article_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Famille : `{c['code_famille']}`\n\n"
            "**Unité de vente** (`AR_UniteVen`) — Saisissez le numéro correspondant :\n\n"
            + _formater_enum(AR_UNITE_VEN_OPTIONS)
            + "\n\n_(Si votre unité n'apparaît pas, saisissez son numéro Sage directement)_"
        )
        return state

    # ── ATTENTE_UNITE ──────────────────────────────────────────────────────────
    if etape == "ATTENTE_UNITE":
        try:
            val = int(question.strip())
        except ValueError:
            if _traiter_retry(state, c, "ATTENTE_UNITE", "⚠️ Saisissez un entier (numéro d'unité Sage).\n\n**Unité de vente** (`AR_UniteVen`) :\n\n" + _formater_enum(AR_UNITE_VEN_OPTIONS)):
                return state
            return state
        c["unite_vente"] = val
        c["etape"] = "ATTENTE_SUIVI_STOCK"
        state["creation_article_en_cours"] = c
        unite_label = AR_UNITE_VEN_OPTIONS.get(val, f"Unité n°{val}")
        state["reponse_finale"] = (
            f"✅ Unité de vente : `{val}` — {unite_label}\n\n"
            "**Suivi de stock** (`AR_SuiviStock`) — Saisissez le numéro correspondant :\n\n"
            + _formater_enum(AR_SUIVI_STOCK_OPTIONS)
        )
        return state

    # ── ATTENTE_SUIVI_STOCK ────────────────────────────────────────────────────
    if etape == "ATTENTE_SUIVI_STOCK":
        val, err = _valider_smallint(question, AR_SUIVI_STOCK_OPTIONS)
        if err:
            if _traiter_retry(state, c, "ATTENTE_SUIVI_STOCK", f"{err}\n\n**Suivi de stock** (`AR_SuiviStock`) :\n\n" + _formater_enum(AR_SUIVI_STOCK_OPTIONS)):
                return state
            return state
        c["suivi_stock"] = val
        c["etape"] = "ATTENTE_CONFIRMATION"
        state["creation_article_en_cours"] = c

        state["reponse_finale"] = (
            _formater_recapitulatif(c)
            + "\n✅ Confirmez-vous la création de cet article ?"
        )
        state["action_buttons"] = ["Oui", "Non"]
        return state

    # ── ATTENTE_CONFIRMATION ───────────────────────────────────────────────────
    if etape == "ATTENTE_CONFIRMATION":
        if _est_non(question):
            state["creation_article_en_cours"] = {}
            state["reponse_finale"] = "🛑 Création de l'article annulée."
            return state

        if not _est_oui(question):
            state["reponse_finale"] = "❓ Répondez **oui** pour confirmer ou **non** pour annuler."
            state["action_buttons"] = ["Oui", "Non"]
            state["creation_article_en_cours"] = c
            return state

        # ── Appel de l'outil MCP creer_article ────────────────────────────────
        try:
            raw = await mcp_pool.call("actions", "creer_article", {
                "ref_article":  c["ref_article"],
                "designation":  c["designation"],
                "prix_achat":   c["prix_achat"],
                "prix_vente":   c["prix_vente"],
                "nature":       c["nature"],
                "code_famille": c["code_famille"],
                "unite_vente":  c["unite_vente"],
                "suivi_stock":  c["suivi_stock"],
            })
            
            raw_str = str(raw) if raw else ""
            msg = "✅ Article créé avec succès."
            if raw_str:
                try:
                    data = json.loads(raw_str)
                    if data.get("statut") == "CREE":
                        ref = data.get("AR_Ref", "N/A")
                        msg = (
                            f"✨ **Félicitations !** L'article **{ref}** a été créé avec succès dans Sage 100.\n\n"
                            f"📦 **Référence** : `{ref}`\n"
                            f"📝 **Désignation** : `{c.get('designation', '—')}`\n\n"
                            "Vous pouvez dès à présent l'utiliser dans vos documents (Devis, Commandes, Factures, etc.)."
                        )
                    elif data.get("statut") == "ERREUR":
                        msg = f"❌ **Échec de la création :**\n\n{data.get('message', 'Erreur inconnue.')}"
                    else:
                        msg = data.get("message", raw_str)
                except json.JSONDecodeError:
                    msg = raw_str

        except Exception as e:
            logger.error(f"[creation_article] Erreur MCP : {e}")
            msg = f"❌ Erreur lors de la création : {e}"

        state["creation_article_en_cours"] = {}
        state["reponse_finale"] = msg
        return state

    return state
