"""Instagram comment welcome: comment event parser and the flow behind each comment."""

import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import OpenAIMessage, OpenAIPayload
from app.models.tables import Company
from app.repositories.company import CompanyRepository
from app.repositories.instagram_welcome import InstagramWelcomeRepository
from app.repositories.prompt import PromptRepository
from app.services.instagram_client import InstagramClient
from app.services.openai import OpenAIService
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

DM_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "instagram_welcome_dm",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "dm": {"type": "string"},
                "product": {"type": ["string", "null"]},
            },
            "required": ["dm", "product"],
            "additionalProperties": False,
        },
    },
}


class InstagramComment(BaseModel):
    comment_id: str
    media_id: str
    author_id: str
    author_username: str | None = None
    text: str = ""
    parent_id: str | None = None


def _required_id(raw: Any) -> str:
    # str() alone would turn None into "None" and pass a fake id along
    if not raw or isinstance(raw, bool) or not isinstance(raw, str | int):
        raise ValueError("missing or invalid id")
    return str(raw)


def _list_at(container: Any, key: str) -> list[Any]:
    items = container.get(key, []) if isinstance(container, dict) else None
    if not isinstance(items, list):
        logger.warning("[InstagramWelcome] Skipping malformed %r in event", key)
        return []
    return items


def parse_comments(body: dict[str, Any]) -> list[tuple[str, InstagramComment]]:
    """Extract the (instagram_account_id, comment) pairs of an event, in payload order."""
    pairs: list[tuple[str, InstagramComment]] = []
    for entry in _list_at(body, "entry"):
        for change in _list_at(entry, "changes"):
            try:
                if change.get("field") != "comments":
                    continue
                value = change["value"]
                parent_id = value.get("parent_id")
                comment = InstagramComment(
                    # Meta names the comment id differently depending on the login type
                    comment_id=_required_id(value.get("id") or value["comment_id"]),
                    media_id=_required_id(value["media"]["id"]),
                    author_id=_required_id(value["from"]["id"]),
                    author_username=value["from"].get("username"),
                    text=value.get("text") or "",
                    parent_id=_required_id(parent_id) if parent_id is not None else None,
                )
                pairs.append((_required_id(entry["id"]), comment))
            except ValidationError:
                # the pydantic message carries the received text and username: keep it out of logs
                logger.warning("[InstagramWelcome] Skipping comment with invalid field types")
            except (KeyError, TypeError, AttributeError, ValueError) as e:
                logger.warning("[InstagramWelcome] Skipping malformed comment: %r", e)
    return pairs


async def handle_comment(db: AsyncSession, company: Company, comment: InstagramComment) -> None:
    """Send the DM de boas-vindas for a Comentário elegível, then the Resposta pública."""
    company_id = company.id
    account_id = company.instagram_account_id
    access_token = company.instagram_access_token
    public_reply = company.instagram_public_reply
    comment_id, media_id = comment.comment_id, comment.media_id

    if not access_token or not public_reply:
        logger.info("[InstagramWelcome] Company %s has no token or Resposta pública", company_id)
        return
    if comment.parent_id or comment.author_id == account_id:
        logger.info("[InstagramWelcome] Comment %s is a reply or the Company's own", comment_id)
        return
    if not (comment_id and media_id and comment.author_id):
        logger.info("[InstagramWelcome] Comment without ids, cannot be reserved")
        return

    welcomes = InstagramWelcomeRepository(db)
    client = InstagramClient()
    welcome_id = None
    step = "reserve"
    try:
        config = await PromptRepository(db).get_prompt_with_config(
            company_id, "instagram_welcome_dm", "instagram_welcome"
        )
        if config is None:
            logger.info("[InstagramWelcome] Company %s has no welcome prompt", company_id)
            return

        welcome_id = await welcomes.reserve(
            company_id,
            comment_id,
            media_id,
            comment.author_id,
            comment.author_username,
            comment.text,
        )
        if welcome_id is None:
            logger.debug("[InstagramWelcome] Turn already taken: comment=%s", comment_id)
            return

        step = "fetch_caption"
        media = await client.get_media(media_id, access_token)
        caption = media.get("caption") or ""
        logger.info("[InstagramWelcome] Post of comment %s: %s", comment_id, media.get("permalink"))

        step = "generate_dm"
        username = f"@{comment.author_username}" if comment.author_username else ""
        # caption last: a caption holding the literal "[USERNAME]" must not be re-substituted
        prompt = config.prompt.replace("[USERNAME]", username).replace("[LEGENDA]", caption)
        api_key = await CompanyRepository(db).get_openai_api_key(company_id)
        # one user message, no tools, strict schema: caption and username are third-party data
        response = await OpenAIService(api_key).chat_completion(
            OpenAIPayload(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                messages=[OpenAIMessage(role="user", content=prompt)],
                response_format=DM_RESPONSE_FORMAT,
            )
        )
        generated = json.loads(response.choices[0].message.content or "")
        dm = generated["dm"]
        if not dm.strip():
            raise ValueError("LLM returned an empty dm")
        product = (generated["product"] or "").strip() or None

        step = "send_dm"
        sent = await client.send_private_reply(comment_id, dm, access_token)
        recipient_id = sent.get("recipient_id")
        if not recipient_id:
            logger.warning("[InstagramWelcome] DM sent, no recipient id: comment=%s", comment_id)

        step = "mark_pending"
        await welcomes.mark_pending(welcome_id, caption, product, dm, recipient_id)
    except Exception as e:
        logger.exception("[InstagramWelcome] Welcome failed at %s: comment=%s", step, comment_id)
        try:
            await db.rollback()
            if welcome_id is not None:
                await welcomes.mark_failed(welcome_id)
        except Exception:
            logger.exception("[InstagramWelcome] Could not mark welcome %s as failed", welcome_id)
        send_critical_alert(
            "INSTAGRAM_WELCOME_FAILED",
            "instagram_welcome.py:handle_comment",
            e,
            company_id=company_id,
            extra=f"comment={comment_id} media={media_id} step={step}",
        )
        return

    try:
        await client.reply_to_comment(comment_id, public_reply, access_token)
    except Exception as e:
        logger.exception("[InstagramWelcome] Resposta pública failed: comment=%s", comment_id)
        send_critical_alert(
            "INSTAGRAM_PUBLIC_REPLY_FAILED",
            "instagram_welcome.py:handle_comment",
            e,
            company_id=company_id,
            extra=f"comment={comment_id} media={media_id}",
        )
