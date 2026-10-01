"""Instagram comment welcome: comment event parser and the hand-off of each comment."""

import logging
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Company

logger = logging.getLogger(__name__)


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
    """Hand-off of a first-level comment of a known Company."""
    logger.info(
        "[InstagramWelcome] Comment received: company=%s comment=%s media=%s",
        company.id,
        comment.comment_id,
        comment.media_id,
    )
