"""Channel-agnostic attachment resolution (audio → transcript, else → phrase).

Shared by ChatwootService and HelenaService so the descriptive-phrase strings
and the text-precedence rules live in exactly ONE place. Pure: no client, no
company, no key — transcription is injected as a callback so this module stays
free of OpenAIService/company wiring (see ``resolve_attachment_message``).
"""

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# type → Portuguese noun phrase. Same string for every channel.
_TYPE_LABELS = {
    "image": "uma imagem",
    "video": "um vídeo",
}

# The two audio-failure messages to the lead, shared so both channels say the
# same thing (they were copy-pasted in ChatwootService before this extraction).
# GENERIC covers a hard failure (download/Whisper error); EMPTY covers a
# transcript that came back blank.
AUDIO_FAILURE_MSG = (
    "Desculpa, tive um problema ao processar seu áudio. "
    "Você pode tentar enviar novamente ou digitar a mensagem?"
)
AUDIO_EMPTY_MSG = (
    "Não consegui entender o áudio. "
    "Você pode tentar enviar novamente ou digitar a mensagem?"
)


class EmptyTranscriptError(ValueError):
    """Raised by resolve_attachment_message when Whisper returns a blank transcript.

    A distinct type so a caller can tell "audio came back empty" (→ AUDIO_EMPTY_MSG)
    from a hard transcription failure (any other exception → AUDIO_FAILURE_MSG).
    """


def describe_attachment(file_type: str | None, url: str | None) -> str:
    """Portuguese phrase telling the AI a non-audio attachment arrived.

    ``image`` → "O usuário enviou uma imagem", ``video`` → "…um vídeo".
    Anything else falls back to the file extension parsed from ``url``'s path
    (upper-cased when ``<= 5`` chars → "…um arquivo PDF"), else "…um arquivo".
    """
    if file_type in _TYPE_LABELS:
        description = _TYPE_LABELS[file_type]
    else:
        ext = ""
        if url:
            path = urlparse(url).path
            if "." in path:
                ext = path.rsplit(".", 1)[-1].upper()
        description = f"um arquivo {ext}" if ext and len(ext) <= 5 else "um arquivo"

    return f"O usuário enviou {description}"


async def resolve_attachment_message(
    text: str | None,
    file_type: str | None,
    url: str | None,
    transcribe: Callable[[str], Awaitable[str]],
) -> tuple[str | None, bool]:
    """Resolve an incoming message into the text the AI core receives.

    Mirrors ChatwootService's attachment rules, channel-agnostic:

    - text precedence → ``(text, True)`` when text is non-empty;
    - no url → ``(text, False)`` (skip: no text and no attachment);
    - ``file_type == "audio"`` → ``await transcribe(url)``; an empty transcript
      raises ``ValueError`` so the caller runs its own channel-specific failure
      path (error message to the lead + critical alert);
    - non-audio → ``(describe_attachment(file_type, url), True)``.

    Returns ``(message, should_continue)``.
    """
    if text and text.strip():
        return text, True

    if not url:
        return text, False

    if file_type == "audio":
        transcript = await transcribe(url)
        if not transcript.strip():
            raise EmptyTranscriptError("empty audio transcript")
        return transcript, True

    return describe_attachment(file_type, url), True


if __name__ == "__main__":
    # House-style self-check (see tests/test_*.py): plain asserts, no framework.
    assert describe_attachment("image", None) == "O usuário enviou uma imagem"
    assert describe_attachment("video", None) == "O usuário enviou um vídeo"
    assert (
        describe_attachment(None, "https://x/y/doc.pdf") == "O usuário enviou um arquivo PDF"
    )
    assert describe_attachment(None, None) == "O usuário enviou um arquivo"
    # long "extension" (a query-ish path segment) is not a real ext → generic phrase
    assert describe_attachment(None, "https://x/y/nodotpath") == "O usuário enviou um arquivo"

    import asyncio

    async def _fixed(_url: str) -> str:
        return "transcript"

    async def _empty(_url: str) -> str:
        return "   "

    async def _checks() -> None:
        # text precedence: attachment ignored
        assert await resolve_attachment_message("hi", "audio", "u", _fixed) == ("hi", True)
        # no text, no url → skip
        assert await resolve_attachment_message("", None, None, _fixed) == ("", False)
        # audio → transcript
        assert await resolve_attachment_message(None, "audio", "u", _fixed) == (
            "transcript",
            True,
        )
        # non-audio → descriptive phrase
        assert await resolve_attachment_message(None, "image", "u", _fixed) == (
            "O usuário enviou uma imagem",
            True,
        )
        # empty transcript → raises EmptyTranscriptError so the caller can pick
        # AUDIO_EMPTY_MSG (vs AUDIO_FAILURE_MSG for a hard failure)
        try:
            await resolve_attachment_message(None, "audio", "u", _empty)
        except EmptyTranscriptError:
            pass
        else:
            raise AssertionError("empty transcript must raise EmptyTranscriptError")

    asyncio.run(_checks())
    print("OK - app/utils/attachments.py self-check passed")
