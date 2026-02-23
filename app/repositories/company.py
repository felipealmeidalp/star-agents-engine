"""Repository for company operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Company

logger = logging.getLogger(__name__)


class CompanyRepository:
    """Data access layer for companies table."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize repository with database session."""
        self.db = db

    async def get_openai_api_key(self, company_id: int) -> str:
        """
        Fetch the OpenAI API key for a specific company.

        Args:
            company_id: Company ID for multi-tenancy

        Returns:
            OpenAI API key string

        Raises:
            ValueError: If company not found
            ValueError: If company has no API key configured
        """
        result = await self.db.execute(
            select(Company.openai_api_key).where(
                Company.id == company_id,
            )
        )
        row = result.scalar_one_or_none()

        if row is None:
            raise ValueError(f"Company {company_id} not found")

        if not row:
            raise ValueError(f"Company {company_id} has no OpenAI API key configured")

        return row

    async def get_rag_collection(self, company_id: int) -> str | None:
        """
        Fetch the Qdrant RAG collection name for a specific company.

        Args:
            company_id: Company ID for multi-tenancy

        Returns:
            RAG collection name or None if not configured
        """
        result = await self.db.execute(
            select(Company.rag_collection).where(
                Company.id == company_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_cw_account_id(
        self,
        cw_account_id: int,
    ) -> Company | None:
        """
        Find company by Chatwoot account ID.

        Args:
            cw_account_id: Chatwoot account ID

        Returns:
            Company or None if not found
        """
        result = await self.db.execute(
            select(Company).where(
                Company.cw_account_id == cw_account_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_cw_token(self, cw_token: str) -> Company | None:
        """
        Find company by Chatwoot webhook token.

        Args:
            cw_token: Unique webhook token (UUID string)

        Returns:
            Company or None if not found
        """
        try:
            token_uuid = UUID(cw_token)
        except ValueError:
            return None

        result = await self.db.execute(
            select(Company).where(Company.cw_token == token_uuid)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, company_id: int) -> Company | None:
        """
        Buscar company por ID.

        Args:
            company_id: Company ID (primary key)

        Returns:
            Company or None if not found
        """
        result = await self.db.execute(
            select(Company).where(Company.id == company_id)
        )
        return result.scalar_one_or_none()

    async def add_contact_to_allowed_list(
        self,
        company_id: int,
        inbox_id: int,
        contact_id: int,
    ) -> bool:
        """
        Add a contact_id to the allowed_contacts list for a given inbox.

        Args:
            company_id: Company ID
            inbox_id: Inbox ID to find in allowed_inboxes
            contact_id: Contact ID to add

        Returns:
            True if contact was added, False if already existed or inbox not found
        """
        result = await self.db.execute(
            select(Company.allowed_contacts).where(Company.id == company_id)
        )
        allowed_contacts: dict[str, Any] | None = result.scalar_one_or_none()

        if not allowed_contacts:
            logger.info(
                f"[CompanyRepo] Company {company_id} has no allowed_contacts config, skipping"
            )
            return False

        allowed_inboxes: list[dict[str, Any]] = allowed_contacts.get("allowed_inboxes", [])
        if not allowed_inboxes:
            logger.info(
                f"[CompanyRepo] Company {company_id} has empty allowed_inboxes, skipping"
            )
            return False

        for inbox_config in allowed_inboxes:
            if inbox_config.get("id") == inbox_id:
                contacts: list[int] = inbox_config.get("allowed_contacts", [])
                if contact_id in contacts:
                    logger.info(
                        f"[CompanyRepo] Contact {contact_id} already in allowed_contacts "
                        f"for inbox {inbox_id}, company {company_id}"
                    )
                    return False

                contacts.append(contact_id)
                inbox_config["allowed_contacts"] = contacts

                await self.db.execute(
                    update(Company)
                    .where(Company.id == company_id)
                    .values(allowed_contacts=allowed_contacts)
                )
                await self.db.commit()

                logger.info(
                    f"[CompanyRepo] Added contact {contact_id} to allowed_contacts "
                    f"for inbox {inbox_id}, company {company_id}"
                )
                return True

        logger.info(
            f"[CompanyRepo] Inbox {inbox_id} not found in allowed_inboxes "
            f"for company {company_id}, skipping"
        )
        return False
