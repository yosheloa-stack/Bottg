"""Gateway Efí Bank para cobranças PIX automáticas."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import ssl
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp

from app.services.payments.base import PaymentGateway, PaymentStatus, PixCharge

logger = logging.getLogger(__name__)

PROD_URL = "https://pix.api.efipay.com.br"
SANDBOX_URL = "https://pix-h.api.efipay.com.br"
TIMEOUT = aiohttp.ClientTimeout(total=35)


class EfiGateway(PaymentGateway):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        pix_key: str,
        *,
        cert_path: str = "",
        cert_pem_base64: str = "",
        sandbox: bool = False,
        webhook_token: str = "",
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._pix_key = pix_key.strip()
        self._base = SANDBOX_URL if sandbox else PROD_URL
        self._webhook_token = webhook_token.strip()
        self._cert_path = self._prepare_cert(cert_path, cert_pem_base64)
        self._ssl = self._build_ssl_context(self._cert_path)
        self._token: str | None = None
        self._token_until = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(
            self._client_id
            and self._client_secret
            and self._pix_key
            and self._cert_path
        )

    def _prepare_cert(self, cert_path: str, cert_pem_base64: str) -> str:
        if cert_path and Path(cert_path).exists():
            return cert_path
        if not cert_pem_base64:
            return ""
        raw = base64.b64decode(cert_pem_base64)
        fd, path = tempfile.mkstemp(prefix="efi_pix_", suffix=".pem")
        os.write(fd, raw)
        os.close(fd)
        os.chmod(path, 0o600)
        return path

    @staticmethod
    def _build_ssl_context(cert_path: str) -> ssl.SSLContext | None:
        if not cert_path:
            return None
        context = ssl.create_default_context()
        # O PEM da Efí pode conter certificado + chave privada no mesmo arquivo.
        context.load_cert_chain(certfile=cert_path)
        return context

    async def _session(self) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(ssl=self._ssl)
        return aiohttp.ClientSession(connector=connector, timeout=TIMEOUT)

    async def _access_token(self) -> str:
        if not self.configured:
            raise RuntimeError(
                "Efí não configurada. Preencha CLIENT_ID, CLIENT_SECRET, PIX_KEY e certificado."
            )

        if self._token and time.monotonic() < self._token_until:
            return self._token

        async with self._token_lock:
            if self._token and time.monotonic() < self._token_until:
                return self._token

            basic = base64.b64encode(
                f"{self._client_id}:{self._client_secret}".encode()
            ).decode()
            headers = {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/json",
            }

            async with await self._session() as session:
                async with session.post(
                    f"{self._base}/oauth/token",
                    headers=headers,
                    json={"grant_type": "client_credentials"},
                ) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200 or not data.get("access_token"):
                        raise RuntimeError(
                            f"Efí OAuth falhou ({resp.status}): {data}"
                        )

            self._token = str(data["access_token"])
            expires = int(data.get("expires_in") or 3600)
            self._token_until = time.monotonic() + max(60, expires - 60)
            return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with await self._session() as session:
            async with session.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                json=json,
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {"raw": await resp.text()}
                return resp.status, data if isinstance(data, dict) else {}

    async def create_pix(
        self,
        amount: Decimal,
        description: str,
        external_reference: str,
        payer_email: str,
        payer_name: str,
    ) -> PixCharge:
        del payer_email, payer_name
        payload = {
            "calendario": {"expiracao": 3600},
            "valor": {"original": f"{amount:.2f}"},
            "chave": self._pix_key,
            "solicitacaoPagador": f"{description} | Pedido #{external_reference}"[:140],
        }
        status, data = await self._request("POST", "/v2/cob", json=payload)
        if status != 201:
            raise RuntimeError(f"Efí retornou {status}: {data}")

        txid = str(data.get("txid") or "")
        if not txid:
            raise RuntimeError("Efí não retornou txid para a cobrança.")

        location = str(data.get("location") or "")
        ticket_url = location
        if ticket_url and not ticket_url.startswith(("http://", "https://")):
            ticket_url = f"https://{ticket_url}"

        return PixCharge(
            payment_id=txid,
            status=PaymentStatus.PENDING,
            qr_code=str(data.get("pixCopiaECola") or ""),
            qr_code_base64="",
            ticket_url=ticket_url,
            amount=amount,
        )

    async def get_status(self, payment_id: str) -> PaymentStatus:
        status, data = await self._request("GET", f"/v2/cob/{payment_id}")
        if status != 200:
            logger.warning(
                "Consulta Efí falhou txid=%s status=%s data=%s",
                payment_id,
                status,
                data,
            )
            return PaymentStatus.UNKNOWN

        state = str(data.get("status") or "").upper()
        if state == "CONCLUIDA":
            return PaymentStatus.APPROVED
        if state == "ATIVA":
            return PaymentStatus.PENDING
        if state.startswith("REMOVIDA"):
            return PaymentStatus.CANCELLED
        return PaymentStatus.UNKNOWN

    async def parse_webhook(self, query: dict, body: dict) -> str | None:
        if self._webhook_token and query.get("hmac") != self._webhook_token:
            return None
        pix = body.get("pix") if isinstance(body, dict) else None
        if not isinstance(pix, list) or not pix:
            return None
        txid = pix[0].get("txid") if isinstance(pix[0], dict) else None
        return str(txid) if txid else None

    async def register_webhook(self, webhook_url: str) -> bool:
        if not webhook_url or not self._pix_key:
            return False
        url = webhook_url
        separator = "&" if "?" in url else "?"
        if self._webhook_token:
            url += f"{separator}hmac={self._webhook_token}&ignorar="
        else:
            url += f"{separator}ignorar="
        status, data = await self._request(
            "PUT",
            f"/v2/webhook/{self._pix_key}",
            json={"webhookUrl": url},
        )
        if status == 201:
            return True
        logger.warning("Falha ao registrar webhook Efí: status=%s data=%s", status, data)
        return False
