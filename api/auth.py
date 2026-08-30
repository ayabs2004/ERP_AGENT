"""Authentication module for Copilot ERP.

Provides user management with hashed passwords stored in a JSON file,
JWT token creation and validation, and FastAPI dependencies to retrieve the
current user and enforce admin rights.
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

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = Path(os.getenv("USERS_FILE", str(BASE_DIR / "data" / "users.json")))
USERS_FILE.parent.mkdir(parents=True, exist_ok=True)

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
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

PBKDF2_ITERATIONS = 260_000
PBKDF2_ALGO = "sha256"


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a random salt and return a formatted hash string."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO, password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_{PBKDF2_ALGO}${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash, returning True if they match."""
    try:
        algo_part, iterations_str, salt, hash_hex = stored_hash.split("$")
        algo = algo_part.replace("pbkdf2_", "")
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(dk.hex(), hash_hex)


class Utilisateur(TypedDict):
    """TypedDict describing a user record with username, password hash, role, full name, active flag, and creation timestamp."""
    username: str
    password_hash: str
    role: str            # "admin" | "commercial" | ...
    nom_complet: str
    actif: bool
    cree_le: str


def _charger_utilisateurs() -> dict[str, Utilisateur]:
    """Load the user dictionary from the JSON storage file, returning an empty dict if the file does not exist or cannot be parsed."""
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("utilisateurs", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _sauvegarder_utilisateurs(utilisateurs: dict[str, Utilisateur]) -> None:
    """Atomically write the provided user dictionary to the JSON storage file."""
    tmp = USERS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"utilisateurs": utilisateurs}, f, ensure_ascii=False, indent=2)
    tmp.replace(USERS_FILE)


def creer_utilisateur(
    username: str, password: str, role: str = "commercial", nom_complet: str = ""
) -> Utilisateur:
    """Create a new active user with the given credentials, store it in the JSON file, and return the user record."""
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
    """Change the password for the specified user after verifying the old password."""
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
    """Deactivate the specified user account."""
    utilisateurs = _charger_utilisateurs()
    u = utilisateurs.get(username.strip().lower())
    if u:
        u["actif"] = False
        _sauvegarder_utilisateurs(utilisateurs)


def authentifier(username: str, password: str) -> Optional[Utilisateur]:
    """Authenticate a username and password. Returns the user record if credentials are valid and the account is active; otherwise returns None. Performs