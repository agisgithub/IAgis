"""Broker HTTP mínimo: mantém a CA fora do worker IAgis."""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

HOST = os.getenv("IAGIS_VPN_BROKER_HOST", "127.0.0.1")
PORT = int(os.getenv("IAGIS_VPN_BROKER_PORT", "8091"))
TOKEN = os.environ.get("IAGIS_VPN_BROKER_TOKEN", "")
REMOTE_HOST = os.getenv("IAGIS_VPN_REMOTE_HOST", "127.0.0.1")
REMOTE_PORT = int(os.getenv("IAGIS_VPN_REMOTE_PORT", "1194"))
PKI = Path(os.getenv("IAGIS_VPN_PKI", "/vpn/pki"))
CA_CERT = Path(os.getenv("IAGIS_VPN_CA_CERT", "/vpn/ca.crt"))
TLS_CRYPT = Path(os.getenv("IAGIS_VPN_TLS_CRYPT", "/vpn/tls-crypt.key"))
SERVER_CRL = Path(os.getenv("IAGIS_VPN_SERVER_CRL", "/vpn/server-crl.pem"))
PROFILES = Path(os.getenv("IAGIS_VPN_PROFILES", "/vpn/profiles"))
EASYRSA = os.getenv("IAGIS_EASYRSA", "/usr/share/easy-rsa/easyrsa")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RESERVED_NAMES = {"server"}
CN_RE = re.compile(r"(?:^|/)CN=([^/]+)")
LOCK = threading.Lock()


class BrokerFailure(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def validate_runtime() -> None:
    if HOST not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("o broker VPN só pode escutar no loopback")
    if len(TOKEN) < 32:
        raise RuntimeError("IAGIS_VPN_BROKER_TOKEN deve ter ao menos 32 caracteres")
    required = (PKI / "private" / "ca.key", CA_CERT, TLS_CRYPT, PKI / "index.txt")
    if not all(path.is_file() for path in required):
        raise RuntimeError("PKI OpenVPN incompleta")
    PROFILES.mkdir(parents=True, exist_ok=True)


def validate_name(value: object) -> str:
    if (not isinstance(value, str) or not NAME_RE.fullmatch(value)
            or value.casefold() in RESERVED_NAMES):
        raise BrokerFailure(HTTPStatus.BAD_REQUEST, "nome de cliente inválido")
    return value


def certificate_rows() -> dict[str, dict[str, str | None]]:
    rows: dict[str, dict[str, str | None]] = {}
    for line in (PKI / "index.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        match = CN_RE.search(parts[5])
        if not match:
            continue
        state = {"V": "active", "R": "revoked", "E": "expired"}.get(parts[0], "unknown")
        rows[match.group(1)] = {
            "name": match.group(1),
            "status": state,
            "expires": parts[1] or None,
            "revoked_at": parts[2] or None,
            "serial": parts[3] or None,
        }
    return rows


def run_easyrsa(*args: str) -> None:
    env = {
        **os.environ,
        "EASYRSA_BATCH": "1",
        "EASYRSA_PKI": str(PKI),
        "EASYRSA_ALGO": "rsa",
        "EASYRSA_KEY_SIZE": "3072",
        "EASYRSA_DIGEST": "sha256",
        "EASYRSA_CERT_EXPIRE": "825",
    }
    try:
        subprocess.run(
            [EASYRSA, *args], check=True, env=env, timeout=180,
            stdin=subprocess.DEVNULL, capture_output=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise BrokerFailure(HTTPStatus.INTERNAL_SERVER_ERROR, "falha na operação da PKI") from exc


def certificate_pem(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.DOTALL
    )
    if not match:
        raise BrokerFailure(HTTPStatus.INTERNAL_SERVER_ERROR, "certificado inválido na PKI")
    return match.group(0)


def write_profile(name: str) -> Path:
    certificate = PKI / "issued" / f"{name}.crt"
    private_key = PKI / "private" / f"{name}.key"
    if not certificate.is_file() or not private_key.is_file():
        raise BrokerFailure(HTTPStatus.INTERNAL_SERVER_ERROR, "material do cliente incompleto")
    profile = f"""client
dev tun
proto udp
remote {REMOTE_HOST} {REMOTE_PORT}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
verify-x509-name server name
tls-version-min 1.2
data-ciphers AES-256-GCM
cipher AES-256-GCM
auth SHA256
pull-filter ignore \"redirect-gateway\"
mssfix 1300
verb 3
<ca>
{certificate_pem(CA_CERT)}
</ca>
<cert>
{certificate_pem(certificate)}
</cert>
<key>
{private_key.read_text(encoding="utf-8").strip()}
</key>
<tls-crypt>
{TLS_CRYPT.read_text(encoding="utf-8").strip()}
</tls-crypt>
"""
    target = PROFILES / f"{name}.ovpn"
    temporary = PROFILES / f".{name}.{uuid4().hex}.tmp"
    temporary.write_text(profile, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target


def create_client(name: str) -> dict[str, object]:
    with LOCK:
        existing = certificate_rows().get(name)
        if existing and existing["status"] != "active":
            raise BrokerFailure(HTTPStatus.CONFLICT, "o nome já pertence a certificado não ativo")
        created = existing is None
        if created:
            run_easyrsa("build-client-full", name, "nopass")
        write_profile(name)
        return {"name": name, "status": "active", "created": created}


def sync_crl() -> None:
    generated = PKI / "crl.pem"
    if not generated.is_file():
        raise BrokerFailure(HTTPStatus.INTERNAL_SERVER_ERROR, "CRL não foi gerada")
    with generated.open("rb") as source, SERVER_CRL.open("wb") as destination:
        shutil.copyfileobj(source, destination)
    SERVER_CRL.chmod(0o644)


def revoke_client(name: str) -> dict[str, object]:
    with LOCK:
        existing = certificate_rows().get(name)
        if existing is None:
            raise BrokerFailure(HTTPStatus.NOT_FOUND, "cliente VPN não encontrado")
        changed = existing["status"] != "revoked"
        if changed:
            if existing["status"] != "active":
                raise BrokerFailure(HTTPStatus.CONFLICT, "certificado não está ativo")
            run_easyrsa("revoke", name)
        run_easyrsa("gen-crl")
        sync_crl()
        (PROFILES / f"{name}.ovpn").unlink(missing_ok=True)
        return {"name": name, "status": "revoked", "revoked": changed}


class Handler(BaseHTTPRequestHandler):
    server_version = "IAgisVPNBroker/1"

    def log_message(self, format: str, *args: object) -> None:
        # Não registra token, corpo da requisição ou conteúdo de perfis.
        print(json.dumps({"event": "http", "message": format % args}), flush=True)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {TOKEN}"
        return hmac.compare_digest(supplied, expected)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 16 * 1024:
            raise BrokerFailure(HTTPStatus.BAD_REQUEST, "corpo inválido")
        try:
            data = json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BrokerFailure(HTTPStatus.BAD_REQUEST, "JSON inválido") from exc
        if not isinstance(data, dict):
            raise BrokerFailure(HTTPStatus.BAD_REQUEST, "objeto JSON esperado")
        return data

    def _route(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if self.command == "GET" and path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._authorized():
            raise BrokerFailure(HTTPStatus.UNAUTHORIZED, "não autorizado")
        if self.command == "GET" and path == "/v1/clients":
            clients = [
                row for name, row in certificate_rows().items()
                if name.casefold() not in RESERVED_NAMES
            ]
            self._json(HTTPStatus.OK, {"clients": clients})
            return
        if self.command == "POST" and path == "/v1/clients":
            name = validate_name(self._read_json().get("name"))
            result = create_client(name)
            self._json(HTTPStatus.CREATED if result["created"] else HTTPStatus.OK, result)
            return
        match = re.fullmatch(r"/v1/clients/([^/]+)(/profile|/revoke)?", path)
        if not match:
            raise BrokerFailure(HTTPStatus.NOT_FOUND, "rota não encontrada")
        name = validate_name(unquote(match.group(1)))
        suffix = match.group(2)
        if self.command == "POST" and suffix == "/revoke":
            self._json(HTTPStatus.OK, revoke_client(name))
            return
        row = certificate_rows().get(name)
        if row is None:
            raise BrokerFailure(HTTPStatus.NOT_FOUND, "cliente VPN não encontrado")
        if self.command == "GET" and suffix is None:
            self._json(HTTPStatus.OK, row)
            return
        if self.command == "GET" and suffix == "/profile":
            if row["status"] != "active":
                raise BrokerFailure(HTTPStatus.CONFLICT, "perfil não está ativo")
            profile_path = PROFILES / f"{name}.ovpn"
            if not profile_path.is_file():
                with LOCK:
                    write_profile(name)
            body = profile_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-openvpn-profile")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}.ovpn"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        raise BrokerFailure(HTTPStatus.METHOD_NOT_ALLOWED, "método não permitido")

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        try:
            self._route()
        except BrokerFailure as exc:
            self._json(exc.status, {"error": exc.message})
        except Exception:  # noqa: BLE001 - fronteira HTTP nunca expõe detalhes internos
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "erro interno"})


if __name__ == "__main__":
    validate_runtime()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
