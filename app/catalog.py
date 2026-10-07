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
        return self.stock != 0 and self.price > 0

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
    ffhub_configured = bool(ffhub_private_key or config.ffhub_api_key)
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
        if product.delivery in {"ffhub_autolike", "ffhub_like_once"} and not ffhub_configured:
            stock = 0
        resolved[code] = ResolvedProduct(product=product, price=price, stock=stock)
    return resolved


async def resolve_product(
    config: Config, db: Database, code: str
) -> ResolvedProduct | None:
    products = await resolve_products(config, db)
    return products.get(code)
