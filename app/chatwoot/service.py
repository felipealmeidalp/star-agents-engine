"""Main service for Chatwoot webhook processing."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatwoot.buffer import MessageBuffer
from app.chatwoot.client import ChatwootClient
from app.chatwoot.schemas import ChatwootAttachment, ChatwootWebhookPayload
from app.models.tables import Agent, Company, Customer
from app.rabbitmq import get_follow_up_publisher
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.services.openai import OpenAIService
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

# Module-level singleton so all ChatwootService instances share the same
# RequestManager (and its locks/active_requests state).
_request_manager: Any = None


def get_request_manager() -> Any:
    """Get the singleton RequestManager instance."""
    global _request_manager
    if _request_manager is None:
        from app.services.request_manager import RequestManager
        _request_manager = RequestManager()
    return _request_manager


class ChatwootService:
    """Orchestrates Chatwoot webhook processing."""

    # Dev commands that can be used when agent status is "dev"
    DEV_COMMANDS = {"#resetar", "#mudar_agente"}

    def __init__(self, db: AsyncSession) -> None:
        """Initialize service with database session."""
        self.db = db
        self.customer_repo = CustomerRepository(db)
        self.company_repo = CompanyRepository(db)
        self.chat_history_repo = ChatHistoryRepository(db)
        self.buffer = MessageBuffer()
        self.client = ChatwootClient()
        self.request_manager = get_request_manager()

    async def process_webhook(
        self,
        payload: ChatwootWebhookPayload,
        company: Company,
    ) -> dict[str, Any]:
        """
        Process a Chatwoot webhook event.

        Flow:
        1. Get or create customer (WITHOUT updating follow-up)
        2. If existing customer: check dev commands FIRST
        3. Handle attachments (audio transcription, unsupported rejection)
        4. Update follow-up + schedule RabbitMQ (only if not dev command)
        5. Buffer, process chat, send responses

        Args:
            payload: Validated webhook payload
            company: Company resolved from webhook token

        Returns:
            Dict with processing result

        Raises:
            ValueError: If configuration not found
        """
        # Extrair atributos ORM em variáveis locais ANTES de qualquer operação async,
        # para evitar lazy load fora do greenlet context do SQLAlchemy.
        cw_base_url = company.cw_base_url
        cw_api_key = company.cw_apikey

        logger.info(
            f"[ChatwootService] Processing webhook: event={payload.event}, "
            f"message_type={payload.message_type}, sender_id={payload.sender.id}, "
            f"company_id={company.id}"
        )

        message = payload.content or ""

        # 1. Get or create customer (WITHOUT updating follow-up)
        customer, is_new = await self._get_or_create_customer_only(
            payload=payload,
            company=company,
        )

        logger.info(
            f"[ChatwootService] Customer: id={customer.id}, "
            f"session={customer.sessionId}, is_new={is_new}"
        )

        # 2. If existing customer, check dev commands FIRST (before follow-up)
        if not is_new:
            dev_command_result = await self._handle_dev_command(
                message=message,
                customer=customer,
                company=company,
                payload=payload,
            )
            if dev_command_result:
                # Dev command executed - return early WITHOUT scheduling follow-up
                logger.info(
                    "[ChatwootService] Dev command executed, skipping follow-up"
                )
                return dev_command_result

        # 3. Handle attachments (audio transcription, unsupported types)
        message, should_continue = await self._handle_attachments(
            message=message,
            payload=payload,
            company=company,
        )
        if not should_continue:
            return {
                "status": "attachment_handled",
                "session_id": customer.sessionId,
            }

        # 4. Update follow-up + schedule RabbitMQ (only reaches here if not dev command)
        await self._update_follow_up_and_schedule(
            customer=customer,
            company=company,
            payload=payload,
            is_new=is_new,
        )

        # 5. Delegate to RequestManager (handles buffering + cancellation)
        logger.info("[ChatwootService] Delegating to RequestManager...")

        async def send_messages_to_lead(messages: list[str]) -> None:
            """Callback to send messages to lead before tool execution."""
            await self._send_responses(
                messages=messages,
                base_url=cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=cw_api_key,
            )

        response = await self.request_manager.on_new_message(
            contact_id=payload.sender.id,
            message=message,
            session_id=customer.sessionId,
            company_id=company.id,
            db=self.db,
            on_send_messages=send_messages_to_lead,
        )

        # If None, message was discarded (newer message in buffer)
        if response is None:
            logger.info(
                f"[ChatwootService] Message discarded for contact {payload.sender.id} "
                "(newer message in buffer or task cancelled)"
            )
            return {
                "status": "buffered",
                "reason": "newer_message_pending",
                "session_id": customer.sessionId,
            }

        logger.info(f"[ChatwootService] Chat response: {response}")

        # 6. Send responses back to Chatwoot
        messages = response.get("resposta", [])
        if messages:
            await self._send_responses(
                messages=messages,
                base_url=cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=cw_api_key,
            )

        return {
            "status": "processed",
            "session_id": customer.sessionId,
            "messages_sent": len(messages),
        }

    async def _get_or_create_customer_only(
        self,
        payload: ChatwootWebhookPayload,
        company: Company,
    ) -> tuple[Customer, bool]:
        """
        Get existing customer or create new one WITHOUT updating follow-up.

        Args:
            payload: Webhook payload
            company: Company instance

        Returns:
            Tuple of (Customer, is_new: bool)
        """
        cw_contact_id = payload.sender.id

        customer, is_new = await self.customer_repo.get_or_create_from_chatwoot(
            cw_contact_id=cw_contact_id,
            cw_conversation_id=payload.conversation.id,
            company_id=company.id,
            agent_id=company.standard_agent_id,
            sub_agent_id=company.standard_sub_agent_id,
            name=payload.sender.name,
            avatar=payload.sender.thumbnail,
        )

        if is_new:
            logger.info(
                "[ChatwootService] Created new customer for contact %d",
                cw_contact_id,
            )

        return customer, is_new

    async def _update_follow_up_and_schedule(
        self,
        customer: Customer,
        company: Company,
        payload: ChatwootWebhookPayload,
        is_new: bool,
    ) -> None:
        """
        Update follow-up tracking and schedule in RabbitMQ.

        Called AFTER dev command check passes (i.e., not a dev command).

        Args:
            customer: Customer instance
            company: Company instance
            payload: Webhook payload
            is_new: Whether customer was just created
        """
        if is_new:
            # Initialize follow-up for new customer
            updated_customer, follow_up_info = await self.customer_repo.initialize_follow_up(
                customer_id=customer.id,
                company_id=company.id,
                sub_agent_id=company.standard_sub_agent_id,
            )
            log_prefix = "[FollowUp Debug] NEW CUSTOMER"
        else:
            # Update follow-up for existing customer
            updated_customer, follow_up_info = await self.customer_repo.update_follow_up_on_message(
                cw_contact_id=customer.cw_contact_id,
                company_id=company.id,
            )
            log_prefix = "[FollowUp Debug]"

        if updated_customer:
            customer = updated_customer

        # Log follow-up debug info
        msg_payload = follow_up_info.get("message_payload") if follow_up_info else None
        logger.info(
            f"{log_prefix} "
            f"company_id={company.id}, "
            f"customer_id={customer.id}, "
            f"sub_agent_id={customer.sub_agent_id}, "
            f"step_order={follow_up_info.get('step_order') if follow_up_info else None}, "
            f"last_message={customer.last_message if updated_customer else 'N/A'}, "
            f"next_follow={customer.next_follow if updated_customer else 'N/A'}, "
            f"message_payload={msg_payload}"
        )

        # Schedule follow-up in RabbitMQ if configured
        if follow_up_info and follow_up_info.get("next_follow_ts"):
            await self._schedule_follow_up(
                company=company,
                customer=customer,
                payload=payload,
                follow_up_info=follow_up_info,
            )

    async def _send_responses(
        self,
        messages: list[str],
        base_url: str | None,
        account_id: int,
        conversation_id: int,
        api_key: str | None,
    ) -> None:
        """Send response messages to Chatwoot."""
        if not api_key:
            logger.error("[ChatwootService] No Chatwoot API key configured")
            return

        if not base_url:
            logger.error("[ChatwootService] No Chatwoot base URL configured")
            return

        logger.info(
            f"[ChatwootService] Sending {len(messages)} messages to Chatwoot"
        )

        try:
            await self.client.send_messages(
                base_url=base_url,
                account_id=account_id,
                conversation_id=conversation_id,
                messages=messages,
                api_key=api_key,
            )
        except Exception as e:
            logger.error(f"[ChatwootService] Failed to send to Chatwoot: {e}")
            send_critical_alert(
                "CHATWOOT_SEND_FAILED",
                "chatwoot/service.py:_send_responses",
                e,
            )

    async def _schedule_follow_up(
        self,
        company: Company,
        customer: Customer,
        payload: ChatwootWebhookPayload,
        follow_up_info: dict[str, Any],
    ) -> None:
        """
        Schedule follow-up message in RabbitMQ.

        Args:
            company: Company for context
            customer: Customer to send follow-up to
            payload: Original webhook payload (for conversation context)
            follow_up_info: Dict with step_order, next_follow_ts, message_payload
        """
        try:
            publisher = get_follow_up_publisher()
            await publisher.publish_follow_up(
                customer_id=customer.id,
                company_id=company.id,
                cw_conversation_id=payload.conversation.id,
                step_order=follow_up_info["step_order"],
                message_payload=follow_up_info["message_payload"] or [],
                last_message=follow_up_info["last_message"],
                next_follow=follow_up_info["next_follow_ts"],
            )
        except Exception as e:
            # Log but don't fail the request - follow-up is nice-to-have
            logger.error(
                f"[ChatwootService] Failed to schedule follow-up: {e}. "
                f"customer_id={customer.id}, company_id={company.id}"
            )
            send_critical_alert(
                "FOLLOWUP_SCHEDULE_FAILED",
                "chatwoot/service.py:_schedule_follow_up",
                e,
                contact_id=customer.id,
                company_id=company.id,
            )

    async def _handle_attachments(
        self,
        message: str,
        payload: ChatwootWebhookPayload,
        company: Company,
    ) -> tuple[str, bool]:
        """
        Handle message attachments (audio transcription, unsupported types).

        Args:
            message: Current message text (may be empty)
            payload: Webhook payload with attachments
            company: Company for API keys and Chatwoot config

        Returns:
            Tuple of (message_text, should_continue).
            should_continue=False means caller should return early.
        """
        # Text has precedence — if there's content, process normally
        if message.strip():
            return message, True

        attachments = payload.attachments or []

        # No content AND no attachments → skip silently
        if not attachments:
            logger.warning(
                "[ChatwootService] Empty message with no attachments, skipping. "
                "sender=%s, company=%s",
                payload.sender.id if payload.sender else "?",
                company.id,
            )
            return message, False

        # Check for audio attachment (use the first one found)
        audio_attachment: ChatwootAttachment | None = None
        non_audio_attachment: ChatwootAttachment | None = None

        for att in attachments:
            if att.file_type == "audio":
                audio_attachment = att
                break
            elif non_audio_attachment is None:
                non_audio_attachment = att

        # Case 1: Audio attachment → transcribe
        if audio_attachment:
            transcribed = await self._transcribe_audio_attachment(
                attachment=audio_attachment,
                payload=payload,
                company=company,
            )
            if transcribed:
                return transcribed, True
            # Transcription failed or empty — error message already sent to client
            return message, False

        # Case 2: Non-audio attachment → describe for AI
        if non_audio_attachment:
            file_type = non_audio_attachment.file_type or "file"

            # Map file_type to descriptive message
            type_labels = {
                "image": "uma imagem",
                "video": "um vídeo",
            }

            if file_type in type_labels:
                description = type_labels[file_type]
            else:
                # Try to extract extension from data_url for more specific description
                ext = ""
                if non_audio_attachment.data_url:
                    from urllib.parse import urlparse
                    path = urlparse(non_audio_attachment.data_url).path
                    if "." in path:
                        ext = path.rsplit(".", 1)[-1].upper()

                if ext and len(ext) <= 5:
                    description = f"um arquivo {ext}"
                else:
                    description = "um arquivo"

            descriptive_message = f"O usuário enviou {description}"

            logger.info(
                "[ChatwootService] Non-audio attachment '%s' → forwarding as AI input: '%s'. "
                "sender=%s, company=%s",
                file_type,
                descriptive_message,
                payload.sender.id if payload.sender else "?",
                company.id,
            )

            return descriptive_message, True

        return message, False

    async def _transcribe_audio_attachment(
        self,
        attachment: ChatwootAttachment,
        payload: ChatwootWebhookPayload,
        company: Company,
    ) -> str | None:
        """
        Transcribe an audio attachment using Whisper API.

        Args:
            attachment: Audio attachment with data_url
            payload: Webhook payload for context
            company: Company for API key

        Returns:
            Transcribed text, or None if transcription failed/empty
        """
        if not attachment.data_url:
            logger.warning(
                "[ChatwootService] Audio attachment has no data_url, sender=%s",
                payload.sender.id if payload.sender else "?",
            )
            await self._send_responses(
                messages=[
                    "Desculpa, tive um problema ao processar seu áudio. "
                    "Você pode tentar enviar novamente ou digitar a mensagem?"
                ],
                base_url=company.cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=company.cw_apikey,
            )
            return None

        try:
            api_key = await self.company_repo.get_openai_api_key(company.id)
            openai_service = OpenAIService(api_key=api_key)
            text = await openai_service.transcribe_audio(attachment.data_url)

            if not text.strip():
                logger.warning(
                    "[ChatwootService] Audio transcription returned empty text, sender=%s",
                    payload.sender.id if payload.sender else "?",
                )
                await self._send_responses(
                    messages=[
                        "Não consegui entender o áudio. Você pode tentar enviar "
                        "novamente ou digitar a mensagem?"
                    ],
                    base_url=company.cw_base_url,
                    account_id=payload.account.id,
                    conversation_id=payload.conversation.id,
                    api_key=company.cw_apikey,
                )
                return None

            logger.info(
                "[ChatwootService] Audio transcribed successfully: %d chars, sender=%s",
                len(text),
                payload.sender.id if payload.sender else "?",
            )
            return text

        except Exception as e:
            logger.error(
                "[ChatwootService] Audio transcription failed: %s, sender=%s",
                e,
                payload.sender.id if payload.sender else "?",
            )

            await self._send_responses(
                messages=[
                    "Desculpa, tive um problema ao processar seu áudio. "
                    "Você pode tentar enviar novamente ou digitar a mensagem?"
                ],
                base_url=company.cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=company.cw_apikey,
            )

            cw_link = (
                f"{company.cw_base_url}/app/accounts/{payload.account.id}"
                f"/conversations/{payload.conversation.id}"
            ) if company.cw_base_url else "N/A"

            send_critical_alert(
                "AUDIO_TRANSCRIPTION_FAILED",
                "chatwoot/service.py:_transcribe_audio_attachment",
                e,
                contact_id=payload.sender.id if payload.sender else None,
                company_id=company.id,
                extra=cw_link,
            )
            return None

    async def _handle_dev_command(
        self,
        message: str,
        customer: Customer,
        company: Company,
        payload: ChatwootWebhookPayload,
    ) -> dict[str, Any] | None:
        """
        Handle dev commands when agent is in dev mode.

        Flow:
        1. Check if agent is in dev mode
        2. If dev: check dev_command_state (pending selection) or new command
        3. If not dev: return None (continue normal flow)

        Args:
            message: The message content
            customer: Customer instance
            company: Company instance
            payload: Webhook payload for sending responses

        Returns:
            Dict with result if command was handled, None otherwise
        """
        # 1. Check if agent is in dev mode
        if not customer.agent_id:
            return None

        result = await self.db.execute(
            select(Agent.status).where(
                Agent.id == customer.agent_id,
                Agent.deleted_at.is_(None),
            )
        )
        agent_status = result.scalar_one_or_none()

        if agent_status != "dev":
            # Not in dev mode - if there was a pending state, clear it
            if customer.dev_command_state:
                logger.info(
                    f"[ChatwootService] Clearing stale dev_command_state - "
                    f"agent {customer.agent_id} is no longer in dev mode"
                )
                await self.customer_repo.set_dev_command_state(
                    session_id=customer.sessionId,
                    company_id=company.id,
                    state=None,
                )
            return None

        # 2. Agent is in dev mode - check for pending state
        if customer.dev_command_state:
            return await self._handle_pending_dev_command(
                message=message,
                customer=customer,
                company=company,
                payload=payload,
            )

        # 3. No pending state - check if message is a new dev command
        message_lower = message.strip().lower()
        if message_lower not in self.DEV_COMMANDS:
            return None

        logger.info(
            f"[ChatwootService] Processing dev command '{message_lower}' "
            f"for customer {customer.id}"
        )

        # Handle #resetar command
        if message_lower == "#resetar":
            return await self._execute_reset_command(customer, company, payload)

        # Handle #mudar_agente command
        if message_lower == "#mudar_agente":
            return await self._execute_mudar_agente_command(customer, company, payload)

        return None

    async def _handle_pending_dev_command(
        self,
        message: str,
        customer: Customer,
        company: Company,
        payload: ChatwootWebhookPayload,
    ) -> dict[str, Any] | None:
        """
        Process input for a pending dev command.

        Args:
            message: The message content (user's selection/input)
            customer: Customer instance
            company: Company instance
            payload: Webhook payload for sending responses

        Returns:
            Dict with result if command was handled, None otherwise
        """
        state = customer.dev_command_state or {}
        command = state.get("command")

        if command == "mudar_agente":
            return await self._handle_agent_selection_input(
                message=message,
                customer=customer,
                company=company,
                payload=payload,
            )

        # Unknown command - clear state
        logger.warning(f"[ChatwootService] Unknown dev_command: {command}")
        await self.customer_repo.set_dev_command_state(
            session_id=customer.sessionId,
            company_id=company.id,
            state=None,
        )
        return None

    async def _execute_mudar_agente_command(
        self,
        customer: Customer,
        company: Company,
        payload: ChatwootWebhookPayload,
    ) -> dict[str, Any]:
        """
        Execute #mudar_agente command - show list of agents.

        Args:
            customer: Customer instance
            company: Company instance
            payload: Webhook payload for sending responses

        Returns:
            Dict with execution result
        """
        from app.repositories.agent import AgentRepository

        agent_repo = AgentRepository(self.db)
        agents = await agent_repo.list_agents_by_company(company.id)

        if not agents:
            await self._send_responses(
                messages=["Nenhum agente disponivel para esta empresa."],
                base_url=company.cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=company.cw_apikey,
            )
            return {
                "status": "dev_command_executed",
                "command": "#mudar_agente",
                "result": "no_agents",
            }

        # Build mapping and list
        agent_mapping = {}
        lines = []
        for idx, agent in enumerate(agents, start=1):
            agent_mapping[str(idx)] = agent["id"]
            marker = " (atual)" if agent["id"] == customer.agent_id else ""
            lines.append(f"- {idx} {agent['name']}{marker}")

        # Save state
        await self.customer_repo.set_dev_command_state(
            session_id=customer.sessionId,
            company_id=company.id,
            state={"command": "mudar_agente", "agent_mapping": agent_mapping},
        )

        # Send message
        msg = (
            "*Para qual agente voce gostaria de mudar?*\n\n"
            + "\n".join(lines)
            + "\n- 0 Cancelar"
        )
        await self._send_responses(
            messages=[msg],
            base_url=company.cw_base_url,
            account_id=payload.account.id,
            conversation_id=payload.conversation.id,
            api_key=company.cw_apikey,
        )

        logger.info(
            f"[ChatwootService] #mudar_agente: listed {len(agents)} agents "
            f"for customer {customer.id}"
        )

        return {
            "status": "dev_command_pending",
            "command": "#mudar_agente",
            "agents_shown": len(agents),
        }

    async def _handle_agent_selection_input(
        self,
        message: str,
        customer: Customer,
        company: Company,
        payload: ChatwootWebhookPayload,
    ) -> dict[str, Any]:
        """
        Process agent selection input for #mudar_agente.

        Args:
            message: The user's selection (number)
            customer: Customer instance
            company: Company instance
            payload: Webhook payload for sending responses

        Returns:
            Dict with execution result
        """
        from app.repositories.agent import AgentRepository

        choice = message.strip()
        state = customer.dev_command_state or {}
        agent_mapping = state.get("agent_mapping", {})

        # Cancel
        if choice == "0":
            await self.customer_repo.set_dev_command_state(
                session_id=customer.sessionId,
                company_id=company.id,
                state=None,
            )
            await self._send_responses(
                messages=["_Operacao cancelada. Nenhum historico apagado\nContinue Normalmente_"],
                base_url=company.cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=company.cw_apikey,
            )
            logger.info(
                f"[ChatwootService] #mudar_agente cancelled for customer {customer.id}"
            )
            return {
                "status": "dev_command_executed",
                "command": "#mudar_agente",
                "result": "cancelled",
            }

        # Invalid option
        if choice not in agent_mapping:
            # Reload list (may have changed)
            agent_repo = AgentRepository(self.db)
            agents = await agent_repo.list_agents_by_company(company.id)

            new_mapping = {}
            lines = []
            for idx, agent in enumerate(agents, start=1):
                new_mapping[str(idx)] = agent["id"]
                marker = " (atual)" if agent["id"] == customer.agent_id else ""
                lines.append(f"- {idx} {agent['name']}{marker}")

            await self.customer_repo.set_dev_command_state(
                session_id=customer.sessionId,
                company_id=company.id,
                state={"command": "mudar_agente", "agent_mapping": new_mapping},
            )

            msg = (
                f"*Opcao invalida: {choice}*\n\n"
                "*Para qual agente voce gostaria de mudar?*\n\n"
                + "\n".join(lines)
                + "\n- 0 Cancelar"
            )
            await self._send_responses(
                messages=[msg],
                base_url=company.cw_base_url,
                account_id=payload.account.id,
                conversation_id=payload.conversation.id,
                api_key=company.cw_apikey,
            )
            logger.info(
                f"[ChatwootService] #mudar_agente invalid selection '{choice}' "
                f"for customer {customer.id}"
            )
            return {
                "status": "dev_command_pending",
                "command": "#mudar_agente",
                "error": "invalid_selection",
            }

        # Valid option - execute change
        new_agent_id = agent_mapping[choice]

        # Get agent name (DB uses 'title' column)
        from sqlalchemy import text
        result = await self.db.execute(
            text("SELECT title FROM agents WHERE id = :agent_id"),
            {"agent_id": new_agent_id},
        )
        row = result.fetchone()
        agent_name = row.title if row else "Desconhecido"

        # Delete chat history
        deleted = await self.chat_history_repo.delete_by_session(
            session_id=customer.sessionId,
            company_id=company.id,
        )

        # Change agent and reset
        await self.customer_repo.change_agent_and_reset(
            session_id=customer.sessionId,
            company_id=company.id,
            new_agent_id=new_agent_id,
        )

        # Send confirmation
        msg = (
            f"*Agente alterado para {agent_name}!*\n\n"
            f"• Historico limpo: {deleted} mensagens\n"
            "• Estado restaurado ao inicial\n\n"
            "_Voce pode iniciar uma nova conversa._"
        )
        await self._send_responses(
            messages=[msg],
            base_url=company.cw_base_url,
            account_id=payload.account.id,
            conversation_id=payload.conversation.id,
            api_key=company.cw_apikey,
        )

        logger.info(
            f"[ChatwootService] #mudar_agente completed: "
            f"customer={customer.id}, new_agent_id={new_agent_id}, "
            f"agent_name={agent_name}, deleted_messages={deleted}"
        )

        return {
            "status": "dev_command_executed",
            "command": "#mudar_agente",
            "result": "changed",
            "new_agent_id": new_agent_id,
        }

    async def _execute_reset_command(
        self,
        customer: Customer,
        company: Company,
        payload: ChatwootWebhookPayload,
    ) -> dict[str, Any]:
        """
        Execute the #resetar dev command.

        Deletes all chat history and resets customer to initial state.

        Args:
            customer: Customer to reset
            company: Company for context
            payload: Webhook payload for sending confirmation message

        Returns:
            Dict with execution result
        """
        # Delete all chat history for this customer
        deleted_messages = await self.chat_history_repo.delete_by_session(
            session_id=customer.sessionId,
            company_id=company.id,
        )

        # Reset customer state
        reset_success = await self.customer_repo.reset_customer(
            session_id=customer.sessionId,
            company_id=company.id,
        )

        logger.info(
            f"[ChatwootService] Reset command executed: "
            f"customer_id={customer.id}, "
            f"deleted_messages={deleted_messages}, "
            f"customer_reset={reset_success}"
        )

        # Send confirmation message to Chatwoot
        confirmation_message = (
            "✅ *Sessão resetada com sucesso!*\n\n"
            f"• Histórico limpo: {deleted_messages} mensagens removidas\n"
            "• Estado do cliente restaurado ao inicial\n\n"
            "_Você pode iniciar uma nova conversa agora._"
        )

        await self._send_responses(
            messages=[confirmation_message],
            base_url=company.cw_base_url,
            account_id=payload.account.id,
            conversation_id=payload.conversation.id,
            api_key=company.cw_apikey,
        )

        return {
            "status": "dev_command_executed",
            "command": "#resetar",
            "session_id": customer.sessionId,
            "deleted_messages": deleted_messages,
            "customer_reset": reset_success,
        }
