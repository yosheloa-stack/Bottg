"""Textos das mensagens do bot (centralizados para fácil edição)."""

WELCOME = (
    "💚 <b>{shop_name}</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "<b>Serviços Free Fire • Entrega automática</b>\n\n"
    "Escolha uma opção abaixo para continuar. 👇"
)

STORE_HEADER = (
    "🛒 <b>LOJA</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Escolha o serviço que deseja comprar:"
)

AUTOLIKE_STORE = (
    "💎 <b>AUTO LIKE 500/1000</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Receba aproximadamente <b>500 a 1.000 likes por dia</b> "
    "na sua conta do Free Fire.\n\n"
    "✅ 1 envio automático por dia\n"
    "✅ Ativação após o pagamento\n"
    "✅ UID solicitado depois do PIX\n"
    "✅ Primeiro envio inicia após ativação\n\n"
    "📆 <b>Escolha seu plano:</b>"
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


PREMIUM_AUTOLIKE_CONFIRM = (
    "💎 <b>Auto Like 500/1000</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "❤️ Média diária: <b>500 a 1.000 likes</b>\n"
    "📆 Duração: <b>{days} dias</b>\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "Após o pagamento:\n"
    "1️⃣ você envia seu UID\n"
    "2️⃣ o plano é ativado\n"
    "3️⃣ os likes começam automaticamente\n\n"
    "Deseja continuar?"
)

PREMIUM_AUTOLIKE_PIX = (
    "💠 <b>PIX gerado com sucesso!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📦 {title}\n"
    "⏱ Plano: <b>{days} dias</b>\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "1️⃣ Faça o pagamento pelo PIX abaixo\n"
    "2️⃣ A confirmação é automática\n"
    "3️⃣ Depois do pagamento eu vou pedir o seu UID\n\n"
    "<code>{qr_code}</code>\n\n"
    "⏳ <i>Aguardando confirmação do pagamento...</i>"
)

PREMIUM_AUTOLIKE_ASK_ID = (
    "✅ <b>Pagamento confirmado!</b>\n\n"
    "💎 Seu plano de Auto-Like Premium está liberado.\n"
    "Agora envie somente o <b>UID</b> da conta que vai receber os likes.\n\n"
    "Assim que eu receber o UID, o primeiro envio entra na fila automaticamente."
)

PREMIUM_AUTOLIKE_ACTIVATED = (
    "💚 <b>Auto-Like Premium ativado!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🆔 UID: <code>{game_id}</code>\n"
    "📆 Plano: <b>{days} dias</b>\n"
    "❤️ Frequência: <b>1 envio por dia</b>\n\n"
    "⚡ O primeiro envio será processado em instantes."
)


LIKE2_SINGLE_CONFIRM = (
    "💎 <b>Like2 • Envio único</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "❤️ 1 envio pago pela FFHub\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "Após o PIX ser confirmado, eu vou pedir o <b>UID</b> da conta.\n"
    "Depois disso, o envio é feito automaticamente.\n\n"
    "Deseja continuar?"
)

LIKE2_SINGLE_PIX = (
    "💠 <b>PIX gerado com sucesso!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📦 Like2 • Envio único\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "1️⃣ Faça o pagamento pelo PIX abaixo\n"
    "2️⃣ A confirmação é automática\n"
    "3️⃣ Depois do pagamento eu vou pedir o seu UID\n\n"
    "<code>{qr_code}</code>\n\n"
    "⏳ <i>Aguardando confirmação do pagamento...</i>"
)

LIKE2_SINGLE_ASK_ID = (
    "✅ <b>Pagamento confirmado!</b>\n\n"
    "💎 Seu envio Like2 está liberado.\n"
    "Agora envie somente o <b>UID</b> da conta que vai receber os likes."
)
