"""
mcp_pool.py — Pool de Connexions MCP Persistantes (v3 — optimisé)
==================================================
Améliorations v3 :
  - Reconnexion avec backoff exponentiel (évite les rafales d'erreurs)
  - Timeout d'appel configurable par variable d'env MCP_CALL_TIMEOUT
  - Lock libéré immédiatement en cas d'erreur (pas de blocage prolongé)
  - Init parallèle conservée (déjà présente en v2)
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ─────────────────────────────────────────────────────────────────────
# Paramètres des serveurs MCP
# ─────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = str(Path(__file__).parent)

MCP_SERVERS = {
    "hub":     StdioServerParameters(
                    command=sys.executable,
                    args=[str(Path(__file__).parent / "mcp_server_sage.py")]
               ),
    "nl2sql":  StdioServerParameters(
                    command=sys.executable,
                    args=[str(Path(__file__).parent / "mcp_nl2sql.py")]
               ),
    "actions": StdioServerParameters(
                    command=sys.executable,
                    args=[str(Path(__file__).parent / "mcp_actions_sage.py")]
               ),
    "kb":      StdioServerParameters(
                    command=sys.executable,
                    args=[str(Path(__file__).parent / "mcp_knowledge_base.py")]
               ),
}

MCP_POOL_INIT_TIMEOUT = float(os.getenv("MCP_POOL_INIT_TIMEOUT", "30"))
MCP_CALL_TIMEOUT      = float(os.getenv("MCP_CALL_TIMEOUT",      "60"))

# Backoff exponentiel pour les reconnexions
_RECONNECT_BASE_DELAY = 1.0   # secondes
_RECONNECT_MAX_DELAY  = 16.0
_RECONNECT_MAX_TRIES  = 3


class _PersistentConnection:
    """
    Encapsule une connexion MCP persistante avec son contexte stdio
    et sa session ClientSession, tous deux maintenus ouverts.
    """
    def __init__(self):
        self._stdio_ctx  = None
        self._session_ctx = None
        self.session: ClientSession | None = None

    async def connect(self, params: StdioServerParameters, timeout: float):
        """Ouvre la connexion stdio + session MCP et effectue le handshake."""
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()

        self._session_ctx = ClientSession(read, write)
        self.session = await self._session_ctx.__aenter__()

        await asyncio.wait_for(self.session.initialize(), timeout=timeout)

    async def close(self):
        """Ferme proprement session puis transport."""
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_ctx = None

        if self._stdio_ctx is not None:
            try:
                await self._stdio_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_ctx = None

        self.session = None


class MCPPool:
    """
    Pool de sessions MCP persistantes.

    Chaque serveur est lancé une fois et conservé ouvert.
    Un verrou asyncio par serveur garantit qu'un seul appel
    est en cours à la fois (les serveurs MCP stdio sont mono-thread).
    """

    def __init__(self):
        self._conns:  dict[str, _PersistentConnection] = {}
        self._locks:  dict[str, asyncio.Lock] = {
            name: asyncio.Lock() for name in MCP_SERVERS
        }
        self._ready = False
        self._reconnect_attempts: dict[str, int] = {name: 0 for name in MCP_SERVERS}

    # ──────────────────────────────────────────────────────────────────
    # Initialisation
    # ──────────────────────────────────────────────────────────────────
    async def init(self):
        """
        Initialise toutes les sessions en parallèle au démarrage.
        Appeler UNE SEULE FOIS dans main() avant la boucle interactive.
        """
        if self._ready:
            return

        print("🔌 [MCPPool] Initialisation des connexions MCP persistantes...")
        print(f"   Python : {sys.executable}")

        results = await asyncio.gather(
            *[self._connect_one(name, params)
              for name, params in MCP_SERVERS.items()],
            return_exceptions=True,
        )

        for name, result in zip(MCP_SERVERS.keys(), results):
            if isinstance(result, Exception):
                print(f"   ⚠️  [{name}] échec connexion : {result!r} "
                      f"— fallback subprocess activé")

        self._ready = True
        connected = len(self._conns)
        print(f"🔌 [MCPPool] {connected}/{len(MCP_SERVERS)} serveurs connectés.\n")

    async def _connect_one(self, name: str, params: StdioServerParameters):
        """Lance et initialise une connexion persistante pour un serveur."""
        conn = _PersistentConnection()
        try:
            await conn.connect(params, timeout=MCP_POOL_INIT_TIMEOUT)
            self._conns[name] = conn
            self._reconnect_attempts[name] = 0
            print(f"   ✅ [{name}] connecté")
        except Exception as e:
            await conn.close()
            raise e

    # ──────────────────────────────────────────────────────────────────
    # Appel d'outil
    # ──────────────────────────────────────────────────────────────────
    async def call(
        self,
        server: str,
        tool: str,
        arguments: dict | None = None,
    ) -> Any:
        """
        Appelle un outil sur un serveur MCP avec timeout configurable.

        Si la session persistante est disponible, l'utilise directement.
        Sinon, crée une connexion éphémère (fallback automatique).

        Returns:
            Texte de la première réponse content[0].text
        """
        args = arguments or {}

        # ── Chemin rapide : session persistante ──────────────────────
        if server in self._conns:
            async with self._locks[server]:
                try:
                    res = await asyncio.wait_for(
                        self._conns[server].session.call_tool(tool, arguments=args),
                        timeout=MCP_CALL_TIMEOUT,
                    )
                    self._reconnect_attempts[server] = 0  # reset sur succès
                    return res.content[0].text
                except asyncio.TimeoutError:
                    print(f"   ⚠️  [MCPPool] '{server}.{tool}' timeout ({MCP_CALL_TIMEOUT}s) → reconnexion")
                    await self._reconnect(server)
                except Exception as e:
                    print(f"   ⚠️  [MCPPool] Session '{server}' erreur : {e} → reconnexion")
                    await self._reconnect(server)

                # Nouvelle tentative après reconnexion
                if server in self._conns:
                    try:
                        res = await asyncio.wait_for(
                            self._conns[server].session.call_tool(tool, arguments=args),
                            timeout=MCP_CALL_TIMEOUT,
                        )
                        return res.content[0].text
                    except Exception as e2:
                        raise RuntimeError(
                            f"[MCPPool] {server}.{tool} failed after reconnect: {e2}"
                        ) from e2

        # ── Fallback : subprocess éphémère ───────────────────────────
        print(f"   🔄 [MCPPool] Fallback subprocess pour '{server}.{tool}'")
        params = MCP_SERVERS.get(server)
        if params is None:
            raise ValueError(f"[MCPPool] Serveur inconnu : '{server}'")

        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await asyncio.wait_for(
                    s.call_tool(tool, arguments=args),
                    timeout=MCP_CALL_TIMEOUT,
                )
                return res.content[0].text

    # ──────────────────────────────────────────────────────────────────
    # Reconnexion avec backoff exponentiel
    # ──────────────────────────────────────────────────────────────────
    async def _reconnect(self, name: str):
        """Reconnecte un serveur après une erreur avec backoff exponentiel."""
        old = self._conns.pop(name, None)
        if old:
            await old.close()

        attempt = self._reconnect_attempts.get(name, 0)
        delay = min(_RECONNECT_BASE_DELAY * (2 ** attempt), _RECONNECT_MAX_DELAY)
        self._reconnect_attempts[name] = attempt + 1

        if attempt > 0:
            print(f"   ⏳ [MCPPool] '{name}' attente {delay:.1f}s avant reconnexion (essai {attempt+1})...")
            await asyncio.sleep(delay)

        params = MCP_SERVERS.get(name)
        if not params:
            return

        try:
            await self._connect_one(name, params)
            print(f"   🔄 [MCPPool] '{name}' reconnecté avec succès.")
        except Exception as e:
            print(f"   ❌ [MCPPool] '{name}' reconnexion impossible : {e}")

    # ──────────────────────────────────────────────────────────────────
    # Fermeture
    # ──────────────────────────────────────────────────────────────────
    async def close(self):
        """Ferme proprement toutes les sessions (appeler à la fin du programme)."""
        print("🔌 [MCPPool] Fermeture de toutes les connexions...")
        await asyncio.gather(
            *[conn.close() for conn in self._conns.values()],
            return_exceptions=True,
        )
        self._conns.clear()
        self._ready = False
        print("🔌 [MCPPool] Toutes les connexions fermées.")


# ─────────────────────────────────────────────────────────────────────
# Instance globale unique (singleton)
# ─────────────────────────────────────────────────────────────────────
pool = MCPPool()
