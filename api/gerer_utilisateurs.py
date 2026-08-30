"""Command-line utility for managing user accounts.

Provides commands to create, deactivate, and list users by invoking functions from the auth module. Intended for internal admin use; no public registration route is exposed.
"""

import getpass
import sys

sys.path.insert(0, ".")

from auth import creer_utilisateur, desactiver_utilisateur, _charger_utilisateurs


def main():
    """Parse command‑line arguments and execute user‑management actions.

    Supported commands:
    - creer: create a new user with a password, role, and optional full name.
    - desactiver: deactivate an existing user.
    - liste: display a list of all users with their status.
    """
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "creer":
        if len(sys.argv) < 4:
            print("Usage : creer <username> <role: admin|commercial> [nom complet...]")
            return
        username = sys.argv[2]
        role = sys.argv[3]
        nom_complet = " ".join(sys.argv[4:]) or username
        password = getpass.getpass("Mot de passe (min. 8 caractères) : ")
        password2 = getpass.getpass("Confirmer le mot de passe : ")
        if password != password2:
            print("❌ Les mots de passe ne correspondent pas.")
            return
        try:
            creer_utilisateur(username, password, role=role, nom_complet=nom_complet)
            print(f"✅ Utilisateur '{username}' créé (rôle: {role}).")
        except ValueError as e:
            print(f"❌ {e}")

    elif cmd == "desactiver":
        if len(sys.argv) < 3:
            print("Usage : desactiver <username>")
            return
        desactiver_utilisateur(sys.argv[2])
        print(f"✅ Utilisateur '{sys.argv[2]}' désactivé.")

    elif cmd == "liste":
        utilisateurs = _charger_utilisateurs()
        if not utilisateurs:
            print("Aucun utilisateur.")
            return
        for u in utilisateurs.values():
            statut = "actif" if u.get("actif", True) else "désactivé"
            print(f"  {u['username']:20s}  rôle={u['role']:12s}  {statut:10s}  {u.get('nom_complet','')}")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()