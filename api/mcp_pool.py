"""mcp_pool module provides a persistent connection pool for MCP servers, handling initialization, calls with configurable timeout, automatic reconnection with exponential backoff, and graceful shutdown."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _utf8_params(script_name: str) -> StdioServerParameters:
    """Create StdioServerParameters with forced UTF-8 encoding.

    The MCP subprocess may inherit a non‑UTF‑8 locale on Windows; this
    function ensures the environment forces UTF‑8 for reliable I/O.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / script_name)],
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )

_SCRIPT_DIR = str(Path(__file__).parent)

MCP_SERVERS = {
    "hub": _utf8_params("mcp_server_sage.py"),
    "nl2sql": _utf8_params("mcp_nl2sql.py"),
    "actions": _utf8_params("mcp_actions_sage.py"),
    "kb": _utf8_params("mcp_knowledge_base.py"),
}

MCP_POOL_INIT_TIMEOUT = float(os.getenv("MCP_POOL_INIT_TIMEOUT", "30"))
MCP_CALL_TIMEOUT = float(os.getenv("MCP_CALL_TIMEOUT", "60"))

logger = logging.getLogger("sage.erp.mcp_pool")

_RECONNECT_BASE_DELAY = 1.0
_RECONNECT_MAX_DELAY = 16.0
_RECONNECT_MAX_TRIES = 3


class _PersistentConnection:
    """Encapsulates a persistent MCP connection with its stdio context and session."""

    def __init__(self):
        """Initialize internal placeholders for stdio and session contexts."""
        self._stdio_ctx = None
        self._session_ctx = None
        self.session: ClientSession | None = None

    async def connect(self, params: StdioServerParameters, timeout: float):
        """Open the stdio transport and MCP session, then perform the handshake."""
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()

        self._session_ctx = ClientSession(read, write)
        self.session = await self._session_ctx.__aenter__()

        await asyncio.wait_for(self.session.initialize(), timeout=timeout)

    async def close(self):
        """Close the session and transport cleanly, ignoring any errors."""
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
    """Pool of persistent MCP sessions.

    Each server is started once and kept open. An asyncio lock per server
    guarantees that only one call is in progress at a time because MCP stdio
    servers are single‑threaded.
    """

    def __init__(self):
        """Create dictionaries for connections, per‑server locks, and reconnection state."""
        self._conns: dict[str, _PersistentConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {
            name: asyncio.Lock() for name in MCP_SERVERS
        }
        self._ready = False
        self._reconnect_attempts: dict[str, int] = {name: 0 for name in MCP_SERVERS}

    async def init(self):
        """Initialize all sessions in parallel; should be called once at program start."""
        if self._ready:
            return

        logger.info("Initialisation des connexions MCP persistantes")
        logger.info("Python : %s", sys.executable)

        results = await asyncio.gather(
            *[self._connect_one(name, params) for name, params in MCP_SERVERS.items()],
            return_exceptions=True,
        )

        for name, result in zip(MCP_SERVERS.keys(), results):
            if isinstance(result, Exception):
                logger.warning("%s échec connexion : %r — fallback subprocess activé", name, result)

        self._ready = True
        connected = len(self._conns)
        logger.info("%s/%s serveurs connectés", connected, len(MCP_SERVERS))

    async def _connect_one(self, name: str, params: StdioServerParameters):
        """Launch and initialize a persistent connection for a given server."""
        conn = _PersistentConnection()
        try:
            await conn.connect(params, timeout=MCP_POOL_INIT_TIMEOUT)
            self._conns[name] = conn
            self._reconnect_attempts[name] = 0
            logger.info("%s connecté", name)
        except Exception as e:
            await conn.close()
            raise e

    _WRITE_TOOL_NAMES = {
        "generer_document_sage",
        "workflow_bl",
        "workflow_of",
        "workflow_bf",
        "workflow_bl_achat",
        "workflow_fa_achat",
        "transformer_document",
        "creer_nouveau_client",
        "creer_nouveau_fournisseur",
        "creer_nouvel_article",
        "enregistrer_reglement_facture",
        "ajuster_stock",
        "creer_ligne_nomenclature",
        "modifier_client",
        "modifier_fournisseur",
        "modifier_article",
    }

    async def call(self, server: str, tool: str, arguments: dict | None = None) -> Any:
        """Call a tool on an MCP server with configurable timeout and reconnection handling.

        If a persistent session exists, it is used; otherwise a temporary subprocess
        fallback is created. Write‑type tools are never automatically retried after
        a failure to avoid duplicate side effects.
        """
        args = arguments or {}
        is_write = tool in self._WRITE_TOOL_NAMES

        if server in self._conns:
            async with self._locks[server]:
                try:
                    logger.debug(
                        "MCPPool: calling %s.%s with args=%s (persistent session)",
                        server,
                        tool,
                        args,
                    )
                    res = await asyncio.wait_for(
                        self._conns[server].session.call_tool(tool, arguments=args),
                        timeout=MCP_CALL_TIMEOUT,
                    )
                    self._reconnect_attempts[server] = 0
                    contents = getattr(res, "content", None) or []
                    if not contents:
                        raise RuntimeError(f"{server}.{tool} returned an empty MCP response")
                    first = contents[0]
                    text = getattr(first, "text", None)
                    if text is None:
                        raise RuntimeError(f"{server}.{tool} returned a non-text MCP response: {first!r}")
                    if getattr(res, "isError", False):
                        raise RuntimeError(f"{server}.{tool} a renvoyé une erreur : {text}")
                    return text
                except asyncio.TimeoutError:
                    logger.warning("%s.%s timeout (%ss) → reconnexion", server, tool, MCP_CALL_TIMEOUT)
                    await self._reconnect(server)
                except asyncio.CancelledError:
                    logger.warning(
                        "%s.%s annulé par timeout externe → reconnexion planifiée en arrière-plan",
                        server,
                        tool,
                    )
                    asyncio.create_task(self._reconnect_safe(server))
                    raise
                except Exception as e:
                    logger.warning("Session %s erreur : %s → reconnexion", server, e)
                    await self._reconnect(server)

                if is_write:
                    raise RuntimeError(
                        f"[MCPPool] {server}.{tool} a échoué (erreur réseau/session). "
                        f"Pour les outils d'écriture, aucune nouvelle tentative automatique n'est faite "
                        f"afin d'éviter les doubles créations. Vérifiez si l'opération a abouti avant de relancer."
                    )

                if server in self._conns:
                    try:
                        logger.debug(
                            "MCPPool: retry calling %s.%s with args=%s (after reconnect)",
                            server,
                            tool,
                            args,
                        )
                        res = await asyncio.wait_for(
                            self._conns[server].session.call_tool(tool, arguments=args),
                            timeout=MCP_CALL_TIMEOUT,
                        )
                        if getattr(res, "isError", False):
                            raise RuntimeError(f"{server}.{tool} a renvoyé une erreur : {res.content[0].text}")
                        return res.content[0].text
                    except Exception as e2:
                        raise RuntimeError(f"[MCPPool] {server}.{tool} failed after reconnect: {e2}") from e2

        logger.info("Fallback subprocess pour %s.%s", server, tool)
        params = MCP_SERVERS.get(server)
        if params is None:
            raise ValueError(f"[MCPPool] Serveur inconnu : '{server}'")

        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                logger.debug(
                    "MCPPool: fallback subprocess call %s.%s with args=%s",
                    server,
                    tool,
                    args,
                )
                res = await asyncio.wait_for(
                    s.call_tool(tool, arguments=args),
                    timeout=MCP_CALL_TIMEOUT,
                )
                if getattr(res, "isError", False):
                    raise RuntimeError(f"{server}.{tool} a renvoyé une erreur : {res.content[0].text}")
                return res.content[0].text

    async def _reconnect(self, name: str):
        """Reconnect a server after an error using exponential backoff."""
        old = self._conns.pop(name, None)
        if old:
            await old.close()

        attempt = self._reconnect_attempts.get(name, 0)
        delay = min(_RECONNECT_BASE_DELAY * (2 ** attempt), _RECONNECT_MAX_DELAY)
        self._reconnect_attempts[name] = attempt + 1

        if attempt > 0:
            logger.info("%s attente %.1fs avant reconnexion (essai %s)", name, delay, attempt + 1)
            await asyncio.sleep(delay)

        params = MCP_SERVERS.get(name)
        if not params:
            return

        try:
            await self._connect_one(name, params)
            logger.info("%s reconnecté avec succès", name)
        except Exception as e:
            logger.exception("%s reconnexion impossible", name)

    async def _reconnect_safe(self, name: str):
        """Protected reconnection used in background after a CancelledError, with a global timeout."""
        try:
            await asyncio.wait_for(self._reconnect(name), timeout=45.0)
        except asyncio.TimeoutError:
            logger.error(
                "%s reconnexion en arrière-plan bloquée >45s → le sous-processus est probablement zombie, abandon."
                " Le prochain appel retentera une reconnexion.",
                name,
            )
            self._conns.pop(name, None)

    async def close(self):
        """Close all sessions cleanly; should be called at program termination."""
        logger.info("Fermeture de toutes les connexions")
        await asyncio.gather(
            *[conn.close() for conn in self._conns.values()],
            return_exceptions=True,
        )
        self._conns.clear()
        self._ready = False
        logger.info("Toutes les connexions fermées")


pool = MCPPool()