"""
llm_anonymizer.py — Anonymisation data-aware avant appel LLM externe
======================================================================
Stratégie hybride en deux passes :

  Passe 1 – Valeurs exactes DB (chargées depuis la base configurée)
    • code tiers (client/fournisseur) → <<CLIENT_1>> / <<FOUR_1>>
    • référence article              → <<ARTICLE_1>>
    • numéro de pièce                → <<PIECE_1>>
    • nom commercial (tiers)         → <<NOM_1>>

  Passe 2 – Regex calibrés sur les formats réels observés
    * Nouveaux codes clients  CLI[0-9]{2,4}    -> <<CLIENT_n>>
    * Nouveaux fournisseurs   FOUR[0-9]{2,4}   -> <<FOUR_n>>
    * Nouvelles pièces        (FA|BL|BC|...)[0-9]{5,14} -> <<PIECE_n>>
    * Montants décimaux       1234,56 / 1234.56 -> <<MONTANT_n>>

La passe DB garantit que des refs comme LAPTOP, IMPRIMANTE, ECRAN4K
sont anonymisées même si elles ne ressemblent pas à un code "générique".

──────────────────────────────────────────────────────────────────
v2 — CORRECTIF DE NEUTRALITÉ DB (règle d'or db_adapter.py)
     Ce module exécutait auparavant trois requêtes SQL avec les noms
     physiques Sage codés en dur (F_COMPTET, CT_Num, CT_Intitule,
     CT_Type, F_ARTICLE, AR_Ref, F_DOCENTETE, DO_Piece) et ouvrait sa
     propre connexion sqlite3 sur un chemin recalculé localement.
     Désormais, toutes les tables/colonnes passent par
     adaptation.db_adapter.table()/col(), et la connexion passe par
     adaptation.db_adapter.get_connection() (sqlite ou mssql selon
     adaptation/db_config.json). Si la base est temporairement
     inaccessible, on retombe silencieusement sur l'anonymisation par
     regex seule (comportement inchangé).
──────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Awaitable, Any

# Permet d'importer adaptation.db_adapter quel que soit le cwd d'exécution
sys.path.insert(0, str(Path(__file__).parent.parent))

from adaptation.db_adapter import table, col, get_connection


# ─────────────────────────────────────────────────────────────────────
# Cache mémoire des valeurs DB (chargé une fois)
# ─────────────────────────────────────────────────────────────────────
_DB_VALUES: dict[str, list[str]] = {}   # "clients", "fournisseurs", "articles", "pieces", "noms"
_DB_LOADED = False


def _load_db_values() -> None:
    """Charge tous les identifiants sensibles depuis la base configurée
    (adaptation/db_config.json), via db_adapter — jamais de nom de
    table/colonne physique en dur ici."""
    global _DB_VALUES, _DB_LOADED
    if _DB_LOADED:
        return
    _DB_LOADED = True

    try:
        clients_table = table("clients_fournisseurs")
        code_col      = col("clients_fournisseurs", "code")
        nom_col       = col("clients_fournisseurs", "nom")
        type_col      = col("clients_fournisseurs", "type_tiers")

        articles_table = table("articles")
        ref_col        = col("articles", "ref")

        doc_entete_table = table("doc_entete")
        piece_col        = col("doc_entete", "piece")

        con = get_connection()
        try:
            cur = con.cursor() if hasattr(con, "cursor") else con

            # Codes tiers (clients type=0, fournisseurs type=1)
            cur.execute(
                f"SELECT {code_col}, {nom_col}, {type_col} FROM {clients_table}"
            )
            raw_rows = cur.fetchall()
            print(f"   [DEBUG] Lignes brutes tiers retournées par SQL : {len(raw_rows)}")
            if raw_rows:
                print(f"   [DEBUG] Exemple première ligne : {raw_rows[0]}")
            
            clients, fournisseurs, noms = [], [], []
            for row in raw_rows:
                # Handle both dict-like and tuple-like rows
                if isinstance(row, dict) or hasattr(row, 'keys'):
                    def _get_val(r, col_name):
                        if col_name in r: return r[col_name]
                        if col_name.lower() in r: return r[col_name.lower()]
                        if col_name.upper() in r: return r[col_name.upper()]
                        return None
                    ct_num = _get_val(row, code_col)
                    ct_intitule = _get_val(row, nom_col)
                    ct_type = _get_val(row, type_col)
                else:
                    ct_num, ct_intitule, ct_type = row[0], row[1], row[2]
                
                if ct_num and str(ct_num).upper() not in ("PROD-INT", ""):
                    # Traiter ct_type en toute sécurité (0 est falsy en Python)
                    if str(ct_type).strip() == "0" or ct_type == 0:
                        clients.append(str(ct_num))
                    elif str(ct_type).strip() == "1" or ct_type == 1:
                        fournisseurs.append(str(ct_num))
                if ct_intitule and len(str(ct_intitule).strip()) >= 3:
                    noms.append(str(ct_intitule).strip())

            # Références articles
            cur.execute(f"SELECT {ref_col} FROM {articles_table}")
            articles = []
            for row in cur.fetchall():
                val = row.get(ref_col) if (isinstance(row, dict) or hasattr(row, 'keys')) else row[0]
                if val: articles.append(str(val))

            # Numéros de pièce
            cur.execute(f"SELECT DISTINCT {piece_col} FROM {doc_entete_table}")
            pieces = []
            for row in cur.fetchall():
                val = row.get(piece_col) if (isinstance(row, dict) or hasattr(row, 'keys')) else row[0]
                if val: pieces.append(str(val))
        finally:
            con.close()

        _DB_VALUES = {
            "clients":      sorted(clients,      key=len, reverse=True),  # plus long d'abord
            "fournisseurs": sorted(fournisseurs, key=len, reverse=True),
            "articles":     sorted(articles,     key=len, reverse=True),
            "pieces":       sorted(pieces,       key=len, reverse=True),
            "noms":         sorted(noms,         key=len, reverse=True),
        }

        total = sum(len(v) for v in _DB_VALUES.values())
        print(f"   [Anonymiseur] {total} entites DB chargees "
              f"({len(clients)} clients, {len(fournisseurs)} fourn., "
              f"{len(articles)} articles, {len(pieces)} pieces, {len(noms)} noms)")


        _rebuild_ac_automaton()

    except Exception as e:
        print(f"   [Anonymiseur] DB inaccessible ({e}) -- fallback regex seul")
        _DB_VALUES = {"clients": [], "fournisseurs": [], "articles": [], "pieces": [], "noms": []}
        _rebuild_ac_automaton()


# Cache de l'automate Aho-Corasick — (re)construit à chaque rechargement DB
_AC_AUTOMATON = None

def _rebuild_ac_automaton():
    """Construit (ou reconstruit) l'automate Aho-Corasick à partir de _DB_VALUES."""
    global _AC_AUTOMATON
    try:
        import ahocorasick
        A = ahocorasick.Automaton()
        _CATEGORY_MAP = {
            "noms":         ("NOM",     "<<NOM_{i}>>"),
            "pieces":       ("PIECE",   "<<PIECE_{i}>>"),
            "clients":      ("CLIENT",  "<<CLIENT_{i}>>"),
            "fournisseurs": ("FOUR",    "<<FOUR_{i}>>"),
            "articles":     ("ARTICLE", "<<ARTICLE_{i}>>"),
        }
        for key, (category, template) in _CATEGORY_MAP.items():
            for val in _DB_VALUES.get(key, []):
                if val and len(val) >= 2:
                    A.add_word(val.lower(), (val.lower(), val, category, template))
        if len(A) > 0:
            A.make_automaton()
            _AC_AUTOMATON = A
        else:
            _AC_AUTOMATON = None
    except (ImportError, Exception):
        _AC_AUTOMATON = None

def _get_ac_automaton():
    """Retourne l'automate courant (None si vide ou pyahocorasick absent)."""
    return _AC_AUTOMATON


# ─────────────────────────────────────────────────────────────────────
# Patterns regex CALIBRÉS sur les formats réels observés
# (pour attraper les NOUVELLES valeurs non encore en DB)
# ─────────────────────────────────────────────────────────────────────
_REGEX_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # Nouveaux codes client : CLI001, CLI1234
    ("CLIENT",  "<<CLIENT_{i}>>",
     re.compile(r"\bCLI\d{2,6}\b", re.IGNORECASE)),

    # Nouveaux codes fournisseur : FOUR01, FOUR003
    ("FOUR",    "<<FOUR_{i}>>",
     re.compile(r"\bFOUR\d{2,6}\b", re.IGNORECASE)),

    # Numéros de pièce (format court 5 chiffres OU timestamp 12 chiffres)
    # Préfixes observés : BL, FA, OF, BC, BR, FF, BF, AV
    ("PIECE",   "<<PIECE_{i}>>",
     re.compile(
         r"\b(?:FA|FF|BL|BC|BF|OF|BR|AV)[0-9]{5,14}\b",
         re.IGNORECASE,
     )),

    # Montants numériques avec décimales (évite d'attraper les années 2024, 2025)
    ("MONTANT", "<<MONTANT_{i}>>",
     re.compile(
         r"\b\d{1,9}[.,]\d{2}\s*(?:€|dt|dh|eur|tnd|dinar|euro)?\b",
         re.IGNORECASE,
     )),
]

# Mots SQL / ERP structurels à ne jamais anonymiser
_WHITELIST = frozenset({
    # SQL
    "select", "from", "where", "join", "group", "order", "limit", "having",
    "count", "sum", "avg", "max", "min", "null", "true", "false", "distinct",
    "and", "or", "not", "in", "on", "by", "as", "is", "case", "when", "then",
    "else", "end", "between", "like", "left", "right", "inner", "outer",
    "union", "insert", "update", "delete", "create", "table", "index",
    # ERP types (ne pas confondre avec des pièces sans suffixe chiffré)
    "bl", "bc", "bf", "of", "fa", "ff", "br",
    # Abréviations métier
    "ca", "ht", "ttc", "tva", "dt", "dh", "eur",
    # Mots courants français pouvant ressembler à des codes
    "client", "clients", "facture", "factures", "article", "articles",
    "stock", "stockage", "montant", "montants", "date", "type", "code",
    "total", "solde", "encours", "liste",
})


# ─────────────────────────────────────────────────────────────────────
# Classe d'anonymisation stateful
# ─────────────────────────────────────────────────────────────────────
class _Anonymizer:
    """État d'anonymisation pour un seul appel (une question + réponse)."""

    def __init__(self):
        self._map: dict[str, str] = {}         # token → valeur originale
        self._reverse: dict[str, str] = {}     # valeur originale → token
        self._counters: dict[str, int] = {}

        # Charger les valeurs DB si ce n'est pas déjà fait
        _load_db_values()

    def _next_token(self, category: str, template: str) -> str:
        n = self._counters.get(category, 0) + 1
        self._counters[category] = n
        return template.replace("{i}", str(n))

    def _register(self, val: str, category: str, template: str) -> str:
        """Enregistre val→token et retourne le token."""
        if val in self._reverse:
            return self._reverse[val]
        tok = self._next_token(category, template)
        self._map[tok] = val
        self._reverse[val] = tok
        return tok

    # ── Passe 1 : valeurs exactes DB — Aho-Corasick O(M+K) ─────────
    def _anonymise_db_values(self, text: str) -> str:
        """Remplace toutes les valeurs DB en un seul passage via Aho-Corasick.
        
        Complexité : O(M + K) où M = longueur du texte, K = nb de correspondances.
        Avant : O(N*M) avec N boucles regex compilées à la volée.
        """
        try:
            import ahocorasick
        except ImportError:
            # Fallback gracieux si pyahocorasick n'est pas installé
            return self._anonymise_db_values_fallback(text)

        # Construction de l'automate (compilé une fois par session via _AC_AUTOMATON)
        automaton = _get_ac_automaton()
        if automaton is None:
            return self._anonymise_db_values_fallback(text)

        # Parcours du texte en un seul scan
        # Collecte des matches : (start, end, val, category)
        text_lower = text.lower()
        hits: list[tuple[int, int, str, str, str]] = []
        for end_idx, (val_lower, original, category, template) in automaton.iter(text_lower):
            start_idx = end_idx - len(val_lower) + 1
            # Vérifier qu'on est bien sur des word boundaries
            before_ok = (start_idx == 0 or not text[start_idx - 1].isalnum() and text[start_idx - 1] != '_')
            after_ok = (end_idx + 1 >= len(text) or not text[end_idx + 1].isalnum() and text[end_idx + 1] != '_')
            if before_ok and after_ok:
                hits.append((start_idx, end_idx + 1, original, category, template))

        if not hits:
            return text

        # Résolution des chevauchements : garder le match le plus long
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        resolved: list[tuple[int, int, str, str, str]] = []
        last_end = -1
        for h in hits:
            if h[0] >= last_end:
                resolved.append(h)
                last_end = h[1]

        # Reconstruction du texte avec substitutions
        result_parts = []
        cursor = 0
        for start, end, original, category, template in resolved:
            result_parts.append(text[cursor:start])
            tok = self._register(original, category, template)
            result_parts.append(tok)
            cursor = end
        result_parts.append(text[cursor:])
        return "".join(result_parts)

    def _anonymise_db_values_fallback(self, text: str) -> str:
        """Passe 1 originale O(N) — utilisée uniquement si pyahocorasick est absent."""
        result = text
        all_values = (
            [("noms", "NOM", "<<NOM_{i}>>")]
            + [("pieces", "PIECE", "<<PIECE_{i}>>")]
            + [("clients", "CLIENT", "<<CLIENT_{i}>>")]
            + [("fournisseurs", "FOUR", "<<FOUR_{i}>>")]
            + [("articles", "ARTICLE", "<<ARTICLE_{i}>>")]
        )
        for key, category, template in all_values:
            for val in _DB_VALUES.get(key, []):
                pattern = re.compile(r"\b" + re.escape(val) + r"\b", re.IGNORECASE)
                if pattern.search(result):
                    tok = self._register(val, category, template)
                    result = pattern.sub(tok, result)
        return result

    # ── Passe 2 : regex pour nouvelles valeurs non encore en DB ─────
    def _anonymise_regex(self, text: str) -> str:
        result = text
        for category, template, pattern in _REGEX_PATTERNS:
            def _replace(m: re.Match, cat=category, tmpl=template) -> str:
                val = m.group(0)
                # Ignorer si c'est un token déjà posé (<<...>>)
                if val.startswith("<<") and val.endswith(">>"):
                    return val
                # Ignorer la whitelist
                if val.lower() in _WHITELIST:
                    return val
                # Ignorer si déjà enregistré comme valeur originale (passe 1)
                if val in self._reverse:
                    return self._reverse[val]
                # Vérifier que la valeur n'est pas déjà un token
                return self._register(val, cat, tmpl)
            result = pattern.sub(_replace, result)
        return result

    def anonymise(self, text: str) -> str:
        """Anonymise le texte en deux passes : DB exacte puis regex."""
        result = self._anonymise_db_values(text)
        result = self._anonymise_regex(result)
        return result

    def deanonymise(self, text: str) -> str:
        """Restaure les valeurs originales dans la réponse du LLM.

        FIX : le LLM a tendance à retirer les chevrons << >> quand il
        recopie un token (surtout en le mettant dans un tableau markdown
        ou une phrase reformulée). On restaure donc aussi bien la forme
        encadrée que la forme nue, comme le fait déjà deanonymise_with_map
        pour le SQL généré par Vanna.
        """
        result = text
        # Du plus long token au plus court pour éviter les remplacements partiels
        for tok, val in sorted(self._map.items(), key=lambda x: len(x[0]), reverse=True):
            # 1. Forme encadrée exacte : <<NOM_1>>
            result = result.replace(tok, val)
            # 2. Forme nue (chevrons retirés par le LLM) : NOM_1
            tok_nu = tok.replace("<<", "").replace(">>", "")
            result = re.sub(rf"\b{re.escape(tok_nu)}\b", lambda _: val, result)
        return result


# ─────────────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────────────

async def invoke_llm_anonymise(
    prompt: str,
    llm_fn: Callable[..., Awaitable[str]],
    **kwargs: Any,
) -> str:
    """
    Wrapper d'anonymisation pour tout appel LLM externe (version async).

    Usage :
        synthese = await invoke_llm_anonymise(prompt, _invoke_llm, use_smart=True)
    """
    anon = _Anonymizer()
    prompt_safe = anon.anonymise(prompt)

    nb_tokens = len(anon._map)
    if nb_tokens:
        print(f"   [Anonymiseur] {nb_tokens} valeur(s) masquee(s) avant envoi LLM")

    response_safe = await llm_fn(prompt_safe, **kwargs)
    response = anon.deanonymise(response_safe)
    return response


def anonymise_sync(text: str) -> tuple[str, dict[str, str]]:
    """
    Version synchrone pour usage dans un thread non-async (ex : Vanna).

    Retourne : (texte_anonymisé, restore_map)
    La restore_map doit être passée à deanonymise_with_map() après génération SQL.
    """
    anon = _Anonymizer()
    text_safe = anon.anonymise(text)
    nb_tokens = len(anon._map)
    if nb_tokens:
        print(f"   [Anonymiseur Vanna] {nb_tokens} valeur(s) masquee(s) dans la question")
    return text_safe, anon._map.copy()


def deanonymise_with_map(text: str, restore_map: dict[str, str]) -> str:
    """
    Restaure les valeurs originales dans *text* (ex : SQL généré par Vanna).

    Usage :
        question_safe, restore_map = anonymise_sync(demande_brute)
        sql_safe = vanna.generate_sql(question_safe)
        sql_final = deanonymise_with_map(sql_safe, restore_map)
    """
    result = text
    for tok, val in sorted(restore_map.items(), key=lambda x: len(x[0]), reverse=True):
        # 1. Remplacement exact du token (avec chevrons)
        result = result.replace(tok, val)
        # 2. Remplacement de la version nue (sans chevrons) générée par le LLM
        tok_nu = tok.replace("<<", "").replace(">>", "")
        result = re.sub(rf"\b{re.escape(tok_nu)}\b", lambda _: val, result)
    return result


def preload() -> None:
    """Force le chargement DB immédiat (à appeler au warmup)."""
    _load_db_values()