"""SQLAlchemy ORM models for all database tables."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    String,
    Text,
    TIMESTAMP,
    JSON,
    ARRAY,
    Float,
    ForeignKey,
    Boolean,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class Company(Base):
    """Company table - multi-tenancy root."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    openai_api_key: Mapped[Optional[str]] = mapped_column(
        "openAi_apiKey", String, nullable=True
    )
    rag_collection: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Chatwoot integration
    cw_account_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cw_apikey: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cw_base_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cw_token: Mapped[Optional[PyUUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, unique=True, index=True
    )
    # Unified inbox+contact filtering (replaces allowed_inboxes and allowed_contacts)
    # Structure: {"allowed_inboxes": [{"id": 1, "allowed_contacts": [1,2,3]}, ...]}
    # null or {"allowed_inboxes": []} = all allowed
    allowed_contacts: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True, default=None
    )
    standard_agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("agents.id"), nullable=True
    )
    standard_sub_agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sub_agents.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )

    # Relationships
    agents: Mapped[List["Agent"]] = relationship(
        back_populates="company", foreign_keys="[Agent.company_id]"
    )
    tools: Mapped[List["Tool"]] = relationship(back_populates="company")
    users: Mapped[List["User"]] = relationship(back_populates="company")
    customers: Mapped[List["Customer"]] = relationship(back_populates="company")


class User(Base):
    """User table - users who manage agents."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    auth_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="users")


class Agent(Base):
    """Agent table - main agent configuration."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # "dev" enables dev commands
    identity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    voice_tone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    master_goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    golden_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    negative_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    company: Mapped["Company"] = relationship(
        back_populates="agents", foreign_keys=[company_id]
    )
    sub_agents: Mapped[List["SubAgent"]] = relationship(back_populates="agent")


class SubAgent(Base):
    """SubAgent table - agent states/personas."""

    __tablename__ = "sub_agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agents.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    mission: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tools: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    agent: Mapped["Agent"] = relationship(back_populates="sub_agents")
    steps: Mapped[List["Step"]] = relationship(back_populates="sub_agent")
    decision_rules: Mapped[List["DecisionRule"]] = relationship(
        back_populates="sub_agent"
    )


class Step(Base):
    """Step table - sequential workflow steps."""

    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agents.id"))
    sub_agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sub_agents.id"))
    step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    relative_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    sub_agent: Mapped["SubAgent"] = relationship(back_populates="steps")


class DecisionRule(Base):
    """DecisionRule table - rules for sub-agent transitions."""

    __tablename__ = "decision_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agents.id"))
    sub_agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sub_agents.id"))
    rule: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    relative_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    sub_agent: Mapped["SubAgent"] = relationship(back_populates="decision_rules")
    connections: Mapped[List["SubAgentConnection"]] = relationship(
        back_populates="decision_rule"
    )


class SubAgentConnection(Base):
    """SubAgentConnection table - transitions between sub-agents."""

    __tablename__ = "sub_agent_connections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agents.id"))
    decision_rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("decision_rules.id")
    )
    source_sub_agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sub_agents.id"), nullable=True
    )
    target_sub_agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sub_agents.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    decision_rule: Mapped["DecisionRule"] = relationship(back_populates="connections")


class Tool(Base):
    """Tool table - available tools/functions."""

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    complete_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # internal/external
    method: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # HTTP method
    endpoint: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # URL base
    send_content_before_execution: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP, nullable=True
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="tools")
    parameters: Mapped[List["ToolParameter"]] = relationship(back_populates="tool")


class ToolParameter(Base):
    """ToolParameter table - individual tool parameters."""

    __tablename__ = "tool_parameters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=True
    )
    tool_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("tools.id"), nullable=True
    )
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    array_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mandatory: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    value: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # fixed/ai
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    tool: Mapped["Tool"] = relationship(back_populates="parameters")


class Customer(Base):
    """Customer table - user sessions."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    sessionId: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    cw_contact_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    cw_conversation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("agents.id"), nullable=True
    )
    sub_agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sub_agents.id"), nullable=True
    )
    variable_prompt_status: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    variable_prompt_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("prompts.id"), nullable=True
    )
    # Follow-up tracking (colunas já existem no banco)
    last_message: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    follow_up: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, default=0)
    next_follow: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Dev command state for multi-step dev commands (e.g., #mudar_agente)
    dev_command_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True, default=None
    )
    # Contextual data about the lead (already exists in DB as JSONB)
    customer_context: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="customers")


class ChatHistory(Base):
    """ChatHistory table - conversation messages."""

    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agents.id"))
    sub_agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sub_agents.id"))
    sessionId: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_follow_up: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False, index=True
    )


class StandardMessage(Base):
    """StandardMessage table - reusable global messages."""

    __tablename__ = "standard_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)


class Invitation(Base):
    """Invitation table - pending user invitations."""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("companies.id"))
    invited_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)


class Prompt(Base):
    """Prompt table - reusable prompts for RAG and other operations."""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=True
    )
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=datetime.utcnow, nullable=False
    )


class FollowUp(Base):
    """FollowUp table - configurações de follow-up agendados."""

    __tablename__ = "follow_ups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=True
    )
    sub_agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sub_agents.id"), nullable=True
    )
    step_order: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    schedule_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delay_minutes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    weekday: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    hour: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    minute: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    message_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )
    time_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Objection(Base):
    """Objection table - scripts for handling customer objections."""

    __tablename__ = "objections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=True
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    script: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("agents.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )


class Imbox(Base):
    """Imbox table - WhatsApp inbox configuration for Meta webhook routing."""

    __tablename__ = "imboxes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=True
    )
    imbox_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    whatsapp: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False
    )
