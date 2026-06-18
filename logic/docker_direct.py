"""Direct-Docker backend — the Docker Engine API over an SSH tunnel.

Lets OmniGrid read + manage a node that runs **plain Docker with no Portainer**
(standalone Docker, reached over SSH). The existing fleet is reached via
Portainer (``logic/portainer.py`` proxies ``/api/endpoints/{eid}/docker/<X>`` to
the daemon); this module reaches a Portainer-less node by opening an **SSH
channel to the node's ``/var/run/docker.sock``** and speaking the Docker Engine
API straight over it — same JSON shapes Portainer proxies, just a different
transport. No daemon TLS, no exposed :2376, no client certs: it reuses the SSH
credentials OmniGrid already has (global ``ssh_default_*`` settings + an optional
per-node ``ssh`` override), so one credential gives both the node's SSH console
AND its Docker API.

Why SSH-tunnel-to-the-socket (not TCP+TLS): the daemon's UNIX socket is the
universal, always-present endpoint; SSH is the credential the operator already
manages; and ``asyncssh`` (already a dependency for the SSH console) can open a
``direct-streamlocal`` channel to a remote UNIX socket. A small self-contained
HTTP/1.1 client speaks the (tiny, single-shot) Docker API surface over that
channel — no streaming, no new dependency.

Connection model: ``connect(node)`` is an async context manager that opens ONE
SSH connection (the handshake cost is paid once per gather / op) and yields a
``DockerClient``; each Docker API call opens a fresh, cheap UNIX-domain channel
on that connection. Callers do all their requests inside one ``async with``.

Auth-failure backoff reuses the shared SSH ``Cooldown`` (keyed ``docker:<id>`` +
user) so a bad credential backs off across both the SSH console and this client.

Public surface:
    async connect(node, *, timeout=None) -> DockerClient    (context manager)
    DockerClient.get(path) / .post(path, body) / .delete(path)
        -> (status:int, parsed_json|None, body_snippet:str)
    async probe(node, *, timeout=None) -> {ok, detail, status, version}

``node`` is one ``docker_nodes`` setting entry:
    {id, label, address, socket_path?, ssh: {user?, port?, password?}, enabled}
SSH key material stays GLOBAL (``ssh_default_*``), mirroring ``logic/ssh.py``;
the per-node ``ssh`` block may override user / port / password.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncssh

from logic import ssh as _ssh
from logic import tuning as _tuning
from logic.coerce import safe_int
from logic.tuning import Tunable as _Tunable

# Default Docker daemon UNIX socket on the node.
_DEFAULT_SOCKET = "/var/run/docker.sock"


class DockerDirectError(Exception):
    """A clean, operator-facing failure (connect / auth / socket / HTTP) — the
    caller maps it to a card error / op failure without a traceback."""


def _timeout() -> float:
    """Per-call wall-clock budget (connect + channel + one HTTP request)."""
    return float(_tuning.tuning_int(_Tunable.DOCKER_DIRECT_TIMEOUT_SECONDS))


def _resolve_node_conn(node: dict) -> dict:
    """Merge a ``docker_nodes`` entry's ``ssh`` override over the global
    ``ssh_default_*`` settings into one connect spec. Key material stays global
    (matching ``logic/ssh.py``); the per-node block may override user / port /
    password. Returns ``{host, user, port, password, private_key, passphrase,
    known_hosts, socket_path}``."""
    g = _ssh.get_global_ssh_settings()
    node = node if isinstance(node, dict) else {}
    _sub = node.get("ssh")
    sub = _sub if isinstance(_sub, dict) else {}
    host = str(node.get("address") or sub.get("host") or "").strip()
    user = str(sub.get("user") or g.get("user") or "root").strip() or "root"
    port = safe_int(sub.get("port")) or safe_int(g.get("port")) or 22
    password = str(sub.get("password") or g.get("password") or "")
    socket_path = str(node.get("socket_path") or _DEFAULT_SOCKET).strip() or _DEFAULT_SOCKET
    return {
        "host": host, "user": user, "port": port, "password": password,
        "private_key": g.get("private_key") or "",
        "passphrase": g.get("passphrase") or "",
        "known_hosts": g.get("known_hosts") or "",
        "socket_path": socket_path,
    }


def _cooldown_key(node: dict, conn: dict) -> "tuple[str, str]":
    """Per-(node, user) auth-cooldown key, namespaced ``docker:`` so it can't
    collide with the curated-host SSH console's ``(host_id, user)`` keys."""
    nid = str((node or {}).get("id") or conn.get("host") or "")
    return f"docker:{nid}", conn.get("user") or ""


def _parse_response(raw: bytes) -> "tuple[int, bytes]":
    """Parse a raw HTTP/1.1 response → ``(status, body_bytes)``. De-chunks a
    ``Transfer-Encoding: chunked`` body; otherwise the body is taken as-is
    (Content-Length or connection-close delimited — we read to EOF so both give
    the full body). Status 0 on an unparseable head."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines or not lines[0]:
        return 0, b""
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        status = 0
    chunked = False
    for h in lines[1:]:
        k, _, v = h.partition(b":")
        if k.strip().lower() == b"transfer-encoding" and b"chunked" in v.strip().lower():
            chunked = True
            break
    if chunked:
        body = _dechunk(body)
    return status, body


def _dechunk(data: bytes) -> bytes:
    """De-chunk an HTTP/1.1 chunked body. Stops at the 0-length terminator or
    a malformed size line (best-effort — returns what it decoded)."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        nl = data.find(b"\r\n", i)
        if nl == -1:
            break
        size_token = data[i:nl].split(b";", 1)[0].strip()
        try:
            size = int(size_token, 16)
        except ValueError:
            break
        if size == 0:
            break
        start = nl + 2
        out += data[start:start + size]
        i = start + size + 2  # skip the chunk data + its trailing CRLF
    return bytes(out)


class DockerClient:
    """Thin Docker Engine API client bound to one open SSH connection. Each call
    opens a fresh UNIX-domain channel to the daemon socket (channels are cheap;
    the SSH handshake is reused). Methods return ``(status, parsed_json|None,
    body_snippet)``."""

    def __init__(self, conn: "asyncssh.SSHClientConnection", socket_path: str, timeout: float):
        self._conn = conn
        self._sock = socket_path
        self._to = timeout

    async def request(self, method: str, path: str,
                      body: Optional[Any] = None) -> "tuple[int, Any, str]":
        """One Docker API request over a fresh channel, bounded by the per-call
        timeout. Raises ``DockerDirectError`` on a transport failure."""
        return await asyncio.wait_for(self._request(method, path, body), timeout=self._to)

    async def _request(self, method: str, path: str, body: Optional[Any]) -> "tuple[int, Any, str]":
        try:
            reader, writer = await self._conn.open_unix_connection(self._sock, encoding=None)
        except (asyncssh.Error, OSError) as e:  # noqa: BLE001
            raise DockerDirectError(
                f"couldn't open the Docker socket {self._sock} over SSH "
                f"({type(e).__name__}: {e}) — is Docker running and is the path right?")
        raw: Any = b""
        try:
            payload = b""
            req = (f"{method} {path} HTTP/1.1\r\nHost: docker\r\n"
                   f"Accept: application/json\r\nConnection: close\r\n")
            if body is not None:
                payload = json.dumps(body).encode()
                req += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
            req += "\r\n"
            writer.write(req.encode() + payload)
            writer.write_eof()
            raw = await reader.read()  # read to EOF (Connection: close)
        finally:
            try:
                writer.close()
            except (asyncssh.Error, OSError):  # best-effort cleanup
                pass
        if not isinstance(raw, (bytes, bytearray)):
            raw = str(raw).encode(errors="replace")
        status, body_bytes = _parse_response(bytes(raw))
        parsed: Any = None
        if body_bytes:
            try:
                parsed = json.loads(body_bytes)
            except (ValueError, TypeError):
                parsed = None
        snippet = body_bytes[:300].decode(errors="replace")
        return status, parsed, snippet

    async def get(self, path: str) -> "tuple[int, Any, str]":
        """GET a Docker endpoint — the ``portainer.pg`` analogue."""
        return await self.request("GET", path)

    async def post(self, path: str, body: Optional[Any] = None) -> "tuple[int, Any, str]":
        """POST a Docker endpoint (optional JSON body)."""
        return await self.request("POST", path, body)

    async def delete(self, path: str) -> "tuple[int, Any, str]":
        """DELETE a Docker endpoint."""
        return await self.request("DELETE", path)


@asynccontextmanager
async def connect(node: dict, *, timeout: Optional[float] = None):
    """Open ONE SSH connection to a ``docker_nodes`` entry and yield a
    ``DockerClient``. Resolves creds (per-node ``ssh`` over global defaults),
    honours the shared SSH auth-cooldown, and closes the connection on exit.
    Raises ``DockerDirectError`` on misconfig / auth / connect failure (auth
    failures arm the cooldown)."""
    conn_spec = _resolve_node_conn(node)
    if not conn_spec["host"]:
        raise DockerDirectError("no address configured for this Docker node")
    if not conn_spec["private_key"] and not conn_spec["password"]:
        raise DockerDirectError(
            "no SSH credentials — set a global SSH key/password in Admin → SSH, "
            "or a password on this Docker node")
    cd_key = _cooldown_key(node, conn_spec)
    remaining = _ssh.auth_cooldown_timer.remaining(*cd_key)
    if remaining:
        raise DockerDirectError(
            f"SSH auth cool-down ({int(remaining)}s remaining) — fix the "
            f"credentials and wait before retrying")
    to = float(timeout if timeout is not None else _timeout())

    client_keys: Any = None
    if conn_spec["private_key"]:
        try:
            client_keys = [asyncssh.import_private_key(
                conn_spec["private_key"], passphrase=conn_spec["passphrase"] or None)]
        except (asyncssh.Error, ValueError, TypeError):
            if not conn_spec["password"]:
                raise DockerDirectError("the global SSH private key couldn't be parsed")
            client_keys = None
    known_hosts: Any = None
    if conn_spec["known_hosts"]:
        try:
            known_hosts = asyncssh.import_known_hosts(conn_spec["known_hosts"])
        except (asyncssh.Error, ValueError, TypeError):
            known_hosts = None
    preferred: list[str] = []
    if client_keys:
        preferred.append("publickey")
    if conn_spec["password"]:
        preferred.append("password")

    print(f"[docker] connect node={(node or {}).get('id')!r} "
          f"target={conn_spec['user']}@{conn_spec['host']}:{conn_spec['port']} "
          f"socket={conn_spec['socket_path']} auth={preferred}")
    try:
        conn = await asyncio.wait_for(asyncssh.connect(
            host=conn_spec["host"], port=conn_spec["port"], username=conn_spec["user"],
            client_keys=client_keys, known_hosts=known_hosts, agent_path=None,
            password=conn_spec["password"] or None,
            preferred_auth=",".join(preferred) or "publickey,password",
            connect_timeout=max(5.0, min(to, 30.0)),
            login_timeout=max(5.0, min(to, 30.0)),
        ), timeout=to)
    except asyncssh.PermissionDenied as e:
        _ssh.auth_cooldown_timer.arm(*cd_key)
        raise DockerDirectError(
            f"SSH auth failed for {conn_spec['user']}@{conn_spec['host']} "
            f"(check the credentials) — {type(e).__name__}")
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as e:  # noqa: BLE001
        raise DockerDirectError(
            f"SSH connect failed for {conn_spec['user']}@{conn_spec['host']}:"
            f"{conn_spec['port']}: {type(e).__name__}: {e}")
    try:
        yield DockerClient(conn, conn_spec["socket_path"], to)
    finally:
        conn.close()


async def probe(node: dict, *, timeout: Optional[float] = None) -> dict:
    """Connectivity probe for the Test-connection button: open the tunnel + GET
    ``/version``. Returns ``{ok, detail, status, version}`` — never raises."""
    try:
        async with connect(node, timeout=timeout) as cli:
            status, data, snippet = await cli.get("/version")
    except DockerDirectError as e:
        return {"ok": False, "detail": str(e), "status": 0, "version": ""}
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0, "version": ""}
    if status == 200 and isinstance(data, dict):
        ver = str(data.get("Version") or "").strip()
        api = str(data.get("ApiVersion") or "").strip()
        detail = "OK"
        if ver:
            detail = f"OK — Docker {ver}" + (f" (API {api})" if api else "")
        return {"ok": True, "detail": detail, "status": 200, "version": ver}
    if status in (401, 403):
        return {"ok": False, "detail": "Docker API rejected the request (auth)",
                "status": status, "version": ""}
    return {"ok": False,
            "detail": f"HTTP {status or '?'} from /version: {snippet[:120]}",
            "status": status, "version": ""}
