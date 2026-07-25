"""Textos das mensagens do bot (centralizados para fácil edição)."""

WELCOME = (
    "👋 <b>Bem-vindo(a) à {shop_name}!</b>\n\n"
    "Aqui você garante serviços para <b>Free Fire</b> com entrega automática:\n\n"
    "🎟️ <b>Passe Booyah</b> — entregue direto no seu ID\n"
    "🔁 <b>Auto-Like</b> — likes diários automáticos\n"
    "❤️ <b>Likes grátis</b> — envie likes para qualquer ID\n\n"
    "Escolha uma opção abaixo 👇"
)

MENU_HINT = "Selecione uma opção no menu 👇"

ASK_LIKE_ID = (
    "❤️ <b>Enviar Likes</b>\n\n"
    "Envie o <b>ID</b> da conta de Free Fire que vai receber os likes.\n"
    "<i>Apenas números (5 a 20 dígitos).</i>"
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

ASK_PURCHASE_ID = (
    "🛒 <b>{title}</b>\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "{description}\n\n"
    "Envie o <b>ID</b> da sua conta de Free Fire para continuar.\n"
    "<i>Apenas números (5 a 20 dígitos).</i>"
)

CONFIRM_PURCHASE = (
    "🧾 <b>Confirme seu pedido</b>\n\n"
    "📦 Produto: <b>{title}</b>\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "{nick_line}"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "Está tudo certo?"
)

PIX_MESSAGE = (
    "💠 <b>Pagamento via PIX gerado!</b>\n\n"
    "📦 {title}\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "💰 Valor: <b>R$ {price}</b>\n\n"
    "1️⃣ Copie o código abaixo (PIX copia e cola)\n"
    "2️⃣ Pague no app do seu banco\n"
    "3️⃣ A entrega é <b>automática</b> após a confirmação ✅\n\n"
    "<code>{qr_code}</code>\n\n"
    "⏳ <i>Aguardando pagamento... você será avisado assim que cair.</i>"
)

PAYMENT_APPROVED = "✅ <b>Pagamento aprovado!</b> Preparando sua entrega..."

DELIVERY_SUCCESS_PASSE = (
    "🎉 <b>Passe Booyah entregue!</b>\n\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "{detail}\n\n"
    "Obrigado pela compra! 💚"
)

DELIVERY_SUCCESS_AUTOLIKE = (
    "🎉 <b>Auto-Like ativado!</b>\n\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "🔁 Seu ID receberá likes automáticos por <b>{days} dias</b>.\n"
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
    "❤️ <b>Likes enviados com sucesso!</b>\n\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "👤 {nick}\n"
    "➕ Likes enviados: <b>{enviadas}</b>\n"
)

LIKE_ALREADY = (
    "⏳ <b>Este ID já recebeu likes hoje.</b>\n\n"
    "🎮 ID: <code>{game_id}</code>\n"
    "Tente novamente após o período de 24h.\n{cooldown}"
)

LIKE_NOT_FOUND = "❌ Jogador não encontrado. Verifique o ID e tente novamente."

GENERIC_ERROR = (
    "😕 Ocorreu um erro ao processar. Tente novamente em instantes.\n"
    "Se persistir, fale com o suporte."
)

CANCELLED = "❌ Operação cancelada. Use /start para voltar ao menu."
