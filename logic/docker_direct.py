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
from typing import Any, Optional, Union

import asyncssh
import httpx

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


def _cooldown_key(node_id: str, conn: dict) -> "tuple[str, str]":
    """Per-(node, user) auth-cooldown key, namespaced ``docker:`` so it can't
    collide with the curated-host SSH console's ``(host_id, user)`` keys."""
    nid = str(node_id or conn.get("host") or "")
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
            # A ChannelOpenError ("open failed") here means the SSH SERVER
            # refused to forward to the UNIX socket — the channel never
            # reached Docker. The usual cause is NOT "Docker is down" (the
            # socket exists); it's the sshd config or socket permissions, so
            # lead with those.
            raise DockerDirectError(
                f"couldn't open the Docker socket {self._sock} over SSH "
                f"({type(e).__name__}: {e}) — the SSH server refused the socket "
                f"forward. Check, in order: (1) sshd allows the forward — it needs "
                f"BOTH 'AllowStreamLocalForwarding yes' AND 'AllowTcpForwarding' "
                f"NOT set to 'no' (OpenSSH gates the socket forward behind the "
                f"general port-forwarding permission, so 'AllowTcpForwarding no' — "
                f"common on hardened/NAS builds — blocks it even with "
                f"AllowStreamLocalForwarding on; use 'AllowTcpForwarding local'), "
                f"then reload sshd; (2) the SSH user can access the socket (be "
                f"root, or in the 'docker' group); (3) Docker is running and the "
                f"socket path is correct. Verify with: sshd -T | grep -i forwarding")
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
            # NOTE: do NOT write_eof() here. HTTP request framing doesn't need an
            # EOF (no body, or a Content-Length), and half-closing our write side
            # makes Docker's Go HTTP server see the connection's read EOF and
            # CANCEL the request context — the daemon then returns
            # `500 {"message":"context canceled"}` even for an instant call like
            # /version. `Connection: close` already makes the daemon close after
            # responding, so `reader.read()` still reads to a clean EOF.
            raw = await reader.read()  # read to EOF (server closes on Connection: close)
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

    async def exec_command(self, command: str) -> "tuple[int, str, str]":
        """Run a shell command on the node over the SAME SSH connection (NOT the
        Docker socket — used for ``docker compose``, which the Engine API can't
        do). Returns ``(exit_status, stdout, stderr)``, bounded by the per-call
        timeout (so a compose op must open ``connect`` with a long timeout — the
        pull can take minutes). Raises ``DockerDirectError`` on a transport
        failure."""
        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=False), timeout=self._to)
        except asyncio.TimeoutError:
            # TimeoutError ⊂ OSError on 3.11+, so it MUST precede the OSError
            # clause below or it's unreachable.
            raise DockerDirectError(
                f"SSH command exceeded the {int(self._to)}s budget")
        except (asyncssh.Error, OSError) as e:
            raise DockerDirectError(f"SSH exec failed: {type(e).__name__}: {e}")
        out = result.stdout if isinstance(result.stdout, str) else str(result.stdout or "")
        err = result.stderr if isinstance(result.stderr, str) else str(result.stderr or "")
        code = result.exit_status if isinstance(result.exit_status, int) else -1
        return code, out, err

    async def get(self, path: str) -> "tuple[int, Any, str]":
        """GET a Docker endpoint — the ``portainer.pg`` analogue."""
        return await self.request("GET", path)

    async def post(self, path: str, body: Optional[Any] = None) -> "tuple[int, Any, str]":
        """POST a Docker endpoint (optional JSON body)."""
        return await self.request("POST", path, body)

    async def delete(self, path: str) -> "tuple[int, Any, str]":
        """DELETE a Docker endpoint."""
        return await self.request("DELETE", path)


def node_transport(node: dict) -> str:
    """The node's transport — ``"tls"`` (TCP+TLS to the daemon) or ``"ssh"``
    (the default; the Docker API over an SSH channel to the UNIX socket)."""
    return "tls" if str((node or {}).get("transport") or "ssh").strip().lower() == "tls" else "ssh"


class TLSDockerClient:
    """Docker Engine API client over a direct TCP+TLS connection to the daemon
    (``https://host:port``). Same ``(status, parsed_json|None, snippet)`` return
    shape as :class:`DockerClient`, so gather / stats / container ops are
    transport-agnostic. Has NO ``exec_command`` — there's no shell channel over
    the daemon socket, so compose-update (which needs ``docker compose``) is
    SSH-only."""

    def __init__(self, client: "httpx.AsyncClient", base: str, timeout: float):
        self._client = client
        self._base = base
        self._to = timeout

    async def request(self, method: str, path: str,
                      body: Optional[Any] = None) -> "tuple[int, Any, str]":
        try:
            r = await self._client.request(method, self._base + path, json=body,
                                           timeout=self._to)
        except (httpx.HTTPError, OSError) as e:
            raise DockerDirectError(
                f"TLS request to {self._base} failed ({type(e).__name__}: {e}) "
                f"— is the daemon listening on TLS and are the certs right?")
        parsed: Any = None
        try:
            parsed = r.json()
        except (ValueError, TypeError):
            parsed = None
        snippet = (r.text or "")[:300]
        return r.status_code, parsed, snippet

    async def get(self, path: str) -> "tuple[int, Any, str]":
        return await self.request("GET", path)

    async def post(self, path: str, body: Optional[Any] = None) -> "tuple[int, Any, str]":
        return await self.request("POST", path, body)

    async def delete(self, path: str) -> "tuple[int, Any, str]":
        return await self.request("DELETE", path)


# Either client `connect()` may yield, depending on the node's transport. Both
# share the `.get` / `.post` / `.delete` surface (only the SSH `DockerClient`
# additionally has `.exec_command`), so callers that only do API requests accept
# this union.
AnyDockerClient = Union[DockerClient, TLSDockerClient]


def _build_tls_context(node: dict) -> "tuple[Any, list[str]]":
    """Build an ``ssl.SSLContext`` for a TLS docker node from its PEM material
    (``tls_ca`` / ``tls_cert`` / ``tls_key``). The CA is loaded in-memory; the
    client cert chain is written to 0600 temp files (``load_cert_chain`` needs
    paths) which the caller deletes after connecting. No CA ⇒ verify off (the
    homelab ``VERIFY_TLS=false`` pattern). ``check_hostname`` is off because a
    daemon cert rarely matches the host / IP. Returns ``(context, tempfiles)``."""
    import os  # noqa: PLC0415
    import ssl  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    ca = str(node.get("tls_ca") or "").strip()
    cert = str(node.get("tls_cert") or "").strip()
    key = str(node.get("tls_key") or "").strip()
    if ca:
        ctx = ssl.create_default_context(cadata=ca)
        ctx.check_hostname = False
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    tmpfiles: list[str] = []
    if cert and key:
        cf = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        cf.write(cert)
        cf.close()
        kf = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        kf.write(key)
        kf.close()
        try:
            os.chmod(kf.name, 0o600)
        except OSError:
            pass
        tmpfiles = [cf.name, kf.name]
        ctx.load_cert_chain(cf.name, kf.name)
    return ctx, tmpfiles


@asynccontextmanager
async def connect_tls(node: dict, *, timeout: Optional[float] = None):
    """Open a TCP+TLS connection to a docker node's daemon (``https://address:
    tls_port``, default 2376) and yield a :class:`TLSDockerClient`. Raises
    ``DockerDirectError`` on misconfig / cert / connect failure."""
    import os  # noqa: PLC0415
    import ssl  # noqa: PLC0415
    host = str(node.get("address") or "").strip()
    if not host:
        raise DockerDirectError("no address configured for this Docker node")
    port = safe_int(node.get("tls_port")) or 2376
    to = float(timeout if timeout is not None else _timeout())
    try:
        ctx, tmpfiles = _build_tls_context(node)
    except (ssl.SSLError, ValueError, OSError) as e:
        raise DockerDirectError(
            f"TLS cert / key / CA couldn't be loaded ({type(e).__name__}: {e})")
    base = f"https://{host}:{port}"
    print(f"[docker] connect-tls node={(node or {}).get('id')!r} target={base} "
          f"verify={'ca' if node.get('tls_ca') else 'off'} "
          f"client_cert={'yes' if tmpfiles else 'no'}")
    client = httpx.AsyncClient(verify=ctx, timeout=to)
    try:
        yield TLSDockerClient(client, base, to)
    finally:
        await client.aclose()
        for f in tmpfiles:
            try:
                os.unlink(f)
            except OSError:
                pass


@asynccontextmanager
async def connect(node: dict, *, timeout: Optional[float] = None):
    """Open ONE connection to a ``docker_nodes`` entry and yield a Docker client.
    Dispatches on the node's transport: ``tls`` → a :class:`TLSDockerClient`
    (TCP+TLS to the daemon); otherwise SSH (the default — the Docker API over an
    SSH channel to the UNIX socket, resolving creds via :func:`connect_resolved`
    + the shared auth-cooldown). Raises ``DockerDirectError`` on misconfig / auth
    / connect failure."""
    if node_transport(node) == "tls":
        async with connect_tls(node, timeout=timeout) as cli:
            yield cli
    else:
        async with connect_resolved(
            _resolve_node_conn(node),
            node_id=str((node or {}).get("id") or ""), timeout=timeout) as cli:
            yield cli


@asynccontextmanager
async def connect_resolved(conn_spec: dict, *, node_id: str = "",
                           timeout: Optional[float] = None):
    """Open ONE SSH connection from an ALREADY-RESOLVED connect spec and yield a
    ``DockerClient``. The spec is ``{host, user, port, password, private_key,
    passphrase, known_hosts, socket_path}`` — the same shape
    :func:`_resolve_node_conn` produces, so a caller that resolved creds through
    a DIFFERENT path (e.g. the curated-host SSH ladder via
    ``logic.ssh.resolve_ssh_connect_spec`` for the Portainer-node stats
    fallback) can reuse the whole SSH-channel + cooldown + auth machinery. The
    direct ``connect(node)`` path delegates here. ``node_id`` namespaces the
    auth-cooldown key. Raises ``DockerDirectError`` on misconfig / auth / connect
    failure (auth failures arm the cooldown)."""
    if not conn_spec.get("host"):
        raise DockerDirectError("no address configured for this Docker node")
    conn_spec.setdefault("socket_path", _DEFAULT_SOCKET)
    if not conn_spec.get("private_key") and not conn_spec.get("password"):
        raise DockerDirectError(
            "no SSH credentials — set a global SSH key/password in Admin → SSH, "
            "or a password on this Docker node")
    cd_key = _cooldown_key(node_id, conn_spec)
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

    print(f"[docker] connect node={node_id!r} "
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


async def diagnose(node: dict, *, timeout: Optional[float] = None) -> dict:
    """Staged connectivity diagnostic for a direct-Docker node — powers the
    "Troubleshoot" action on an unreachable Node card. Runs each layer
    INDEPENDENTLY so the operator sees exactly which step fails + the fix for
    it, instead of one opaque error:

      SSH transport:  config → DNS → TCP(ssh port) → SSH auth → Docker socket (/_ping)
      TLS transport:  config → TLS + Docker /_ping (single step)

    Later stages are skipped once one fails (they depend on it). Returns
    ``{ok, transport, target, steps:[{key,label,ok,detail,hint}]}``. Never
    raises — every failure is captured as a step."""
    to = float(timeout if timeout is not None else _timeout())
    transport = node_transport(node)
    steps: list = []

    def _add(key: str, label: str, ok: bool, detail: str = "", hint: str = "") -> None:
        steps.append({"key": key, "label": label, "ok": bool(ok),
                      "detail": detail, "hint": hint})

    # ---- TLS transport: a lighter single-step check (SSH is the common case) ----
    if transport == "tls":
        host = str(node.get("address") or "").strip()
        _add("config", "Configuration", bool(host),
             "TLS address set" if host else "No address configured",
             "" if host else "Set the node's Address (host:port, usually :2376) in Admin → Docker Nodes.")
        if host:
            res = await probe(node, timeout=to)
            _add("tls", "TLS handshake + Docker /_ping", bool(res.get("ok")),
                 str(res.get("detail") or ""),
                 "" if res.get("ok") else
                 "Confirm the daemon is listening on tcp://<host>:2376 with TLS enabled, and the pasted CA + "
                 "client cert/key match the daemon's. A self-signed daemon needs its CA pasted into the node.")
        return {"ok": all(s["ok"] for s in steps) and bool(steps),
                "transport": transport, "target": host, "steps": steps}

    # ---- SSH transport ----
    conn = _resolve_node_conn(node)
    host, port, user = conn["host"], conn["port"], conn["user"]
    sock_path = conn["socket_path"]
    target = f"{user}@{host}:{port}" if host else ""

    def _result() -> dict:
        return {"ok": all(s["ok"] for s in steps) and bool(steps),
                "transport": transport, "target": target, "steps": steps}

    # 1. Configuration
    have_creds = bool(conn["private_key"] or conn["password"])
    _add("config", "Configuration", bool(host) and have_creds,
         (f"{target} · socket {sock_path}" if host else "No address configured")
         + ("" if have_creds else " · no SSH credentials"),
         (("" if host else "Set the node's Address (host/IP) in Admin → Docker Nodes. ")
          + ("" if have_creds else "Set a global SSH key/password in Admin → SSH, or a per-node password.")))
    if not host or not have_creds:
        return _result()

    # 2. DNS resolution
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port), timeout=min(to, 10.0))
        addrs = sorted({str(i[4][0]) for i in infos if i and i[4]})
        _add("dns", "DNS resolution", True, f"{host} → {', '.join(addrs[:4]) or '?'}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        _add("dns", "DNS resolution", False, f"{type(e).__name__}: {e}",
             "The OmniGrid container can't resolve this hostname. Use the FQDN or an IP address, or add a "
             "compose `extra_hosts:` / internal-DNS entry so the container can resolve it.")
        return _result()

    # 3. TCP connect to the SSH port
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=min(to, 15.0))
        w.close()
        try:
            await asyncio.wait_for(w.wait_closed(), timeout=2.0)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:  # noqa: BLE001
            pass
        _add("tcp", f"TCP connect to port {port}", True, f"Port {port} is open")
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        _add("tcp", f"TCP connect to port {port}", False, f"{type(e).__name__}: {e}",
             f"Can't reach {host}:{port}. The host may be offline, the SSH service not running, the port wrong, "
             "or a firewall is blocking the OmniGrid container's network from this host.")
        return _result()

    # 4. SSH authentication
    client_keys: Any = None
    if conn["private_key"]:
        try:
            client_keys = [asyncssh.import_private_key(
                conn["private_key"], passphrase=conn["passphrase"] or None)]
        except (asyncssh.Error, ValueError, TypeError):
            client_keys = None
    ssh_conn = None
    try:
        ssh_conn = await asyncio.wait_for(asyncssh.connect(
            host=host, port=port, username=user, client_keys=client_keys,
            known_hosts=None, agent_path=None, password=conn["password"] or None,
            connect_timeout=max(5.0, min(to, 30.0)), login_timeout=max(5.0, min(to, 30.0)),
        ), timeout=to)
        _add("ssh", "SSH authentication", True, f"Authenticated as {user}")
    except asyncssh.PermissionDenied:
        _add("ssh", "SSH authentication", False, "Permission denied",
             f"SSH rejected the credentials for {user}@{host}. Check the SSH user + the global key/password "
             "(Admin → SSH) or the per-node password override.")
        return _result()
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        _add("ssh", "SSH authentication", False, f"{type(e).__name__}: {e}",
             "The SSH handshake failed (host key, algorithm, or transport). Check the SSH server settings.")
        return _result()

    # 5. Docker socket over SSH — reuse DockerClient.get so this inherits the
    #    full AllowStreamLocalForwarding / socket-perms guidance on refusal.
    try:
        cli = DockerClient(ssh_conn, sock_path, to)
        status, _data, snip = await cli.get("/_ping")
        if status == 200:
            _add("socket", "Docker socket over SSH", True, "Docker API responded (/_ping → 200 OK)")
        else:
            _add("socket", "Docker socket over SSH", False,
                 f"HTTP {status or '?'} from /_ping: {snip[:120]}",
                 f"The socket forward opened but Docker didn't answer OK at {sock_path}. Check the socket path "
                 "and that the Docker daemon is running on the node.")
    except DockerDirectError as e:
        # The socket forward was refused ("open failed"). That has TWO very
        # different causes: (a) SSH forwarding is disabled on the server, or
        # (b) forwarding works but the SOCKET itself is unreachable (the user
        # can't read it, or Docker isn't running). Distinguish them by probing
        # a harmless TCP forward to the node's OWN ssh port through the tunnel —
        # if that succeeds, forwarding is fine and the block is the socket.
        fwd_ok = False
        try:
            _fr, _fw = await asyncio.wait_for(
                ssh_conn.open_connection("127.0.0.1", port), timeout=min(to, 10.0))
            _fw.close()
            fwd_ok = True
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:  # noqa: BLE001
            fwd_ok = False
        if fwd_ok:
            _hint = (f"SSH forwarding IS enabled (a test forward through this connection succeeded) — so the block "
                     f"is the SOCKET, not sshd. Either the '{user}' user can't read {sock_path} (use a root SSH user "
                     f"for this node, or add '{user}' to the 'docker' group), or Docker isn't running / the socket "
                     f"path is wrong. Check on the node: `ls -l {sock_path}` and `id {user}`.")
        else:
            _hint = ("SSH forwarding is BLOCKED (a plain TCP forward through this connection also failed) — set "
                     "`AllowTcpForwarding yes` (or `local`) AND `AllowStreamLocalForwarding yes` in the node's sshd "
                     "config and RESTART the SSH service. On TrueNAS: System → Services → SSH → tick 'Allow TCP Port "
                     "Forwarding' + add those to Auxiliary Parameters, then restart SSH; verify with "
                     "`sshd -T | grep -i forwarding`.")
        _add("socket", "Docker socket over SSH", False, str(e), _hint)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        _add("socket", "Docker socket over SSH", False, f"{type(e).__name__}: {e}",
             f"Couldn't reach the Docker socket at {sock_path} over the SSH tunnel.")
    finally:
        if ssh_conn is not None:
            ssh_conn.close()
    return _result()
