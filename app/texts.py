"""Textos das mensagens do bot (centralizados para fácil edição)."""

WELCOME = (
    "💚 <b>{shop_name}</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "<b>Serviços Free Fire • Entrega automática</b>\n\n"
    "🛒 <b>Comprar</b>\n"
    "Passe Booyah e Auto-Like.\n\n"
    "🔎 <b>Consultar jogador</b>\n"
    "Veja os dados de uma conta pelo ID.\n\n"
    "📦 <b>Meus pedidos</b>\n"
    "Acompanhe suas compras e entregas.\n\n"
    "❤️ <b>Enviar likes</b>\n"
    "Em grupos, use <code>/like ID</code>.\n\n"
    "👇 Escolha uma opção abaixo:"
)

STORE_HEADER = (
    "🛒 <b>Loja</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Selecione um produto para comprar:\n"
)

MENU_HINT = "👇 Selecione uma opção no menu"

ASK_LIKE_ID = (
    "❤️ <b>Enviar Likes</b>\n\n"
    "Envie o <b>ID</b> da conta de Free Fire que vai receber os likes.\n"
    "<i>Apenas números (5 a 20 dígitos).</i>"
)

LIKE_PRIVATE_BLOCKED = (
    "❤️ <b>Envio de likes só funciona em grupos!</b>\n\n"
    "Entre no nosso grupo e use o comando:\n"
    "<code>/like SEU_ID</code>\n\n"
    "Por aqui (privado) você tem a <b>Loja</b>, consultas e seus pedidos. 👇"
)

ASK_INFO_ID = (
    "🔎 <b>Consultar Jogador</b>\n\n"
    "Envie o <b>ID</b> da conta de Free Fire que deseja consultar.\n"
    "<i>Apenas números.</i>"
)

INVALID_ID = (
    "⚠️ ID inválido. Envie apenas números (5 a 20 dígitos).\n"
    "Tente novamente ou toque em <b>Cancelar</b>."
)

PRODUCT_DETAIL = (
    "🛒 <b>{title}</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "{description}\n\n"
    "💰 Valor: <b>R$ {price}</b>\n"
    "📦 Estoque: <b>{stock}</b>\n\n"
    "Envie o <b>ID</b> da sua conta de Free Fire para continuar.\n"
    "<i>Apenas números (5 a 20 dígitos).</i>"
)

OUT_OF_STOCK = (
    "😔 <b>Produto esgotado no momento.</b>\n\n"
    "Volte mais tarde ou fale com o suporte."
)

CONFIRM_PURCHASE = (
    "🧾 <b>Confirme seu pedido</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📦 Produto: <b>{title}</b>\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "{nick_line}"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "Está tudo certo?"
)

PIX_MESSAGE = (
    "💠 <b>PIX gerado com sucesso!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📦 {title}\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "1️⃣ Copie o código PIX abaixo\n"
    "2️⃣ Pague no app do seu banco\n"
    "3️⃣ A entrega é <b>automática</b> ✅\n\n"
    "<code>{qr_code}</code>\n\n"
    "⏳ <i>Aguardando pagamento... você será avisado assim que cair.</i>"
)

PAYMENT_APPROVED = "✅ <b>Pagamento aprovado!</b> Preparando sua entrega..."

DELIVERY_SUCCESS_PASSE = (
    "🎉 <b>Passe Booyah entregue!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "{detail}\n\n"
    "Obrigado pela compra! 💚"
)

DELIVERY_SUCCESS_AUTOLIKE = (
    "🎉 <b>Auto-Like ativado!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "🔁 Likes automáticos por <b>{days} dias</b>.\n"
    "{detail}\n\n"
    "Obrigado pela compra! 💚"
)

DELIVERY_FAILED = (
    "⚠️ <b>Pagamento confirmado, mas a entrega automática falhou.</b>\n\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "📄 Pedido: <code>#{order_id}</code>\n\n"
    "Não se preocupe — nossa equipe foi notificada e vai resolver. "
    "Guarde o número do pedido."
)

LIKE_SUCCESS = (
    "💚 𝙇𝙄𝙆𝙀𝙎 𝙀𝙉𝙑𝙄𝘼𝘿𝙊𝙎\n\n"
    "👤 𝙅𝙤𝙜𝙖𝙙𝙤𝙧: <b>{nick}</b>\n"
    "🆔 𝙐𝙄𝘿: <code>{game_id}</code>\n"
    "🌎 𝙍𝙚𝙜𝙞𝙖̃𝙤: <b>{region}</b>\n\n"
    "📊 𝙇𝙞𝙠𝙚𝙨 𝙖𝙣𝙩𝙚𝙨: <b>{antes}</b>\n"
    "📈 𝙇𝙞𝙠𝙚𝙨 𝙖𝙜𝙤𝙧𝙖: <b>{depois}</b>\n"
    "❤️ 𝙀𝙣𝙫𝙞𝙖𝙙𝙤𝙨: <b>+{enviadas}</b>\n"
    "🎯 𝙈𝙚𝙩𝙖: <b>{target} 𝙡𝙞𝙠𝙚𝙨</b>\n"
    "⚡ 𝙏𝙚𝙢𝙥𝙤: <b>{tempo}s</b>\n\n"
    "✅ 𝙇𝙞𝙠𝙚𝙨 𝙘𝙤𝙣𝙛𝙞𝙧𝙢𝙖𝙙𝙤𝙨 𝙘𝙤𝙢 𝙨𝙪𝙘𝙚𝙨𝙨𝙤!"
)

LIKE_ALREADY = (
    "⏳ <b>Este jogador não pode receber mais likes no momento.</b>\n\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "O perfil atingiu o limite atual. Tente novamente após o próximo reset.\n{cooldown}"
)

LIKE_NOT_FOUND = "❌ Jogador não encontrado. Verifique o ID e tente novamente."

GENERIC_ERROR = (
    "😕 Ocorreu um erro ao processar. Tente novamente em instantes.\n"
    "Se persistir, fale com o suporte."
)

CANCELLED = "❌ Operação cancelada. Use /start para voltar ao menu."
