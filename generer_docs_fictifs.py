#!/usr/bin/env python3
"""
generer_docs_fictifs.py — Génération de documents fictifs pour le RAG ERP
==========================================================================
Génère, à partir des VRAIES données de entreprise_mock.db (clients,
fournisseurs, articles), des documents texte fictifs mais cohérents :

  - fiche_article     : fiche technique par article (AR_Ref réel)
  - commande_email     : email de commande client (CT_Num + articles réels)
  - note_crm            : conditions négociées / historique client
  - reclamation_sav    : réclamation qualité sur un article

Chaque document est écrit en .md dans kb_docs/ avec un en-tête de
métadonnées (frontmatter simple) réutilisé ensuite par indexer_kb.py
pour le filtrage Qdrant (doc_type, ref_article, code_client).

Utilise Ollama pour varier le style d'écriture (optionnel). Si Ollama
n'est pas disponible, bascule automatiquement sur une génération par
templates + variations aléatoires (le script fonctionne toujours).

Usage :
    python generer_docs_fictifs.py
"""

import os
import re
import random
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "entreprise_mock.db"))
OUT_DIR = Path(__file__).parent / "kb_docs"
OLLAMA_MODEL_CHAT = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2")

random.seed(42)  # reproductible

# ── Tentative d'utiliser Ollama pour varier le style ──────────────────
try:
    import ollama

    def _test_ollama() -> bool:
        try:
            ollama.chat(
                model=OLLAMA_MODEL_CHAT,
                messages=[{"role": "user", "content": "ok"}],
            )
            return True
        except Exception:
            return False

    USE_LLM = _test_ollama()
except ImportError:
    USE_LLM = False

if USE_LLM:
    print(f"✅ Ollama disponible ({OLLAMA_MODEL_CHAT}) → génération variée par LLM")
else:
    print("⚠️  Ollama indisponible → génération par templates (fallback, toujours fonctionnel)")


import os
from langchain_openai import ChatOpenAI

# ── Mêmes variables d'env que orchestrateur_general.py ────────────────
GROQ_URL   = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1")
GROQ_KEY   = (os.getenv("GROQ_KEY", "") or "").strip()
GROQ_MODEL = os.getenv("GROQ_FAST", "llama-3.1-8b-instant")  # rapide, suffisant pour varier le style

_llm_groq = ChatOpenAI(
    model=GROQ_MODEL,
    temperature=0.8,
    api_key=GROQ_KEY,
    base_url=GROQ_URL,
) if GROQ_KEY else None


def _test_groq() -> bool:
    if _llm_groq is None:
        return False
    try:
        _llm_groq.invoke("ok")
        return True
    except Exception:
        return False


USE_LLM = _test_groq()

if USE_LLM:
    print(f"✅ Groq disponible ({GROQ_MODEL}) → génération variée par LLM")
else:
    print("⚠️  Groq indisponible → génération par templates (fallback, toujours fonctionnel)")


def llm(prompt: str, fallback: str) -> str:
    """Appelle Groq (via ChatOpenAI) si disponible, sinon renvoie le texte de secours."""
    if not USE_LLM:
        return fallback
    try:
        r = _llm_groq.invoke(prompt)
        texte = r.content.strip() if isinstance(r.content, str) else str(r.content).strip()
        return texte if texte else fallback
    except Exception:
        return fallback
# ─────────────────────────────────────────────────────────────────────
# LECTURE DES DONNÉES RÉELLES
# ─────────────────────────────────────────────────────────────────────
def charger_donnees():
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(
            f"Base introuvable : {DB_PATH}\n"
            f"Vérifie la variable DB_PATH ou place ce script à côté de entreprise_mock.db"
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    clients = conn.execute(
        "SELECT CT_Num, CT_Intitule FROM F_COMPTET WHERE CT_Type=0"
    ).fetchall()
    fournisseurs = conn.execute(
        "SELECT CT_Num, CT_Intitule FROM F_COMPTET WHERE CT_Type=1"
    ).fetchall()
    articles = conn.execute(
        "SELECT AR_Ref, AR_Design, AR_PrixVen, AR_PrixAch FROM F_ARTICLE"
    ).fetchall()

    conn.close()

    if not clients or not articles:
        raise RuntimeError(
            "Base vide (aucun client ou article trouvé). "
            "Vérifie que entreprise_mock.db est bien initialisée."
        )

    return clients, fournisseurs, articles


# ─────────────────────────────────────────────────────────────────────
# HELPERS FRONTMATTER
# ─────────────────────────────────────────────────────────────────────
def _ecrire_doc(nom_fichier: str, metadata: dict, corps: str):
    OUT_DIR.mkdir(exist_ok=True)
    lignes_meta = ["---"]
    for k, v in metadata.items():
        lignes_meta.append(f"{k}: {v}")
    lignes_meta.append("---")
    contenu = "\n".join(lignes_meta) + "\n\n" + corps.strip() + "\n"
    (OUT_DIR / nom_fichier).write_text(contenu, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# 1. FICHES ARTICLE
# ─────────────────────────────────────────────────────────────────────
_MATIERES = ["polypropylène", "acier inoxydable", "aluminium anodisé", "ABS renforcé", "PVC rigide", "verre trempé"]
_PROCESS = ["injection plastique", "usinage CNC", "emboutissage", "assemblage manuel", "moulage sous pression"]
_USAGES = [
    "usage intérieur, environnement sec",
    "usage extérieur, résistant aux intempéries",
    "usage industriel intensif",
    "usage domestique standard",
    "environnement à température contrôlée",
]


def generer_fiche_article(art) -> None:
    ref, design, prix_vente, prix_achat = art["AR_Ref"], art["AR_Design"], art["AR_PrixVen"], art["AR_PrixAch"]
    matiere = random.choice(_MATIERES)
    process = random.choice(_PROCESS)
    usage = random.choice(_USAGES)
    tolerance = f"±{random.choice([0.1, 0.2, 0.5, 1.0])} mm"
    temp = f"{random.randint(180, 260)}°C" if "injection" in process or "moulage" in process else "N/A"

    fallback = f"""# Fiche technique — {ref} ({design})

## Caractéristiques
- Matière : {matiere}
- Procédé de fabrication : {process}
- Tolérance dimensionnelle : {tolerance}
- Température de process : {temp}

## Usage recommandé
Cet article est destiné à un {usage}.

## Prix
- Prix d'achat : {prix_achat:.2f}
- Prix de vente : {prix_vente:.2f}
"""
    prompt = (
        f"Rédige en français une fiche technique interne courte et réaliste pour un article ERP.\n"
        f"Référence : {ref}\nDésignation : {design}\n"
        f"Matière : {matiere}\nProcédé : {process}\nUsage : {usage}\n"
        f"Format markdown avec sections Caractéristiques / Usage recommandé / Précautions. "
        f"Reste concis (10-15 lignes)."
    )
    corps = llm(prompt, fallback)

    _ecrire_doc(
        f"fiche_article_{ref}.md",
        {"doc_type": "fiche_article", "ref_article": ref, "code_client": ""},
        corps,
    )


# ─────────────────────────────────────────────────────────────────────
# 2. EMAILS DE COMMANDE CLIENT
# ─────────────────────────────────────────────────────────────────────
_INTROS_EMAIL = [
    "Bonjour,\n\nPourriez-vous préparer la commande suivante :",
    "Bonjour,\n\nComme convenu, voici notre commande :",
    "Bjr,\n\nOn a besoin rapidement de :",
    "Bonjour à l'équipe,\n\nMerci de traiter cette commande dès que possible :",
]
_SIGNATURES = ["Cordialement,", "Merci d'avance,", "Bien à vous,", "Salutations,"]
_REMARQUES = [
    "Livraison souhaitée avant la fin du mois si possible.",
    "Merci de confirmer les délais de livraison.",
    "Facturation à adresser au service comptabilité comme d'habitude.",
    "",
    "Attention, référence article à vérifier, pas sûr du code exact.",
]


def generer_email_commande(client, articles, index: int) -> None:
    code_client, nom_client = client["CT_Num"], client["CT_Intitule"]
    nb_lignes = random.randint(1, 3)
    lignes_art = random.sample(articles, k=min(nb_lignes, len(articles)))

    details = []
    for a in lignes_art:
        qte = random.choice([5, 10, 20, 25, 50, 100])
        details.append(f"- {a['AR_Design']} ({a['AR_Ref']}) x {qte}")

    remarque = random.choice(_REMARQUES)
    date_email = (datetime.now() - timedelta(days=random.randint(1, 180))).strftime("%d/%m/%Y")

    fallback = (
        f"{random.choice(_INTROS_EMAIL)}\n\n"
        + "\n".join(details)
        + (f"\n\n{remarque}" if remarque else "")
        + f"\n\n{random.choice(_SIGNATURES)}\n{nom_client}"
    )

    prompt = (
        f"Rédige un email professionnel de commande client (français), envoyé le {date_email} par "
        f"{nom_client}, commandant :\n" + "\n".join(details) + "\n"
        f"Ton naturel, parfois pressé, sans formules exagérées. 6-10 lignes maximum."
    )
    corps = llm(prompt, fallback)

    _ecrire_doc(
        f"commande_email_{code_client}_{index}.md",
        {
            "doc_type": "commande_email",
            "ref_article": lignes_art[0]["AR_Ref"] if lignes_art else "",
            "code_client": code_client,
            "date": date_email,
        },
        corps,
    )


# ─────────────────────────────────────────────────────────────────────
# 3. NOTES CRM / CONDITIONS NÉGOCIÉES
# ─────────────────────────────────────────────────────────────────────
def generer_note_crm(client) -> None:
    code_client, nom_client = client["CT_Num"], client["CT_Intitule"]
    remise = random.choice([5, 8, 10, 12, 15])
    delai_paiement = random.choice([30, 45, 60, 90])
    annee = random.choice([2023, 2024, 2025])
    contexte = random.choice([
        "client fidèle depuis plusieurs années",
        "gros volume de commandes trimestrielles",
        "négociation suite à un litige résolu à l'amiable",
        "accord cadre signé avec la direction commerciale",
    ])

    fallback = f"""# Note CRM — {nom_client} ({code_client})

## Conditions commerciales négociées
- Remise contractuelle : {remise}%
- Délai de paiement : {delai_paiement} jours
- Contexte : {contexte}
- Validé en {annee} par le service commercial

## Historique
Ce client bénéficie de conditions particulières suite à {contexte}.
Toute modification de ces conditions doit être validée par la direction commerciale.
"""
    prompt = (
        f"Rédige une note CRM interne courte (français) pour le client {nom_client} ({code_client}) "
        f"précisant : remise négociée {remise}%, délai de paiement {delai_paiement} jours, "
        f"contexte : {contexte}, validé en {annee}. Format markdown, 8-12 lignes."
    )
    corps = llm(prompt, fallback)

    _ecrire_doc(
        f"note_crm_{code_client}.md",
        {"doc_type": "note_crm", "ref_article": "", "code_client": code_client},
        corps,
    )


# ─────────────────────────────────────────────────────────────────────
# 4. RÉCLAMATIONS / SAV
# ─────────────────────────────────────────────────────────────────────
_MOTIFS_RECLAMATION = [
    "défaut de soudure sur le boîtier",
    "rayures constatées à la livraison",
    "dysfonctionnement après quelques semaines d'usage",
    "pièce non conforme au plan technique",
    "emballage endommagé ayant abîmé le produit",
]


def generer_reclamation(article, index: int) -> None:
    ref, design = article["AR_Ref"], article["AR_Design"]
    motif = random.choice(_MOTIFS_RECLAMATION)
    nb_clients = random.randint(1, 15)
    date_reclam = (datetime.now() - timedelta(days=random.randint(1, 365))).strftime("%d/%m/%Y")
    serie = random.choice(["série 2024", "série 2025", "lot de production Q2", "lot de production Q4"])

    fallback = f"""# Réclamation SAV — {design} ({ref})

## Motif
{motif.capitalize()}, signalé pour la {serie}.

## Détails
- Date de signalement : {date_reclam}
- Nombre de clients concernés : {nb_clients}
- Statut : en cours de traitement par le service qualité

## Action recommandée
Vérifier les lots concernés ({serie}) et informer le fournisseur si le défaut provient
d'un composant acheté.
"""
    prompt = (
        f"Rédige une fiche de réclamation SAV interne (français) pour l'article {design} ({ref}), "
        f"motif : {motif}, {serie}, {nb_clients} clients concernés, signalé le {date_reclam}. "
        f"Format markdown, 8-12 lignes."
    )
    corps = llm(prompt, fallback)

    _ecrire_doc(
        f"reclamation_{ref}_{index}.md",
        {"doc_type": "reclamation_sav", "ref_article": ref, "code_client": ""},
        corps,
    )


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    print(f"📂 Base source : {DB_PATH}")
    clients, fournisseurs, articles = charger_donnees()
    print(f"   {len(clients)} clients | {len(fournisseurs)} fournisseurs | {len(articles)} articles")

    OUT_DIR.mkdir(exist_ok=True)
    compteur = {"fiche_article": 0, "commande_email": 0, "note_crm": 0, "reclamation_sav": 0}

    # 1. Fiches article — une par article
    print("📄 Génération des fiches article...")
    for art in articles:
        generer_fiche_article(art)
        compteur["fiche_article"] += 1

    # 2. Emails de commande — 2 à 4 par client
    print("📧 Génération des emails de commande...")
    for client in clients:
        nb_emails = random.randint(2, 4)
        for i in range(nb_emails):
            generer_email_commande(client, articles, i)
            compteur["commande_email"] += 1

    # 3. Notes CRM — environ 40% des clients ont des conditions négociées
    print("🗒️  Génération des notes CRM...")
    for client in clients:
        if random.random() < 0.4:
            generer_note_crm(client)
            compteur["note_crm"] += 1

    # 4. Réclamations — environ 30% des articles ont eu un souci qualité
    print("⚠️  Génération des réclamations SAV...")
    for art in articles:
        if random.random() < 0.3:
            nb_reclam = random.randint(1, 2)
            for i in range(nb_reclam):
                generer_reclamation(art, i)
                compteur["reclamation_sav"] += 1

    print("\n✅ Génération terminée :")
    for doc_type, nb in compteur.items():
        print(f"   {doc_type:<18} : {nb}")
    print(f"\n📁 Fichiers écrits dans : {OUT_DIR}/")
    print("➡️  Lance maintenant : python indexer_kb.py")


if __name__ == "__main__":
    main()