"""
response_cache.py — Cache TTL v2
==================================
Corrections v2 :
  AMÉLIORATION #7 : invalidate_writes() purge aussi le cache DISQUE (shelve)
                    en plus du cache RAM → plus de données périmées après écriture.
  AMÉLIORATION #8 : support multi-utilisateur via user_id optionnel dans la clé
                    → chaque utilisateur a son propre espace de cache.
"""

import asyncio
import os
import time
import shelve
import hashlib
import json
from typing import Any

CACHE_TTL      = float(os.getenv("CACHE_TTL",      "120"))
DISK_CACHE_TTL = float(os.getenv("DISK_CACHE_TTL", "600"))
DISK_CACHE_PATH = os.getenv("DISK_CACHE_PATH", "./disk_cache_sage")

ACTIONS_CACHABLES = {
    "CA_GLOBAL", "LISTE_ARTICLES", "TOP_CLIENTS", "PALMARES_ARTICLES",
    "LISTE_CLIENTS", "RENTABILITE", "SAISONNALITE", "DSO", "RFM",
}

# Actions invalidées après une écriture
ACTIONS_WRITE_SENSITIVE = {
    "CA_GLOBAL", "TOP_CLIENTS", "PALMARES_ARTICLES", "LISTE_CLIENTS",
    "RENTABILITE", "DSO", "RFM", "CLIENTS_BAISSE", "FACTURES_NON_REGLEES",
    "LISTE_ARTICLES",
}


class ResponseCache:
    """Cache TTL RAM + Disque avec support multi-utilisateur."""

    def __init__(self, ttl: float = CACHE_TTL):
        self._ttl   = ttl
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock  = asyncio.Lock()
        self._disk_lock: asyncio.Lock | None = None

    def _ram_key(self, action: str, user_id: str = "", **kwargs) -> str:
        parts = [action]
        if user_id:
            parts.append(f"u={user_id}")
        for k, v in sorted(kwargs.items()):
            if v:
                parts.append(f"{k}={v}")
        return ":".join(parts)

    def _disk_key(self, action: str, user_id: str = "", **kwargs) -> str:
        raw = action + ":" + user_id + ":" + json.dumps(kwargs, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

    def is_cachable(self, action: str) -> bool:
        return action in ACTIONS_CACHABLES

    # ── Cache RAM ────────────────────────────────────────────────────

    async def get(self, action: str, user_id: str = "", **kwargs) -> Any | None:
        key = self._ram_key(action, user_id, **kwargs)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, ts = entry
            if time.time() - ts > self._ttl:
                del self._store[key]
                return None
            return value

    async def set(self, action: str, value: Any, user_id: str = "", **kwargs):
        key = self._ram_key(action, user_id, **kwargs)
        async with self._lock:
            self._store[key] = (value, time.time())

    # ── Cache Disque ─────────────────────────────────────────────────

    async def _get_disk_lock(self) -> asyncio.Lock:
        if self._disk_lock is None:
            self._disk_lock = asyncio.Lock()
        return self._disk_lock

    async def disk_get(self, action: str, user_id: str = "", **kwargs) -> str | None:
        lock = await self._get_disk_lock()
        key  = self._disk_key(action, user_id, **kwargs)
        try:
            async with lock:
                def _read():
                    with shelve.open(DISK_CACHE_PATH) as db:
                        entry = db.get(key)
                        if entry is None:
                            return None
                        value, ts = entry
                        if time.time() - ts > DISK_CACHE_TTL:
                            del db[key]
                            return None
                        return value
                return await asyncio.to_thread(_read)
        except Exception:
            return None

    async def disk_set(self, action: str, value: str, user_id: str = "", **kwargs):
        lock = await self._get_disk_lock()
        key  = self._disk_key(action, user_id, **kwargs)
        try:
            async with lock:
                def _write():
                    with shelve.open(DISK_CACHE_PATH) as db:
                        db[key] = (value, time.time())
                await asyncio.to_thread(_write)
        except Exception:
            pass

    # ── Invalidation ─────────────────────────────────────────────────

    async def invalidate_writes(self, user_id: str = ""):
        """
        AMÉLIORATION #7 : invalide RAM + DISQUE après une écriture ERP.
        Sans user_id → invalide tout le cache (toutes actions sensibles).
        Avec user_id → invalide seulement les entrées de cet utilisateur en RAM.
        Le disque est entièrement purgé des actions sensibles (pas de filtre user).
        """
        # RAM
        async with self._lock:
            prefix_filter = (f"u={user_id}:" if user_id else "")
            keys_to_remove = [
                k for k in self._store
                if any(k.startswith(a) or (prefix_filter and a in k)
                       for a in ACTIONS_WRITE_SENSITIVE)
            ]
            for k in keys_to_remove:
                del self._store[k]
            if keys_to_remove:
                print(f"   🗑️  [Cache RAM] {len(keys_to_remove)} entrées invalidées.")

        # DISQUE — purge complète des actions sensibles
        lock = await self._get_disk_lock()
        try:
            async with lock:
                def _purge_disk():
                    purged = 0
                    with shelve.open(DISK_CACHE_PATH) as db:
                        # shelve ne supporte pas la suppression pendant l'itération
                        keys = list(db.keys())
                        for k in keys:
                            entry = db.get(k)
                            if entry is None:
                                continue
                            # On stocke l'action dans la valeur ? Non — on purge tout
                            # ce qui est expiré ou trop vieux (sécurité conservative)
                            _, ts = entry
                            # Marque comme expiré → sera supprimé au prochain accès
                            # Option plus agressive : supprimer toutes les entrées
                            if time.time() - ts > 0:  # toujours vrai → purge totale
                                del db[k]
                                purged += 1
                    return purged
                purged = await asyncio.to_thread(_purge_disk)
                if purged:
                    print(f"   🗑️  [Cache Disque] {purged} entrées purgées après écriture.")
        except Exception:
            pass

    def stats(self) -> str:
        now   = time.time()
        total = len(self._store)
        valid = sum(1 for _, (_, ts) in self._store.items() if now - ts <= self._ttl)
        return f"Cache RAM: {valid}/{total} entrées valides (TTL={self._ttl}s)"


cache = ResponseCache()