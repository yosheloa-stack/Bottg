# 🤖 Bottg — Bot de Vendas Telegram (Free Fire)

Bot de Telegram **multifunções** para serviços de Free Fire, com botões
profissionais, integração com a **Auto Like System API** / **Passe Booyah API**
e pagamento automático via **PIX (Mercado Pago)**.

## ✨ Funcionalidades

| Recurso | Descrição |
|---|---|
| ❤️ **Enviar Likes** | Envio de likes por ID — funciona em **grupo** (`/like <id>`) e no **privado** (menu). Respeita o cooldown de 24h. |
| 🔎 **Consultar Jogador** | Nick, nível, likes, ranking, clã e região por ID. |
| 🎟️ **Loja — Passe Booyah** | Venda + entrega automática de Passe Booyah após o pagamento. |
| 🔁 **Loja — Auto-Like (30 dias)** | Assinatura de likes automáticos diários. |
| 💠 **Pagamento PIX** | QR Code + copia-e-cola via Mercado Pago; confirmação por **webhook** e botão "Já paguei". |
| 📦 **Meus pedidos** | Histórico de pedidos do usuário. |
| ⚙️ **Painel Admin** | Estatísticas, consulta de estoque de passes e broadcast. |

## 🏗️ Arquitetura

```
bot.py                    # Ponto de entrada (polling + webhook)
app/
├── config.py             # Configuração via .env + catálogo de produtos
├── states.py             # Estados de conversa (FSM)
├── texts.py              # Textos das mensagens
├── utils.py              # Validação de ID, parsing de nick, etc.
├── delivery.py           # Entrega automática pós-pagamento (idempotente)
├── webhook.py            # Servidor aiohttp para webhook do Mercado Pago
├── database/             # SQLite assíncrono (usuários e pedidos)
├── keyboards/            # Teclados inline (botões)
├── services/
│   ├── autolike.py       # Cliente Auto Like System API
│   ├── passe.py          # Cliente Passe Booyah API
│   └── payments/         # Gateway de pagamento (interface + Mercado Pago)
└── handlers/             # Handlers: comum, likes, info, loja, admin
```

A camada de pagamento é **modular** (`PaymentGateway`): trocar o Mercado Pago
por outro provedor (PushinPay, Efí…) exige apenas uma nova implementação da
interface em `app/services/payments/`.

## 🚀 Como rodar

### 1. Pré-requisitos
- Python 3.10+
- Um bot criado no [@BotFather](https://t.me/BotFather) (token)
- Keys da API (`AUTOLIKE_API_KEY` e `PASSE_API_KEY`)
- Um **Access Token** do Mercado Pago (Credenciais de produção)

### 2. Instalação

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuração

```bash
cp .env.example .env
# edite o .env com seus tokens e keys
```

Principais variáveis (veja `.env.example` para a lista completa):

- `BOT_TOKEN` — token do Telegram
- `ADMIN_IDS` — seu ID numérico (para o painel admin)
- `AUTOLIKE_API_KEY` / `PASSE_API_KEY` — keys das APIs
- `MERCADOPAGO_ACCESS_TOKEN` — token do Mercado Pago
- `WEBHOOK_PUBLIC_URL` — URL pública que recebe o webhook do MP
- `PRICE_PASSE` / `PRICE_AUTOLIKE_30D` — preços

### 4. Execução

```bash
python bot.py
```

## 💳 Webhook do Mercado Pago

Para a confirmação **automática** dos pagamentos, o bot sobe um servidor
HTTP em `WEBHOOK_HOST:WEBHOOK_PORT` com a rota `/webhook/mercadopago`.

1. Exponha essa porta publicamente (domínio próprio, Nginx, ou um túnel como
   `ngrok`/`cloudflared`) e coloque a URL final em `WEBHOOK_PUBLIC_URL`.
2. O bot já envia essa `notification_url` a cada cobrança criada.

> Sem `WEBHOOK_PUBLIC_URL` definido, o bot continua funcionando: o cliente
> confirma o pagamento pelo botão **"Já paguei / verificar"**, que consulta o
> status diretamente no Mercado Pago.

## 🧾 Fluxo de compra

1. Cliente abre a **Loja** e escolhe um produto.
2. Informa o **ID** do Free Fire (o bot valida e mostra o nick).
3. Confirma → o bot gera um **PIX** (QR Code + copia-e-cola).
4. Ao pagar, o webhook (ou o botão "Já paguei") confirma e a **entrega é
   automática**: o produto é enviado ao ID via API e o cliente é avisado.

## 🔐 Segurança

- Segredos ficam **somente** no `.env` (fora do Git — veja `.gitignore`).
- Entrega **idempotente**: um pedido nunca é entregue duas vezes.
- Falhas de entrega notificam o cliente **e** os admins, preservando o nº do pedido.

## 📌 Observações

As respostas das APIs de Free Fire podem variar em nomes de campos; o parsing
em `app/utils.py` e nos handlers é **tolerante** e cobre as variações comuns
(`nickname`/`nick`/`nome`, etc.). Ajuste conforme o retorno real da sua key.
