"""Resolução do catálogo efetivo (preço/estoque geridos pelo admin sobre o config)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.config import Config, Product
from app.database import Database


@dataclass
class ResolvedProduct:
    product: Product
    price: Decimal
    stock: int  # -1 = ilimitado
    service_ready: bool = True
    service_issue: str = ""

    @property
    def code(self) -> str:
        return self.product.code

    @property
    def title(self) -> str:
        return self.product.title

    @property
    def description(self) -> str:
        return self.product.description

    @property
    def available(self) -> bool:
        return self.stock != 0 and self.price > 0 and self.service_ready

    @property
    def stock_label(self) -> str:
        if self.stock < 0:
            return "Ilimitado"
        if self.stock == 0:
            return "Esgotado"
        return str(self.stock)


async def resolve_products(config: Config, db: Database) -> dict[str, ResolvedProduct]:
    settings = await db.all_product_settings()
    ffhub_private_key = await db.get_owner_setting("FFHUB_API_KEY")
    autosystem_private_key = await db.get_owner_setting("AUTOSYSTEM_API_KEY")
    ffhub_configured = bool(ffhub_private_key or config.ffhub_api_key)
    autosystem_configured = bool(
        autosystem_private_key or config.autosystem_api_key
    )
    resolved: dict[str, ResolvedProduct] = {}
    for code, product in config.products.items():
        s = settings.get(code, {})
        price = product.price
        if s.get("price"):
            try:
                price = Decimal(str(s["price"]))
            except (InvalidOperation, ValueError):
                price = product.price
        stock = int(s.get("stock", -1)) if s.get("stock") is not None else -1
        service_ready = True
        service_issue = ""
        if product.delivery in {"ffhub_autolike", "ffhub_like_once"} and not ffhub_configured:
            service_ready = False
            service_issue = "FFHub API não configurada"
        elif product.delivery == "autosystem_autolike" and not autosystem_configured:
            service_ready = False
            service_issue = "AutoSystem API não configurada"
        resolved[code] = ResolvedProduct(
            product=product,
            price=price,
            stock=stock,
            service_ready=service_ready,
            service_issue=service_issue,
        )
    return resolved


async def resolve_product(
    config: Config, db: Database, code: str
) -> ResolvedProduct | None:
    products = await resolve_products(config, db)
    return products.get(code)


async def reseller_discount_percent(db: Database) -> Decimal:
    raw = await db.get_owner_setting("RESELLER_DISCOUNT_PERCENT")
    try:
        value = Decimal(str(raw or "20"))
    except (InvalidOperation, ValueError):
        value = Decimal("20")
    return max(Decimal("0"), min(value, Decimal("80")))


async def price_for_user(
    db: Database,
    product: ResolvedProduct,
    user_id: int,
) -> tuple[Decimal, bool, Decimal]:
    """Preço unitário/fixo efetivo para o usuário.

    Revendedores recebem o desconto global nos produtos vendáveis, exceto
    recarga de saldo e a própria compra de acesso à revenda.
    """
    is_reseller = await db.is_reseller(int(user_id))
    discount = Decimal("0")
    price = product.price

    if is_reseller and product.code not in {"balance_topup", "reseller_access"}:
        discount = await reseller_discount_percent(db)
        multiplier = (Decimal("100") - discount) / Decimal("100")
        price = (price * multiplier).quantize(Decimal("0.01"))

    return price, is_reseller, discount
