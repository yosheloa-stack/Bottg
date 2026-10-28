"""Handler de consulta de informações do jogador."""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timezone, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import texts
from app.keyboards.inline import back_home, cancel_only
from app.services.autolike import AutoLikeApi
from app.services.autosystem_likes import AutoSystemLikesApi
from app.states import InfoFlow
from app.ui import edit_screen
from app.utils import clean_game_id, extract_nick, is_valid_game_id

router = Router(name="info")


def _format_info(game_id: str, data: dict) -> str:
    nick = extract_nick(data) or ""
    # Tenta extrair campos comuns de forma tolerante
    src = data
    player = data.get("player") or data.get("jogador") or data.get("basicInfo")
    if isinstance(player, dict):
        src = {**data, **player}

    def g(*keys, default=None):
        for k in keys:
            if src.get(k) not in (None, "", "-", "—"):
                return src[k]
        return default

    linhas = [
        ("👤", "Nick", nick),
        ("🆔", "UID", game_id),
        ("📊", "Nível", g('level', 'nivel', 'nível')),
        ("💚", "Likes", g('likes', 'curtidas')),
        ("🏆", "Ranking", g('ranking', 'rank')),
        ("🛡️", "Guilda", g('clan', 'cla', 'clã', 'guild')),
        ("🌎", "Região", g('region', 'regiao', 'região', default='BR')),
    ]
    corpo = "\n".join(f"{e} <b>{r}:</b> {html.escape(str(v))}" for e, r, v in linhas if not _vazio(v))
    return f"🎮 <b>INFO FREE FIRE</b>\n\n{corpo}"


# ── O /info DO SITE (Auto System): cartão em imagem + texto sem traço ──────
# Mesmo desenho dos bots Aurora (WhatsApp e Telegram). Campo que a API não
# mandou não aparece — nada de "—" no lugar.
_BRT = timezone(timedelta(hours=-3))


def _vazio(v) -> bool:
    return v is None or str(v).strip() in ("", "-", "—")


def _numero(v) -> str:
    s = str(v).strip()
    if re.fullmatch(r"[\d.,]+", s):
        return f"{int(re.sub(r'[^\d]', '', s)):,}".replace(",", ".")
    return s


def _data_curta(v) -> str | None:
    if _vazio(v):
        return None
    s = str(v).strip()
    try:
        if re.fullmatch(r"\d{9,13}", s):
            n = int(s) / (1000 if len(s) > 11 else 1)
            return datetime.fromtimestamp(n, _BRT).strftime("%d/%m/%Y")
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            d = datetime.fromisoformat(s.replace(" UTC", "").replace(" ", "T")).replace(tzinfo=timezone.utc)
            return d.astimezone(_BRT).strftime("%d/%m/%Y")
    except (ValueError, OverflowError, OSError):
        pass
    return re.sub(r"\s*(às|as|at)\s*\d{1,2}:\d{2}(:\d{2})?\s*$", "", s, flags=re.I)


def _data_hora(v) -> str | None:
    """Data com hora (último login): "30/09/2026 01:00"."""
    if _vazio(v):
        return None
    s = str(v).strip()
    try:
        if re.fullmatch(r"\d{9,13}", s):
            n = int(s) / (1000 if len(s) > 11 else 1)
            return datetime.fromtimestamp(n, _BRT).strftime("%d/%m/%Y %H:%M")
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            d = datetime.fromisoformat(s.replace(" UTC", "").replace(" ", "T")).replace(tzinfo=timezone.utc)
            return d.astimezone(_BRT).strftime("%d/%m/%Y %H:%M")
    except (ValueError, OverflowError, OSError):
        pass
    return _data_curta(v)


# "Language_PT", "LANGUAGE_PORTUGUESE", "pt" → nome do idioma.
_IDIOMAS = [
    (r"portugu|^pt\b|_pt$", "Português"), (r"spanish|espa|^es\b|_es$", "Español"),
    (r"english|^en\b|_en$", "English"), (r"indones|^id\b|_id$", "Bahasa Indonesia"),
    (r"thai|^th\b|_th$", "ไทย"), (r"vietnam|^vi\b|_vi$", "Tiếng Việt"),
    (r"russian|^ru\b|_ru$", "Русский"), (r"arab|^ar\b|_ar$", "العربية"),
]


def _idioma(v) -> str | None:
    if _vazio(v):
        return None
    s = str(v)
    for padrao, nome in _IDIOMAS:
        if re.search(padrao, s, re.I):
            return nome
    return re.sub(r"^language[_ ]*", "", s, flags=re.I).replace("_", " ")


def _codigo(v) -> bool:
    """Rank como número cru (código do jogo) não diz nada pra quem lê."""
    return v is not None and bool(re.fullmatch(r"\s*\d+\s*", str(v)))


def _rank(nome, pontos) -> str | None:
    n = None if _vazio(nome) or _codigo(nome) else str(nome)
    p = None if _vazio(pontos) else f"{_numero(pontos)} pts"
    return " • ".join(x for x in (n, p) if x) or None


def _texto_site(d: dict) -> str:
    """O /info COMPLETO: conta, rank, perfil, pet, guilda e líder em blocos.
    Campo que a API não mandou não aparece, e bloco vazio some inteiro."""
    def linha(emoji, rotulo, valor):
        return None if _vazio(valor) else f"{emoji} <b>{rotulo}:</b> {html.escape(str(valor))}"

    def bloco(titulo, *linhas):
        ok = [x for x in linhas if x]
        return "\n".join([titulo, *ok]) if ok else ""

    def num(v):
        return None if _vazio(v) else _numero(v)

    rank = d.get("rank") or {}
    nomes = d.get("nomes") or {}
    pet = d.get("pet") or {}
    clan = d.get("clan") or {}
    cap = d.get("capitao") or {}
    cap_rank = cap.get("rank") or {}
    credito = d.get("credito") or {}
    social = d.get("social") or {}
    assinatura = "" if _vazio(social.get("assinatura")) else " ".join(str(social["assinatura"]).split())[:140]
    membros = None
    if not _vazio(clan.get("membros")):
        membros = num(clan.get("membros")) if _vazio(clan.get("capacidade")) else f"{num(clan['membros'])}/{num(clan['capacidade'])}"
    partes = [
        "🎮 <b>INFO FREE FIRE</b>",
        bloco("👤 <b>CONTA</b>",
              linha("🏷️", "Nick", d.get("nick")),
              linha("🆔", "UID", d.get("uid")),
              linha("🌎", "Região", d.get("regiao")),
              linha("🗣️", "Idioma", _idioma(d.get("linguagem"))),
              linha("📊", "Nível", d.get("nivel")),
              linha("✨", "XP", num(d.get("exp"))),
              linha("💚", "Likes", num(d.get("likes"))),
              linha("📱", "Versão", d.get("versao")),
              linha("📅", "Conta criada", _data_curta(d.get("conta_criada"))),
              linha("🕒", "Último login", _data_hora(d.get("ultimo_acesso")))),
        bloco("🏆 <b>RANK</b>",
              linha("🥇", "Rank BR", _rank(rank.get("br"), rank.get("br_pontos"))),
              linha("🔝", "Maior rank BR", None if _codigo(rank.get("br_maximo")) else rank.get("br_maximo")),
              linha("⚔️", "Rank CS", _rank(rank.get("cs"), rank.get("cs_pontos"))),
              linha("🔝", "Maior rank CS", None if _codigo(rank.get("cs_maximo")) else rank.get("cs_maximo")),
              linha("🗓️", "Temporada", rank.get("temporada"))),
        bloco("🎖️ <b>PERFIL</b>",
              linha("⭐", "Pontuação de honra", credito.get("pontuacao", d.get("reputacao"))),
              linha("🏅", "Emblemas", num(d.get("emblemas"))),
              linha("💎", "Diamantes gastos", num(d.get("diamantes_gastos"))),
              linha("👑", "Título", nomes.get("titulo")),
              linha("🖼️", "Banner", nomes.get("banner")),
              linha("🧑", "Avatar", nomes.get("avatar")),
              linha("📌", "Pin", nomes.get("pin")),
              linha("🎒", "Mochila", nomes.get("mochila"))),
        bloco("🐾 <b>PET</b>",
              linha("🏷️", "Nome", pet.get("nome")),
              linha("📊", "Nível", pet.get("nivel")),
              linha("✨", "XP", num(pet.get("exp")))),
        bloco("🛡️ <b>GUILDA</b>",
              linha("🏷️", "Nome", clan.get("nome")),
              linha("🆔", "ID", clan.get("id")),
              linha("📊", "Nível", clan.get("nivel")),
              linha("👥", "Membros", membros)),
        bloco("👑 <b>LÍDER DA GUILDA</b>",
              linha("🏷️", "Nick", cap.get("nick")),
              linha("🆔", "UID", cap.get("uid") or clan.get("capitao_uid")),
              linha("📊", "Nível", cap.get("nivel")),
              linha("💚", "Likes", num(cap.get("likes"))),
              linha("🥇", "Rank BR", _rank(cap_rank.get("br"), cap_rank.get("br_pontos"))),
              linha("🕒", "Último login", _data_hora(cap.get("ultimo_acesso")))),
        f"💬 <i>{html.escape(assinatura)}</i>" if assinatura else "",
    ]
    return "\n\n".join(p for p in partes if p)


_LIMITE_LEGENDA = 1000


def _visivel(texto: str) -> int:
    """Tamanho que o Telegram conta na legenda: sem as tags HTML."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", texto)).encode("utf-16-le")) // 2


def _dividir_legenda(texto: str) -> tuple[str, str]:
    """Os blocos que cabem na legenda da foto, e o resto (vai logo abaixo)."""
    blocos = texto.split("\n\n")
    cabe: list[str] = []
    for i, b in enumerate(blocos):
        if _visivel("\n\n".join([*cabe, b])) > _LIMITE_LEGENDA:
            return "\n\n".join(cabe), "\n\n".join(blocos[i:])
        cabe.append(b)
    return texto, ""


async def _info_site(message: Message, game_id: str, site: AutoSystemLikesApi) -> bool:
    """Consulta pelo site. False = não deu (o chamador tenta a API antiga)."""
    status_msg = await message.answer("⏳ Consultando...")
    (status, d), imagem = await asyncio.gather(site.player_info(game_id), site.player_banner(game_id))
    if status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return True
    if status != 200 or d.get("sucesso") is not True:
        await status_msg.delete()
        return False
    texto = _texto_site(d)
    if imagem:
        legenda, resto = _dividir_legenda(texto)
        try:
            await message.answer_photo(BufferedInputFile(imagem, filename="info.png"), caption=legenda or None)
            await status_msg.delete()
            if resto:
                await message.answer(resto)
            return True
        except Exception:  # noqa: BLE001 — sem a foto, vai o texto
            pass
    await status_msg.edit_text(texto)
    return True


async def _do_info(
    message: Message, game_id: str, autolike: AutoLikeApi, site: AutoSystemLikesApi | None = None
) -> None:
    if site is not None and site.enabled and await _info_site(message, game_id, site):
        return
    status_msg = await message.answer("⏳ Consultando...")
    result = await autolike.info_player(game_id)
    if result.status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return
    if not result.ok or not isinstance(result.data, dict):
        await status_msg.edit_text(texts.GENERIC_ERROR)
        return
    await status_msg.edit_text(_format_info(game_id, result.data))


@router.message(Command("info"))
async def cmd_info(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    autolike: AutoLikeApi,
    autosystem_likes_api: AutoSystemLikesApi | None = None,
) -> None:
    arg = (command.args or "").strip()
    if not arg:
        if message.chat.type == "private":
            await state.set_state(InfoFlow.waiting_id)
            await message.answer(texts.ASK_INFO_ID, reply_markup=cancel_only())
        else:
            await message.reply("Use assim: <code>/info SEU_ID</code>")
        return
    game_id = clean_game_id(arg.split()[0])
    if not is_valid_game_id(game_id):
        await message.reply(texts.INVALID_ID)
        return
    await _do_info(message, game_id, autolike, autosystem_likes_api)


@router.callback_query(F.data == "menu:info")
async def cb_info(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(InfoFlow.waiting_id)
    await edit_screen(query, texts.ASK_INFO_ID, cancel_only())
    await query.answer()


@router.message(InfoFlow.waiting_id)
async def info_receive_id(
    message: Message,
    state: FSMContext,
    autolike: AutoLikeApi,
    autosystem_likes_api: AutoSystemLikesApi | None = None,
) -> None:
    game_id = clean_game_id(message.text or "")
    if not is_valid_game_id(game_id):
        await message.answer(texts.INVALID_ID, reply_markup=cancel_only())
        return
    await state.clear()
    await _do_info(message, game_id, autolike, autosystem_likes_api)
    await message.answer("Use /start para voltar ao menu.", reply_markup=back_home())
