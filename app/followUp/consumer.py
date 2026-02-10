"""Consumer for follow-up messages from RabbitMQ."""

import json
import logging
from datetime import UTC, datetime

from aio_pika.abc import AbstractIncomingMessage

from app.chatwoot.client import ChatwootClient
from app.config import settings
from app.db.database import AsyncSessionLocal
from app.rabbitmq.connection import get_rabbitmq_channel
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.utils.content_formatter import format_content_for_storage

logger = logging.getLogger(__name__)


async def on_follow_up_message(message: AbstractIncomingMessage) -> None:
    """
    Callback executado quando mensagem chega na work queue.

    Fluxo:
    1. Valida se last_message ainda é o mesmo (lead não enviou nova mensagem)
    2. Valida se step_order está correto (follow-up não foi processado antes)
    3. Envia mensagens via Chatwoot com delay humanizado
    4. Salva no chat_history com is_follow_up=True

    Args:
        message: Mensagem recebida do RabbitMQ
    """
    async with message.process():
        data = json.loads(message.body)
        customer_id = data.get("customer_id")
        company_id = data.get("company_id")
        cw_conversation_id = data.get("cw_conversation_id")
        step_order = data.get("step_order")
        message_payload = data.get("message_payload", [])
        last_message_str = data.get("last_message")

        logger.info(
            f"[FollowUp Consumer] Processing follow-up: "
            f"customer={customer_id}, step={step_order}"
        )

        async with AsyncSessionLocal() as db:
            # Repositórios
            customer_repo = CustomerRepository(db)
            company_repo = CompanyRepository(db)
            chat_history_repo = ChatHistoryRepository(db)

            # 1. Buscar customer
            customer = await customer_repo.get_by_id(customer_id)
            if not customer:
                logger.warning(
                    f"[FollowUp Consumer] Customer {customer_id} not found, discarding"
                )
                return

            # 2. VALIDAÇÃO 1: last_message (comparação exata com microsegundos)
            event_last_message = datetime.fromisoformat(
                last_message_str.replace("Z", "+00:00")
            )
            # Garantir que customer.last_message está em UTC para comparação
            if customer.last_message is None:
                logger.info(
                    f"[FollowUp Consumer] Customer {customer_id} has no last_message, "
                    "discarding"
                )
                return

            customer_last_message = (
                customer.last_message.replace(tzinfo=UTC)
                if customer.last_message.tzinfo is None
                else customer.last_message
            )

            if customer_last_message != event_last_message:
                logger.info(
                    f"[FollowUp Consumer] Customer {customer_id} sent new message "
                    f"(db={customer_last_message}, event={event_last_message}), "
                    "discarding follow-up"
                )
                return

            # 3. VALIDAÇÃO 2: step_order
            expected_follow_up = step_order - 1
            current_follow_up = customer.follow_up or 0

            if current_follow_up != expected_follow_up:
                logger.info(
                    f"[FollowUp Consumer] Follow-up step mismatch for customer "
                    f"{customer_id} (expected={expected_follow_up}, "
                    f"current={current_follow_up}), discarding"
                )
                return

            # 4. VALIDAÇÃO 3: next_follow não pode ser NULL (fluxo encerrado)
            if customer.next_follow is None:
                logger.info(
                    f"[FollowUp Consumer] Customer {customer_id} has next_follow=NULL "
                    "(flow ended), discarding"
                )
                return

            # 5. VALIDAÇÃO 4: dev_command_state deve ser NULL
            # Se estiver preenchido, usuário está no meio de um comando dev (#mudar_agente)
            if customer.dev_command_state is not None:
                dev_command_retry_count = data.get("dev_command_retry_count", 0)
                max_retries = 3

                if dev_command_retry_count >= max_retries:
                    logger.info(
                        f"[FollowUp Consumer] Customer {customer_id} has dev_command_state "
                        f"after {max_retries} retries, discarding follow-up"
                    )
                    return

                # Re-schedule com 5 minutos de delay
                logger.info(
                    f"[FollowUp Consumer] Customer {customer_id} has dev_command_state active, "
                    f"rescheduling with 5min delay (retry {dev_command_retry_count + 1}/{max_retries})"
                )

                from datetime import timedelta
                from app.rabbitmq import get_follow_up_publisher

                publisher = get_follow_up_publisher()
                await publisher.publish_follow_up(
                    customer_id=customer_id,
                    company_id=company_id,
                    cw_conversation_id=cw_conversation_id,
                    step_order=step_order,
                    message_payload=message_payload,
                    last_message=event_last_message,
                    next_follow=datetime.now(UTC) + timedelta(minutes=5),
                    dev_command_retry_count=dev_command_retry_count + 1,
                )
                return

            # 6. Buscar company para credenciais Chatwoot
            company = await company_repo.get_by_id(company_id)
            if not company:
                logger.error(
                    f"[FollowUp Consumer] Company {company_id} not found, discarding"
                )
                return

            if not company.cw_base_url or not company.cw_apikey or not company.cw_account_id:
                logger.error(
                    f"[FollowUp Consumer] Company {company_id} missing Chatwoot config "
                    f"(base_url={company.cw_base_url}, apikey={'set' if company.cw_apikey else 'missing'}, "
                    f"account_id={company.cw_account_id}), discarding"
                )
                return

            # 5. Enviar mensagens via Chatwoot
            client = ChatwootClient()
            messages = message_payload if isinstance(message_payload, list) else [message_payload]

            logger.info(
                f"[FollowUp Consumer] Sending {len(messages)} message(s) to "
                f"conversation {cw_conversation_id}"
            )

            results = await client.send_messages(
                base_url=company.cw_base_url,
                account_id=company.cw_account_id,
                conversation_id=cw_conversation_id,
                messages=messages,
                api_key=company.cw_apikey,
            )

            # Verificar se houve erro no envio
            errors = [r for r in results if "error" in r]
            if errors:
                logger.error(
                    f"[FollowUp Consumer] Some messages failed to send: {errors}"
                )
                # Continua para salvar no histórico mesmo com erros parciais

            # 6. Formatar e salvar no chat_history
            content = format_content_for_storage(json.dumps({"resposta": messages}))

            await chat_history_repo.insert_follow_up_message(
                session_id=customer.sessionId,
                company_id=company_id,
                agent_id=customer.agent_id,
                sub_agent_id=customer.sub_agent_id,
                content=content,
            )

            # 7. Agendar próximo follow-up se existir
            next_follow_info, next_follow_ts = (
                await customer_repo.schedule_next_follow_up_step(
                    customer_id=customer_id,
                    executed_step_order=step_order,
                    last_message=event_last_message,
                )
            )

            if next_follow_info and next_follow_ts:
                from app.rabbitmq import get_follow_up_publisher

                publisher = get_follow_up_publisher()
                await publisher.publish_follow_up(
                    customer_id=customer_id,
                    company_id=company_id,
                    cw_conversation_id=cw_conversation_id,
                    step_order=next_follow_info["step_order"],
                    message_payload=next_follow_info["message_payload"] or [],
                    last_message=event_last_message,  # MESMO do evento, NÃO atualiza
                    next_follow=next_follow_ts,
                )
                logger.info(
                    f"[FollowUp Consumer] Follow-up step {step_order} sent successfully. "
                    f"Scheduled next step {next_follow_info['step_order']} for "
                    f"customer {customer_id}, next_follow={next_follow_ts}"
                )
            else:
                logger.info(
                    f"[FollowUp Consumer] Follow-up step {step_order} sent successfully. "
                    f"No more follow-ups for customer {customer_id}"
                )


async def start_follow_up_consumer() -> None:
    """
    Inicia consumer para a fila de follow-ups.

    Este consumer escuta a fila de trabalho (não a de delay).
    Mensagens chegam aqui após o TTL expirar na fila de delay.
    """
    queue_name = settings.rabbit_follow_up_queue

    channel = await get_rabbitmq_channel()

    # Declara a fila se não existir (idempotente)
    queue = await channel.declare_queue(
        queue_name,
        durable=True,
    )

    # Inicia consumo
    await queue.consume(on_follow_up_message)

    logger.info(f"[FollowUp Consumer] Listening on queue: {queue_name}")
