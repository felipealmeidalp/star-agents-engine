"""Repository for customer operations."""

import json
import logging
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Customer

logger = logging.getLogger(__name__)


class CustomerRepository:
    """Data access layer for customers table."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize repository with database session."""
        self.db = db

    async def get_by_session(
        self,
        session_id: str,
        company_id: int,
    ) -> Customer | None:
        """
        Buscar customer por session_id e company_id.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            Customer or None if not found
        """
        result = await self.db.execute(
            select(Customer).where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
                Customer.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        session_id: str,
        company_id: int,
        new_status: bool,
    ) -> None:
        """
        Atualizar status do customer.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            new_status: New status value (False = IA bloqueada)
        """
        await self.db.execute(
            update(Customer)
            .where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
            )
            .values(status=new_status)
        )
        await self.db.commit()

    async def update_sub_agent(
        self,
        session_id: str,
        company_id: int,
        new_sub_agent_id: int,
    ) -> None:
        """
        Atualizar sub_agent_id do customer.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            new_sub_agent_id: ID of the new sub-agent
        """
        await self.db.execute(
            update(Customer)
            .where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
            )
            .values(sub_agent_id=new_sub_agent_id)
        )
        await self.db.commit()

    async def clear_variable_prompt(
        self,
        session_id: str,
        company_id: int,
    ) -> int | None:
        """
        Limpar variable_prompt do customer.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            O variable_prompt_id que foi limpo (para desativar na tabela prompts)
            ou None se não havia variable_prompt ativo
        """
        # Primeiro busca o customer para pegar o variable_prompt_id
        customer = await self.get_by_session(session_id, company_id)
        if not customer or not customer.variable_prompt_id:
            return None

        variable_prompt_id = customer.variable_prompt_id

        # Limpa o variable_prompt do customer
        await self.db.execute(
            update(Customer)
            .where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
            )
            .values(
                variable_prompt_status=False,
                variable_prompt_id=None,
            )
        )
        await self.db.commit()

        return variable_prompt_id

    async def update_variable_prompt(
        self,
        session_id: str,
        company_id: int,
        prompt_id: int,
    ) -> None:
        """
        Update customer with variable prompt reference.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            prompt_id: ID of the generated prompt
        """
        await self.db.execute(
            update(Customer)
            .where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
            )
            .values(
                variable_prompt_status=True,
                variable_prompt_id=prompt_id,
            )
        )
        await self.db.commit()

    async def get_by_cw_contact_id(
        self,
        cw_contact_id: int,
    ) -> Customer | None:
        """
        Find customer by Chatwoot contact ID.

        Args:
            cw_contact_id: Chatwoot contact ID

        Returns:
            Customer or None if not found
        """
        result = await self.db.execute(
            select(Customer).where(
                Customer.cw_contact_id == cw_contact_id,
                Customer.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_cw_conversation_id(
        self,
        cw_conversation_id: int,
    ) -> Customer | None:
        """
        Find customer by Chatwoot conversation ID.

        Args:
            cw_conversation_id: Chatwoot conversation ID

        Returns:
            Customer or None if not found
        """
        result = await self.db.execute(
            select(Customer).where(
                Customer.cw_conversation_id == cw_conversation_id,
                Customer.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, customer_id: int) -> Customer | None:
        """
        Buscar customer por ID (primary key).

        Args:
            customer_id: Customer ID

        Returns:
            Customer or None if not found
        """
        result = await self.db.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create_from_chatwoot(
        self,
        cw_contact_id: int,
        cw_conversation_id: int,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
        name: str | None = None,
        avatar: str | None = None,
    ) -> Customer:
        """
        Create a new customer from Chatwoot contact.

        Uses the cw_conversation_id as the sessionId so each conversation
        is independent (even for the same contact).

        Args:
            cw_contact_id: Chatwoot contact ID (sender.id)
            cw_conversation_id: Chatwoot conversation ID (conversation.id)
            company_id: Company ID for multi-tenancy
            agent_id: Default agent ID from company
            sub_agent_id: Default sub-agent ID from company
            name: Contact name (sender.name)
            avatar: Contact avatar URL (sender.thumbnail)

        Returns:
            The created Customer instance
        """
        customer = Customer(
            company_id=company_id,
            sessionId=str(cw_conversation_id),
            cw_contact_id=cw_contact_id,
            cw_conversation_id=cw_conversation_id,
            name=name,
            avatar=avatar,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            follow_up=0,  # Explícito para manter objeto Python sincronizado com default do Postgres
        )
        self.db.add(customer)
        await self.db.commit()
        await self.db.refresh(customer)
        return customer

    async def get_or_create_from_chatwoot(
        self,
        cw_contact_id: int,
        cw_conversation_id: int,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
        name: str | None = None,
        avatar: str | None = None,
    ) -> tuple[Customer, bool]:
        """
        Busca customer existente ou cria novo a partir de dados do Chatwoot.

        Lookup por cw_conversation_id (cada conversa é independente).
        Protegido contra race condition: se duas tasks tentarem criar
        simultaneamente, a segunda detecta o IntegrityError e busca
        o registro criado pela primeira.

        Args:
            cw_contact_id: Chatwoot contact ID (sender.id)
            cw_conversation_id: Chatwoot conversation ID
            company_id: Company ID para multi-tenancy
            agent_id: Agent ID padrao da company
            sub_agent_id: Sub-agent ID padrao da company
            name: Nome do contato
            avatar: URL do avatar

        Returns:
            Tuple (Customer, is_new: bool)
        """
        # 1. Tentar buscar existente por conversation_id (mesma conversa)
        existing = await self.get_by_cw_conversation_id(cw_conversation_id)
        if existing:
            return existing, False

        # 2. Contato já existe mas abriu nova conversa → atualizar sessionId e conversation
        existing_contact = await self.get_by_cw_contact_id(cw_contact_id)
        if existing_contact:
            existing_contact.sessionId = str(cw_conversation_id)
            existing_contact.cw_conversation_id = cw_conversation_id
            await self.db.commit()
            await self.db.refresh(existing_contact)
            logger.info(
                "[CustomerRepository] Contato %d abriu nova conversa %d, "
                "sessionId atualizado",
                cw_contact_id,
                cw_conversation_id,
            )
            return existing_contact, True

        # 3. Contato novo — criar customer
        try:
            customer = Customer(
                company_id=company_id,
                sessionId=str(cw_conversation_id),
                cw_contact_id=cw_contact_id,
                cw_conversation_id=cw_conversation_id,
                name=name,
                avatar=avatar,
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                follow_up=0,
            )
            self.db.add(customer)
            await self.db.commit()
            await self.db.refresh(customer)
            return customer, True
        except IntegrityError:
            await self.db.rollback()
            logger.warning(
                "[CustomerRepository] Race condition detectada para "
                "cw_conversation_id=%d, buscando registro vencedor",
                cw_conversation_id,
            )
            existing = await self.get_by_cw_conversation_id(cw_conversation_id)
            if existing:
                return existing, False
            # Pode ser race no cw_contact_id
            existing_contact = await self.get_by_cw_contact_id(cw_contact_id)
            if existing_contact:
                return existing_contact, False
            raise

    async def update_follow_up_on_message(
        self,
        cw_contact_id: int,
        company_id: int,
    ) -> tuple[Customer | None, dict[str, Any] | None]:
        """
        Atualiza tracking de follow-up quando lead envia mensagem.

        Faz em UMA query otimizada:
        - last_message = NOW() no timezone São Paulo
        - follow_up = 0 (reset)
        - next_follow = timestamp calculado baseado em follow_ups

        O sub_agent_id é obtido do próprio customer (pode ter transitado).

        Args:
            cw_contact_id: Chatwoot contact ID
            company_id: Company ID for multi-tenancy

        Returns:
            Tuple (Customer atualizado, dict com info do follow-up para log)
            Retorna (None, None) se customer não existe
        """
        query = text("""
            WITH
            -- Base time para consistência (evita micro-variação de timestamp)
            base_time AS (
                SELECT NOW() AT TIME ZONE 'America/Sao_Paulo' AS now_sp
            ),
            -- Busca o customer para pegar o sub_agent_id atual
            customer_data AS (
                SELECT c.id, c.sub_agent_id
                FROM customers c
                WHERE c.cw_contact_id = :cw_contact_id
                  AND c.deleted_at IS NULL
                LIMIT 1
            ),
            follow_config AS (
                SELECT
                    f.step_order,
                    f.schedule_type,
                    f.delay_minutes,
                    f.weekday,
                    f.hour,
                    f.minute,
                    f.message_payload
                FROM follow_ups f, customer_data cd
                WHERE f.company_id = :company_id
                  AND f.sub_agent_id = cd.sub_agent_id
                  AND f.step_order = 1
                LIMIT 1
            ),
            next_follow_calc AS (
                SELECT
                    CASE
                        WHEN fc.schedule_type = 'minutes' THEN
                            (bt.now_sp + make_interval(mins => fc.delay_minutes::int))
                                AT TIME ZONE 'America/Sao_Paulo'
                        WHEN fc.schedule_type = 'weekday' THEN
                            CASE
                                WHEN EXTRACT(ISODOW FROM bt.now_sp)::int < fc.weekday::int
                                     OR (EXTRACT(ISODOW FROM bt.now_sp)::int = fc.weekday::int
                                         AND bt.now_sp::time < make_time(fc.hour::int, fc.minute::int, 0))
                                THEN
                                    (DATE_TRUNC('day', bt.now_sp)
                                    + make_interval(days => (fc.weekday::int - EXTRACT(ISODOW FROM bt.now_sp)::int))
                                    + make_interval(hours => fc.hour::int, mins => fc.minute::int))
                                        AT TIME ZONE 'America/Sao_Paulo'
                                ELSE
                                    (DATE_TRUNC('day', bt.now_sp)
                                    + make_interval(days => (7 - EXTRACT(ISODOW FROM bt.now_sp)::int + fc.weekday::int))
                                    + make_interval(hours => fc.hour::int, mins => fc.minute::int))
                                        AT TIME ZONE 'America/Sao_Paulo'
                            END
                        ELSE NULL
                    END as next_follow_ts,
                    fc.step_order,
                    fc.message_payload
                FROM follow_config fc, base_time bt
            ),
            updated AS (
                UPDATE customers c
                SET
                    last_message = (SELECT now_sp AT TIME ZONE 'America/Sao_Paulo' FROM base_time),
                    follow_up = 0,
                    next_follow = (SELECT next_follow_ts FROM next_follow_calc),
                    updated_at = (SELECT now_sp AT TIME ZONE 'America/Sao_Paulo' FROM base_time)
                WHERE c.cw_contact_id = :cw_contact_id
                  AND c.deleted_at IS NULL
                RETURNING *
            )
            SELECT
                u.*,
                nfc.next_follow_ts,
                nfc.step_order as follow_up_order,
                nfc.message_payload as follow_up_payload
            FROM updated u
            LEFT JOIN next_follow_calc nfc ON true;
        """)

        result = await self.db.execute(
            query,
            {"cw_contact_id": cw_contact_id, "company_id": company_id},
        )
        row = result.fetchone()

        if not row:
            return None, None

        await self.db.commit()

        # Busca customer atualizado para retornar objeto ORM
        customer = await self.get_by_cw_contact_id(cw_contact_id)

        # Extrai info do follow-up (inclui last_message para evitar dependência do cache ORM)
        follow_up_info: dict[str, Any] | None = None
        if row.follow_up_order is not None:
            follow_up_info = {
                "step_order": row.follow_up_order,
                "next_follow_ts": row.next_follow_ts,
                "message_payload": row.follow_up_payload,
                "last_message": row.last_message,
            }

        return customer, follow_up_info

    async def initialize_follow_up(
        self,
        customer_id: int,
        company_id: int,
        sub_agent_id: int,
    ) -> tuple[Customer | None, dict[str, Any] | None]:
        """
        Inicializa tracking de follow-up para um customer recém-criado.

        Atualiza em UMA query:
        - last_message = NOW() no timezone São Paulo
        - follow_up = 0
        - next_follow = timestamp calculado baseado em follow_ups

        Args:
            customer_id: Customer ID
            company_id: Company ID for multi-tenancy
            sub_agent_id: Sub-agent ID para buscar configuração de follow-up

        Returns:
            Tuple (Customer atualizado, dict com info do follow-up)
            Retorna (None, None) se customer não existe
        """
        query = text("""
            WITH
            -- Base time para consistência
            base_time AS (
                SELECT NOW() AT TIME ZONE 'America/Sao_Paulo' AS now_sp
            ),
            follow_config AS (
                SELECT
                    f.step_order,
                    f.schedule_type,
                    f.delay_minutes,
                    f.weekday,
                    f.hour,
                    f.minute,
                    f.message_payload
                FROM follow_ups f
                WHERE f.company_id = :company_id
                  AND f.sub_agent_id = :sub_agent_id
                  AND f.step_order = 1
                LIMIT 1
            ),
            next_follow_calc AS (
                SELECT
                    CASE
                        WHEN fc.schedule_type = 'minutes' THEN
                            (bt.now_sp + make_interval(mins => fc.delay_minutes::int))
                                AT TIME ZONE 'America/Sao_Paulo'
                        WHEN fc.schedule_type = 'weekday' THEN
                            CASE
                                WHEN EXTRACT(ISODOW FROM bt.now_sp)::int < fc.weekday::int
                                     OR (EXTRACT(ISODOW FROM bt.now_sp)::int = fc.weekday::int
                                         AND bt.now_sp::time < make_time(fc.hour::int, fc.minute::int, 0))
                                THEN
                                    (DATE_TRUNC('day', bt.now_sp)
                                    + make_interval(days => (fc.weekday::int - EXTRACT(ISODOW FROM bt.now_sp)::int))
                                    + make_interval(hours => fc.hour::int, mins => fc.minute::int))
                                        AT TIME ZONE 'America/Sao_Paulo'
                                ELSE
                                    (DATE_TRUNC('day', bt.now_sp)
                                    + make_interval(days => (7 - EXTRACT(ISODOW FROM bt.now_sp)::int + fc.weekday::int))
                                    + make_interval(hours => fc.hour::int, mins => fc.minute::int))
                                        AT TIME ZONE 'America/Sao_Paulo'
                            END
                        ELSE NULL
                    END as next_follow_ts,
                    fc.step_order,
                    fc.message_payload
                FROM follow_config fc, base_time bt
            ),
            updated AS (
                UPDATE customers c
                SET
                    last_message = (SELECT now_sp AT TIME ZONE 'America/Sao_Paulo' FROM base_time),
                    follow_up = 0,
                    next_follow = (SELECT next_follow_ts FROM next_follow_calc),
                    updated_at = (SELECT now_sp AT TIME ZONE 'America/Sao_Paulo' FROM base_time)
                WHERE c.id = :customer_id
                  AND c.deleted_at IS NULL
                RETURNING *
            )
            SELECT
                u.*,
                nfc.next_follow_ts,
                nfc.step_order as follow_up_order,
                nfc.message_payload as follow_up_payload
            FROM updated u
            LEFT JOIN next_follow_calc nfc ON true;
        """)

        result = await self.db.execute(
            query,
            {
                "customer_id": customer_id,
                "company_id": company_id,
                "sub_agent_id": sub_agent_id,
            },
        )
        row = result.fetchone()

        if not row:
            return None, None

        await self.db.commit()

        # Busca customer atualizado para retornar objeto ORM
        customer = await self.get_by_id(customer_id)

        # Extrai info do follow-up (inclui last_message para evitar dependência do cache ORM)
        follow_up_info: dict[str, Any] | None = None
        if row.follow_up_order is not None:
            follow_up_info = {
                "step_order": row.follow_up_order,
                "next_follow_ts": row.next_follow_ts,
                "message_payload": row.follow_up_payload,
                "last_message": row.last_message,
            }

        return customer, follow_up_info

    async def schedule_next_follow_up_step(
        self,
        customer_id: int,
        executed_step_order: int,
        last_message: Any,
    ) -> tuple[dict[str, Any] | None, Any]:
        """
        Agenda o próximo follow-up após o step atual ser executado.

        Busca o próximo follow-up (step_order = executed + 1) e:
        - Se time_reference = "now": NOW() + delay_minutes
        - Se time_reference = "last_message": last_message + delay_minutes
          - Se já passou, pula para o próximo step (loop)
        - Se não encontrar mais steps: seta next_follow = NULL

        NÃO atualiza last_message (deve permanecer igual).

        Args:
            customer_id: Customer ID
            executed_step_order: O step_order que acabou de ser executado
            last_message: Timestamp do last_message para cálculo baseado em last_message

        Returns:
            Tuple (dict com info do próximo follow-up, next_follow_ts)
            Retorna (None, None) se não há próximo follow-up
        """
        from datetime import UTC, datetime, timedelta
        from zoneinfo import ZoneInfo

        SP_TZ = ZoneInfo("America/Sao_Paulo")
        current_step = executed_step_order

        # Loop para encontrar o próximo follow-up válido
        while True:
            # Busca o próximo follow-up
            query = text("""
                SELECT
                    f.step_order,
                    f.schedule_type,
                    f.delay_minutes,
                    f.weekday,
                    f.hour,
                    f.minute,
                    f.message_payload,
                    f.time_reference,
                    NOW() AT TIME ZONE 'America/Sao_Paulo' AS now_sp
                FROM follow_ups f
                JOIN customers c ON c.id = :customer_id
                    AND c.deleted_at IS NULL
                    AND f.company_id = c.company_id
                    AND f.sub_agent_id = c.sub_agent_id
                WHERE f.step_order = :next_step_order
                LIMIT 1;
            """)

            result = await self.db.execute(
                query,
                {"customer_id": customer_id, "next_step_order": current_step + 1},
            )
            row = result.fetchone()

            if not row:
                # Não há mais follow-ups - atualiza follow_up e limpa next_follow
                # follow_up deve ser current_step (último step verificado/pulado)
                clear_query = text("""
                    UPDATE customers
                    SET
                        follow_up = :follow_up_value,
                        next_follow = NULL,
                        updated_at = NOW() AT TIME ZONE 'America/Sao_Paulo'
                    WHERE id = :customer_id
                      AND deleted_at IS NULL;
                """)
                await self.db.execute(
                    clear_query,
                    {"customer_id": customer_id, "follow_up_value": current_step},
                )
                await self.db.commit()
                return None, None

            # Calcula next_follow_ts baseado no time_reference
            time_reference = row.time_reference or "now"
            now_sp = row.now_sp
            delay_minutes = row.delay_minutes or 0

            # Se delay_minutes = 0, pula para o próximo step
            if delay_minutes == 0:
                current_step = row.step_order
                continue

            if time_reference == "last_message" and row.schedule_type == "minutes":
                # Baseado no last_message + delay_minutes
                # Garantir que last_message está em UTC
                if last_message.tzinfo is None:
                    last_message_utc = last_message.replace(tzinfo=UTC)
                else:
                    last_message_utc = last_message

                next_follow_ts = last_message_utc + timedelta(minutes=delay_minutes)

                # Verificar se já passou
                now_utc = datetime.now(UTC)
                if next_follow_ts <= now_utc:
                    # Já passou! Pula para o próximo step
                    current_step = row.step_order
                    continue

            else:
                # time_reference = "now" ou schedule_type = "weekday"
                # Usa a lógica original baseada em NOW()
                # now_sp é naive (sem timezone), precisa adicionar timezone de SP
                if now_sp.tzinfo is None:
                    now_sp = now_sp.replace(tzinfo=SP_TZ)

                if row.schedule_type == "minutes":
                    next_follow_ts = now_sp + timedelta(minutes=delay_minutes)
                elif row.schedule_type == "weekday":
                    # Lógica de weekday
                    now_dow = now_sp.isoweekday()
                    target_dow = row.weekday or 1
                    target_time = now_sp.replace(
                        hour=row.hour or 0,
                        minute=row.minute or 0,
                        second=0,
                        microsecond=0,
                    )

                    if now_dow < target_dow or (
                        now_dow == target_dow and now_sp.time() < target_time.time()
                    ):
                        days_ahead = target_dow - now_dow
                    else:
                        days_ahead = 7 - now_dow + target_dow

                    next_follow_ts = (
                        now_sp.replace(hour=0, minute=0, second=0, microsecond=0)
                        + timedelta(days=days_ahead)
                    ).replace(hour=row.hour or 0, minute=row.minute or 0)
                else:
                    next_follow_ts = now_sp

            # Encontrou um follow-up válido - atualiza customer
            # follow_up deve ser step_order - 1 para passar na validação do consumer
            # (consumer valida: customer.follow_up == step_order - 1)
            update_query = text("""
                UPDATE customers
                SET
                    follow_up = :follow_up_value,
                    next_follow = :next_follow_ts,
                    updated_at = NOW() AT TIME ZONE 'America/Sao_Paulo'
                WHERE id = :customer_id
                  AND deleted_at IS NULL;
            """)
            await self.db.execute(
                update_query,
                {
                    "customer_id": customer_id,
                    "follow_up_value": row.step_order - 1,
                    "next_follow_ts": next_follow_ts,
                },
            )
            await self.db.commit()

            return {
                "step_order": row.step_order,
                "message_payload": row.message_payload,
            }, next_follow_ts

    async def reset_customer(
        self,
        session_id: str,
        company_id: int,
    ) -> bool:
        """
        Reset customer to initial state for dev commands.

        Resets:
        - last_message = NULL
        - next_follow = NULL
        - follow_up = 0
        - sub_agent_id = first sub_agent of the agent (relative_id = 1)

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            True if customer was reset, False if not found
        """
        query = text("""
            UPDATE customers c
            SET
                last_message = NULL,
                next_follow = NULL,
                follow_up = 0,
                sub_agent_id = (
                    SELECT sa.id
                    FROM sub_agents sa
                    WHERE sa.agent_id = c.agent_id
                      AND sa.relative_id = 1
                      AND sa.deleted_at IS NULL
                    LIMIT 1
                ),
                updated_at = NOW() AT TIME ZONE 'America/Sao_Paulo'
            WHERE c."sessionId" = :session_id
              AND c.company_id = :company_id
              AND c.deleted_at IS NULL
            RETURNING c.id;
        """)

        result = await self.db.execute(
            query,
            {"session_id": session_id, "company_id": company_id},
        )
        row = result.fetchone()
        await self.db.commit()

        return row is not None

    async def set_dev_command_state(
        self,
        session_id: str,
        company_id: int,
        state: dict | None,
    ) -> bool:
        """
        Set dev command state for a customer.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            state: Dict with command and data, or None to clear
                   Ex: {"command": "mudar_agente", "agent_mapping": {"1": 123}}

        Returns:
            True if customer was updated, False if not found
        """
        query = text("""
            UPDATE customers
            SET
                dev_command_state = :state,
                updated_at = NOW() AT TIME ZONE 'America/Sao_Paulo'
            WHERE "sessionId" = :session_id
              AND company_id = :company_id
              AND deleted_at IS NULL
            RETURNING id;
        """)

        result = await self.db.execute(
            query,
            {
                "session_id": session_id,
                "company_id": company_id,
                "state": json.dumps(state) if state else None,
            },
        )
        row = result.fetchone()
        await self.db.commit()
        return row is not None

    async def change_agent_and_reset(
        self,
        session_id: str,
        company_id: int,
        new_agent_id: int,
    ) -> bool:
        """
        Change agent_id and reset customer state (similar to reset_customer).

        Resets:
        - agent_id = new_agent_id
        - sub_agent_id = first sub_agent of the NEW agent (relative_id=1)
        - last_message = NULL
        - next_follow = NULL
        - follow_up = 0
        - dev_command_state = NULL

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            new_agent_id: ID of the new agent

        Returns:
            True if customer was updated, False if not found
        """
        query = text("""
            UPDATE customers c
            SET
                agent_id = :new_agent_id,
                last_message = NULL,
                next_follow = NULL,
                follow_up = 0,
                sub_agent_id = (
                    SELECT sa.id
                    FROM sub_agents sa
                    WHERE sa.agent_id = :new_agent_id
                      AND sa.relative_id = 1
                      AND sa.deleted_at IS NULL
                    LIMIT 1
                ),
                dev_command_state = NULL,
                updated_at = NOW() AT TIME ZONE 'America/Sao_Paulo'
            WHERE c."sessionId" = :session_id
              AND c.company_id = :company_id
              AND c.deleted_at IS NULL
            RETURNING c.id;
        """)

        result = await self.db.execute(
            query,
            {
                "session_id": session_id,
                "company_id": company_id,
                "new_agent_id": new_agent_id,
            },
        )
        row = result.fetchone()
        await self.db.commit()
        return row is not None
