"""
auth.py — Authentification utilisateur (login / mot de passe) pour Copilot ERP.
================================================================================
Remplace le "jeton API partagé" (un seul secret copié dans le frontend, visible
par n'importe qui ouvre les DevTools) par un vrai système de comptes :

  - Chaque utilisateur a un identifiant + un mot de passe.
  - Les mots de passe ne sont JAMAIS stockés en clair : ils sont hashés avec
    PBKDF2-HMAC-SHA256 (sel aléatoire par utilisateur, 260 000 itérations —
    même ordre de grandeur que le hasher par défaut de Django).
  - À la connexion, le serveur délivre un JWT (JSON Web Token) signé, avec une
    durée de vie limitée. Ce token est envoyé par le frontend sur chaque appel
    (Authorization: Bearer <jwt>), et le serveur le valide à chaque requête —
    sans jamais revoir le mot de passe.
  - Le JWT contient l'identité (username, rôle) mais AUCUN secret exploitable :
    même intercepté, il expire et ne permet pas de retrouver le mot de passe.

Stockage des comptes : fichier JSON `data/users.json` (suffisant pour un outil
interne à quelques dizaines d'utilisateurs). Pour une volumétrie plus grande,
remplacer `_charger_utilisateurs` / `_sauvegarder_utilisateurs` par des requêtes
vers une vraie table SQL — le reste du module n'a pas besoin de changer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

import jwt  # PyJWT
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = Path(os.getenv("USERS_FILE", str(BASE_DIR / "data" / "users.json")))
USERS_FILE.parent.mkdir(parents=True, exist_ok=True)

# Secret de signature JWT — DOIT être défini en prod (pas de défaut en dur).
# En dev, si absent, on en génère un aléatoire au démarrage : les tokens émis
# ne resteront valides que tant que le process tourne (relance = déconnexion
# de tout le monde), ce qui est un garde-fou volontaire contre les oublis.
JWT_SECRET = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(48)
    print(
        "⚠️  [auth] JWT_SECRET_KEY non défini dans l'environnement : "
        "un secret temporaire a été généré pour cette session serveur. "
        "Définis JWT_SECRET_KEY dans .env pour que les connexions "
        "survivent aux redémarrages."
    )

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8h par défaut

PBKDF2_ITERATIONS = 260_000
PBKDF2_ALGO = "sha256"


# ─────────────────────────────────────────────────────────────────────
# HASHING MOT DE PASSE (PBKDF2 — stdlib uniquement, pas de dépendance native)
# ─────────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO, password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_{PBKDF2_ALGO}${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo_part, iterations_str, salt, hash_hex = stored_hash.split("$")
        algo = algo_part.replace("pbkdf2_", "")
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(dk.hex(), hash_hex)


# ─────────────────────────────────────────────────────────────────────
# STOCKAGE UTILISATEURS (fichier JSON)
# ─────────────────────────────────────────────────────────────────────
class Utilisateur(TypedDict):
    username: str
    password_hash: str
    role: str            # "admin" | "commercial" | ...
    nom_complet: str
    actif: bool
    cree_le: str


def _charger_utilisateurs() -> dict[str, Utilisateur]:
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("utilisateurs", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _sauvegarder_utilisateurs(utilisateurs: dict[str, Utilisateur]) -> None:
    tmp = USERS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"utilisateurs": utilisateurs}, f, ensure_ascii=False, indent=2)
    tmp.replace(USERS_FILE)  # écriture atomique


def creer_utilisateur(
    username: str, password: str, role: str = "commercial", nom_complet: str = ""
) -> Utilisateur:
    username = username.strip().lower()
    if not username or len(username) < 3:
        raise ValueError("Identifiant trop court (3 caractères minimum).")
    if len(password) < 8:
        raise ValueError("Mot de passe trop court (8 caractères minimum).")

    utilisateurs = _charger_utilisateurs()
    if username in utilisateurs:
        raise ValueError(f"L'utilisateur '{username}' existe déjà.")

    utilisateur: Utilisateur = {
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "nom_complet": nom_complet or username,
        "actif": True,
        "cree_le": datetime.now(timezone.utc).isoformat(),
    }
    utilisateurs[username] = utilisateur
    _sauvegarder_utilisateurs(utilisateurs)
    return utilisateur


def changer_mot_de_passe(username: str, ancien: str, nouveau: str) -> None:
    utilisateurs = _charger_utilisateurs()
    username = username.strip().lower()
    u = utilisateurs.get(username)
    if not u or not verify_password(ancien, u["password_hash"]):
        raise ValueError("Ancien mot de passe incorrect.")
    if len(nouveau) < 8:
        raise ValueError("Nouveau mot de passe trop court (8 caractères minimum).")
    u["password_hash"] = hash_password(nouveau)
    _sauvegarder_utilisateurs(utilisateurs)


def desactiver_utilisateur(username: str) -> None:
    utilisateurs = _charger_utilisateurs()
    u = utilisateurs.get(username.strip().lower())
    if u:
        u["actif"] = False
        _sauvegarder_utilisateurs(utilisateurs)


def authentifier(username: str, password: str) -> Optional[Utilisateur]:
    """Vérifie identifiant + mot de passe. Retourne l'utilisateur si OK, sinon None.
    Le timing est volontairement constant (on hash même si l'utilisateur n'existe
    pas) pour ne pas laisser deviner par mesure de temps si un compte existe."""
    utilisateurs = _charger_utilisateurs()
    username_n = username.strip().lower()
    u = utilisateurs.get(username_n)
    if u is None:
        # Anti-timing-attack : on fait quand même un hash bidon.
        hash_password(password)
        return None
    if not u.get("actif", True):
        return None
    if not verify_password(password, u["password_hash"]):
        return None
    return u


def _bootstrap_admin_si_necessaire() -> None:
    """Au tout premier démarrage (aucun utilisateur enregistré), crée un compte
    admin initial pour ne pas se retrouver bloqué dehors. Le mot de passe vient
    de ADMIN_USERNAME/ADMIN_PASSWORD si fournis, sinon un mot de passe aléatoire
    est généré et écrit UNE SEULE FOIS dans data/admin_credentials_INITIAL.txt
    (à lire, noter, puis supprimer)."""
    utilisateurs = _charger_utilisateurs()
    if utilisateurs:
        return

    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    genere = admin_pass is None
    if genere:
        admin_pass = secrets.token_urlsafe(12)

    creer_utilisateur(admin_user, admin_pass, role="admin", nom_complet="Administrateur")

    if genere:
        fichier = USERS_FILE.parent / "admin_credentials_INITIAL.txt"
        with open(fichier, "w", encoding="utf-8") as f:
            f.write(
                f"Compte admin initial créé automatiquement.\n"
                f"Identifiant : {admin_user}\n"
                f"Mot de passe : {admin_pass}\n\n"
                f"⚠️  Connecte-toi, crée tes vrais comptes, puis SUPPRIME ce fichier.\n"
            )
        print(
            f"🔑 [auth] Aucun utilisateur trouvé → compte admin créé.\n"
            f"          Identifiant : {admin_user}\n"
            f"          Identifiants complets écrits dans : {fichier}\n"
            f"          (définis ADMIN_USERNAME / ADMIN_PASSWORD dans .env pour éviter "
            f"ce comportement au prochain déploiement.)"
        )
    else:
        print(f"🔑 [auth] Compte admin initial créé depuis .env : {admin_user}")


_bootstrap_admin_si_necessaire()


# ─────────────────────────────────────────────────────────────────────
# JWT
# ─────────────────────────────────────────────────────────────────────
def create_access_token(username: str, role: str) -> tuple[str, int]:
    """Retourne (token, expires_in_seconds)."""
    now = int(time.time())
    expires_in = JWT_EXPIRE_MINUTES * 60
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + expires_in,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expirée, reconnectez-vous.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token invalide.")


# ─────────────────────────────────────────────────────────────────────
# DÉPENDANCE FASTAPI : UTILISATEUR COURANT
# ─────────────────────────────────────────────────────────────────────
_bearer_scheme = HTTPBearer()


class CurrentUser(TypedDict):
    username: str
    role: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> CurrentUser:
    payload = decode_access_token(credentials.credentials)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Token invalide.")

    # On revérifie que le compte existe toujours et est actif à CHAQUE requête :
    # si un admin désactive un compte, l'accès est coupé immédiatement, sans
    # attendre l'expiration naturelle du token.
    utilisateurs = _charger_utilisateurs()
    u = utilisateurs.get(username)
    if not u or not u.get("actif", True):
        raise HTTPException(status_code=401, detail="Compte désactivé ou introuvable.")

    return {"username": username, "role": payload.get("role", u.get("role", "commercial"))}


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs.")
    return user
