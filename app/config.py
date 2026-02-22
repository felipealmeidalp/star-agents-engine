"""Application configuration management using Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignora variáveis extras no .env
    )

    # App
    app_name: str = "star-agents"

    # Database (using Supabase Session Pooler - no local pooling needed)
    database_url: str
    db_echo: bool = False

    # Security
    api_key: str

    # Qdrant (RAG)
    qdrant_url: str

    # Redis (para buffer de mensagens)
    redis_url: str = "redis://localhost:6379"

    # RabbitMQ (para follow-ups com delay)
    rabbit_url: str = "amqp://localhost:5672/"
    rabbit_user: str = "guest"
    rabbit_pass: str = "guest"
    rabbit_connection_timeout: int = 10
    rabbit_heartbeat: int = 60
    rabbit_follow_up_queue: str = "follow_ups"

    # App behavior
    debug: bool = False
    dev_mode: bool = False  # Bypass buffer e outras features para dev local
    message_buffer_delay: int = 5  # Segundos para aguardar no buffer de mensagens
    max_chat_history_messages: int = 10

    # Meta WhatsApp webhook
    meta_app_secret: str = ""          # Facebook App Secret para X-Hub-Signature-256
    meta_verify_token: str = ""        # hub.verify_token para verificação GET
    meta_dedup_ttl: int = 3600         # TTL dedup keys no Redis (1h)
    meta_forward_timeout: int = 10     # Timeout forward pro Chatwoot (s)
    meta_cache_ttl: int = 300          # TTL cache imbox/company no Redis (5min)

    # Alerting (WhatsApp via Evolution API)
    alert_enabled: bool = True
    alert_evo_api_url: str = "https://evo.starbots.com.br"
    alert_evo_api_key: str = "278CBA42321E-4D72-8355-73F2DC7BB446"
    alert_evo_instance: str = "report"
    alert_phone_number: str = "5551995567237"
    alert_rate_limit_seconds: int = 300  # 5 minutos

    # Internal (não exposto no .env)
    tool_execution_timeout: int = 300  # 5 minutos
    openai_timeout: int = 300  # 5 minutos


# Singleton instance
settings = Settings()
