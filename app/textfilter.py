"""Filtro de saída: remove emojis e traduz as mensagens para o idioma do usuário."""
from __future__ import annotations

import re

# Dígito com keycap (1️⃣) vira "1."
_KEYCAP = re.compile(r"([0-9#*])️?⃣")
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emojis, bandeiras, símbolos
    "☀-➿"          # símbolos diversos e dingbats (✅ ❤ ✏ ...)
    "⬀-⯿"          # setas e estrelas (⬅ ⭐)
    "⌀-⏿"          # relógios (⏳ ⏰ ⏱)
    "←-⇿"          # setas
    "ℹ‼⁉〰〽㊗㊙©®™"
    "️‍⃣"
    "\U000E0020-\U000E007F"  # tags de bandeira
    "]+ ?"
)
_SPACES = re.compile(r"[ \t]{2,}")


def strip_emojis(text: str) -> str:
    if not text:
        return text
    lines = []
    for line in text.split("\n"):
        body = line.lstrip(" ")
        indent = line[: len(line) - len(body)]
        body = _KEYCAP.sub(r"\1.", body)
        body = _EMOJI.sub("", body)
        body = _SPACES.sub(" ", body).strip(" ")
        lines.append((indent + body) if body else "")
    return "\n".join(lines)


# ----- Tradução linha a linha -----
_TEMPLATES: list[tuple[re.Pattern, dict[str, str]]] = []


def _compile() -> None:
    from app.translations import LINES

    items = []
    for pt, es, en in LINES:
        key = strip_emojis(pt).strip()
        literal = key.replace("{}", "")
        parts = [re.escape(p) for p in key.split("{}")]
        pattern = re.compile("^" + "(.*?)".join(parts) + "$", re.S)
        items.append((len(literal), pattern, {"es": es, "en": en}))
    # Textos mais longos primeiro, para "UID: {} · {} dias" vencer "UID: {}".
    items.sort(key=lambda x: -x[0])
    _TEMPLATES[:] = [(p, t) for _, p, t in items]


def _translate_line(line: str, lang: str, depth: int = 0) -> str:
    if not _TEMPLATES:
        _compile()
    for pattern, targets in _TEMPLATES:
        m = pattern.match(line)
        if not m:
            continue
        values = list(m.groups())
        if depth < 2:
            values = [_translate_line(v, lang, depth + 1) if v else v for v in values]
        out = targets[lang]
        for v in values:
            out = out.replace("{}", v, 1)
        return out
    return line


def translate(text: str, lang: str) -> str:
    """Traduz cada linha conhecida; linhas sem tradução ficam como estão."""
    if not text or lang not in ("es", "en"):
        return text
    lines = []
    for line in text.split("\n"):
        stripped = line.lstrip(" ")
        indent = line[: len(line) - len(stripped)]
        lines.append(indent + _translate_line(stripped, lang) if stripped else line)
    return "\n".join(lines)


def render(text: str, lang: str) -> str:
    """Remove emojis e traduz para o idioma do usuário."""
    return translate(strip_emojis(text), lang)
