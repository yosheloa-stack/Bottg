"""Configuração central do Aurora System.

IMPORTANTE:
- Este arquivo é versionado no repositório.
- Enquanto o repositório estiver público, mantenha credenciais secretas vazias.
- Quando o repositório estiver realmente privado, as credenciais podem ser
  preenchidas diretamente aqui e o fallback de ambiente pode ser removido.
"""

# Telegram
BOT_TOKEN = ""
ADMIN_IDS = [8204579375]

# Identidade
SHOP_NAME = "Aurora System"
SUPPORT_USERNAME = "@seu_suporte"
MENU_BANNER = "assets/menu.gif"

# APIs de likes
API_BASE_URL = "https://fluxggx.squareweb.app"
AUTOLIKE_API_KEY = ""

LIKES_API_BASE_URL = "http://likespainel.squareweb.app"
LIKES_API_KEY = ""
LIKES_QUANTITY = 100

AUTOSYSTEM_BASE_URL = "https://autolikesystem.com.br"
AUTOSYSTEM_API_KEY = ""

# Passe
PASSE_API_KEY = ""

# FFHub / Auto-Like Premium
FFHUB_BASE_URL = "https://ffhub-shop.shardweb.app"
FFHUB_API_KEY = ""

# Região
DEFAULT_REGION = "BR"

# Efí Bank
EFI_CLIENT_ID = ""
EFI_CLIENT_SECRET = ""
EFI_PIX_KEY = ""
EFI_CERT_PATH = ""
EFI_CERT_PEM_BASE64 = ""
EFI_SANDBOX = False
EFI_WEBHOOK_TOKEN = ""

# Webhook
WEBHOOK_PUBLIC_URL = ""
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080

# Mercado Pago legado
MERCADOPAGO_ACCESS_TOKEN = ""
MERCADOPAGO_WEBHOOK_SECRET = ""

# Preços
PRICE_PASSE = "4.00"
PRICE_LIKE2_7D = "0.00"
PRICE_LIKE2_15D = "0.00"
PRICE_LIKE2_30D = "0.00"

# Banco
DB_PATH = "data/bot.db"
