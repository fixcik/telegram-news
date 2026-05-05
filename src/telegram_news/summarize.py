from __future__ import annotations

import logging

from openai import AsyncOpenAI

from .config import Config, Group
from .tg import Message

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты помощник, который делает дайджест новостей из Telegram-каналов на русском языке.

Формат вывода — Telegram HTML. Поддерживаются ТОЛЬКО теги:
<b>, <i>, <u>, <s>, <a href="URL">, <code>, <blockquote>.
Не используй <ul>, <li>, <h1..6>, <p>, <br>, markdown (## ** [text](url)).

Структура:
- Первая строка: <b>{подходящий эмодзи} {название группы}</b>.
- Пустая строка.
- Затем буллеты. Каждый буллет:
  • в начале — тематический эмодзи (🔥 атаки/удары, 💥 разрушения, ⚡ инфраструктура, 🛡 оборона, 🪖 фронт, 📉/📈 экономика, ⚖️ право/санкции, 🏛 политика, 🤝 переговоры, 📊 статистика, 🚨 ЧП, 🛢 энергетика, и т.п. — выбирай по содержанию)
  • затем короткий жирный заголовок темы через <b>...</b>, через двоеточие или точку;
  • затем 2-4 предложения с конкретикой (цифры, имена, локации, даты), если тема в "Интересно";
  • в конце буллета — компактные ссылки в формате <a href="URL1">[1]</a> <a href="URL2">[2]</a> <a href="URL3">[3]</a> (нумерация по порядку появления, до 5 ссылок на буллет, если их больше — оставь самые релевантные).
- Между буллетами — пустая строка.

Содержание:
- Группируй однотипные новости в один буллет, не дублируй одно и то же из разных каналов.
- Темы вне фокуса — короткая строка без раскрытия или пропускай.
- Темы из "Не интересно" — пропускай молча.
- Не выдумывай факты. Если в исходниках мало деталей — пиши короче.
- Если в сообщениях есть проверяемые утверждения, цифры или имена, которые \
важны для буллета и могут быть устаревшими/неточными — сверься через web search \
и при необходимости поправь формулировку или добавь свежую ссылку. Web search \
используй точечно, не на каждый буллет.
- Если ни одно сообщение не подходит — выведи ровно одну строку: <i>Ничего по интересам за период.</i>

Аккуратно с HTML:
- В обычном тексте символы '<', '>' и '&' заменяй на '&lt;', '&gt;', '&amp;'.
- В URL внутри href ничего не экранируй — копируй как есть.
- Не вкладывай теги-ссылки друг в друга.

Среди источников могут быть групповые чаты — у их сообщений в заголовке указан \
автор в формате `[Имя]`. Используй имена когда это помогает раскрыть контекст \
обсуждения (например: «Иван предложил X, Петя возразил»), опускай когда не нужно. \
Реплаи помечены символом ↳ в начале текста.
"""


def _format_messages_for_prompt(messages: list[Message]) -> str:
    lines: list[str] = []
    for m in messages:
        header = f"[{m.channel_title} | {m.date:%Y-%m-%d %H:%M}"
        if m.sender_name:
            header += f" | {m.sender_name}"
        header += f" | {m.link}]"
        lines.append(header)
        lines.append(m.text)
        lines.append("---")
    return "\n".join(lines)


async def summarize_group(
    cfg: Config, group: Group, messages: list[Message]
) -> str:
    client = AsyncOpenAI(
        api_key=cfg.openrouter.api_key,
        base_url=cfg.openrouter.base_url,
        timeout=cfg.openrouter.request_timeout_s,
    )

    parts = [
        f"Группа каналов: {group.name}",
        "",
        f"Что интересно пользователю в этой группе:\n{group.interests}",
    ]
    if group.instructions:
        parts.append("")
        parts.append(
            f"Дополнительные инструкции для этой группы:\n{group.instructions}"
        )
    parts.append("")
    parts.append(
        f"Сырые сообщения за период:\n\n{_format_messages_for_prompt(messages)}"
    )
    parts.append("")
    parts.append("Сделай дайджест по правилам.")
    user_prompt = "\n".join(parts)

    model = cfg.openrouter.model
    if not model.endswith(":online"):
        model = f"{model}:online"

    log.info(
        "Summarizing group=%s messages=%d via model=%s",
        group.name, len(messages), model,
    )

    resp = await client.chat.completions.create(
        model=model,
        temperature=cfg.openrouter.temperature,
        max_tokens=cfg.openrouter.max_tokens,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        # OpenRouter: cap reasoning budget on reasoning-capable models.
        # For news summarization chain-of-thought is wasted spend; non-reasoning
        # models silently ignore this field.
        extra_body={"reasoning": {"effort": "low"}},
    )

    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    if not content:
        # DeepSeek v3.1 (hybrid reasoning) on OpenRouter sometimes routes the
        # actual answer into `message.reasoning` while leaving `content` empty —
        # fall back to it before giving up.
        extra = choice.message.model_extra or {}
        fallback = (extra.get("reasoning") or extra.get("reasoning_content") or "").strip()
        if fallback:
            log.warning(
                "LLM content empty for group=%s — using 'reasoning' fallback (len=%d)",
                group.name, len(fallback),
            )
            content = fallback
        else:
            log.warning(
                "LLM returned empty content for group=%s. finish_reason=%s usage=%s extra_keys=%s",
                group.name, choice.finish_reason, resp.usage, list(extra.keys()),
            )
    else:
        log.info(
            "LLM ok group=%s finish=%s tokens=%s/%s",
            group.name, choice.finish_reason,
            getattr(resp.usage, "prompt_tokens", "?"),
            getattr(resp.usage, "completion_tokens", "?"),
        )
    return content
