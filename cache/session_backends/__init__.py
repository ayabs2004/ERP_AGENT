"""
session_backends/ — Backends de stockage de session.
Contient :
  - SessionBackend (ABC) : interface abstraite
  - FileSessionBackend : stockage fichier (défaut)
  - RedisSessionBackend : stockage Redis (optionnel)
"""

from abc import ABC, abstractmethod
from typing import Any
import json
import time


class SessionBackend(ABC):
    """Interface abstraite pour le stockage de session."""
    
    @abstractmethod
    async def get(self, session_id: str) -> dict[str, Any] | None:
        """Récupère une session par ID."""
        pass
    
    @abstractmethod
    async def set(self, session_id: str, data: dict[str, Any], ttl: int = 3600) -> None:
        """Stocke une session avec TTL en secondes."""
        pass
    
    @abstractmethod
    async def delete(self, session_id: str) -> None:
        """Supprime une session."""
        pass
    
    @abstractmethod
    async def exists(self, session_id: str) -> bool:
        """Vérifie si une session existe."""
        pass
    
    @abstractmethod
    async def clear_expired(self) -> int:
        """Nettoie les sessions expirées. Retourne le nombre supprimé."""
        pass


class FileSessionBackend(SessionBackend):
    """Backend fichier (JSON) pour développement/test."""
    
    def __init__(self, directory: str = "./sessions"):
        self.directory = directory
        import os
        os.makedirs(directory, exist_ok=True)
    
    def _path(self, session_id: str) -> str:
        import hashlib
        h = hashlib.md5(session_id.encode()).hexdigest()[:16]
        return f"{self.directory}/{h}.json"
    
    async def get(self, session_id: str) -> dict[str, Any] | None:
        import asyncio
        try:
            content = await asyncio.to_thread(self._read, session_id)
            if not content:
                return None
            data = json.loads(content)
            if data.get("expires_at", 0) < time.time():
                await self.delete(session_id)
                return None
            return data.get("data", {})
        except Exception:
            return None
    
    async def set(self, session_id: str, data: dict[str, Any], ttl: int = 3600) -> None:
        import asyncio
        payload = json.dumps({
            "data": data,
            "expires_at": time.time() + ttl,
            "created_at": time.time(),
        })
        await asyncio.to_thread(self._write, session_id, payload)
    
    async def delete(self, session_id: str) -> None:
        import asyncio
        try:
            await asyncio.to_thread(self._remove, session_id)
        except Exception:
            pass
    
    async def exists(self, session_id: str) -> bool:
        import asyncio
        return await asyncio.to_thread(self._check_exists, session_id)
    
    async def clear_expired(self) -> int:
        import asyncio
        return await asyncio.to_thread(self._clear_expired_sync)
    
    def _read(self, session_id: str) -> str:
        try:
            with open(self._path(session_id), "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""
    
    def _write(self, session_id: str, content: str) -> None:
        with open(self._path(session_id), "w", encoding="utf-8") as f:
            f.write(content)
    
    def _remove(self, session_id: str) -> None:
        import os
        try:
            os.remove(self._path(session_id))
        except FileNotFoundError:
            pass
    
    def _check_exists(self, session_id: str) -> bool:
        import os
        return os.path.exists(self._path(session_id))
    
    def _clear_expired_sync(self) -> int:
        import os
        count = 0
        now = time.time()
        for fname in os.listdir(self.directory):
            if not fname.endswith(".json"):
                continue
            fpath = f"{self.directory}/{fname}"
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("expires_at", 0) < now:
                    os.remove(fpath)
                    count += 1
            except Exception:
                pass
        return count


class RedisSessionBackend(SessionBackend):
    """Backend Redis pour production."""
    
    def __init__(self, url: str = "redis://localhost:6379/0", prefix: str = "sage:session:"):
        self.url = url
        self.prefix = prefix
        self._client = None
    
    def _get_client(self):
        if self._client is None:
            try:
                import redis  # type: ignore
                self._client = redis.from_url(self.url, decode_responses=True)
            except ImportError:
                raise ImportError("pip install redis")
        return self._client
    
    def _key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"
    
    async def get(self, session_id: str) -> dict[str, Any] | None:
        client = self._get_client()
        try:
            raw = await client.get(self._key(session_id))
            if not raw:
                return None
            data = json.loads(raw)
            return data.get("data", {})
        except Exception:
            return None
    
    async def set(self, session_id: str, data: dict[str, Any], ttl: int = 3600) -> None:
        client = self._get_client()
        payload = json.dumps({
            "data": data,
            "expires_at": time.time() + ttl,
            "created_at": time.time(),
        })
        await client.setex(self._key(session_id), ttl, payload)
    
    async def delete(self, session_id: str) -> None:
        client = self._get_client()
        try:
            await client.delete(self._key(session_id))
        except Exception:
            pass
    
    async def exists(self, session_id: str) -> bool:
        client = self._get_client()
        try:
            return bool(await client.exists(self._key(session_id)))
        except Exception:
            return False
    
    async def clear_expired(self) -> int:
        # Redis gère automatiquement l'expiration via TTL
        return 0