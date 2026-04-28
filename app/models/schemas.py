"""Pydantic schemas for request/response validation."""

from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Request/Response Schemas
# =============================================================================


class ChatRequest(BaseModel):
    """Request schema for POST /chat endpoint."""

    session_id: str = Field(..., description="Unique session identifier")
    message: str = Field(..., description="User message content")
    company_id: int = Field(..., description="Company ID for multi-tenancy")


class ReprocessRequest(BaseModel):
    """Request schema for POST /chat/reprocess endpoint."""

    customer_id: int = Field(..., description="Customer ID to reprocess")


class VoeChatRequest(BaseModel):
    """Request schema for POST /voe/chat endpoint."""

    session_id: str = Field(..., min_length=1, max_length=100, description="Unique session identifier")
    message: str = Field(..., min_length=1, max_length=10000, description="User message content")


class VoeCreateCustomerRequest(BaseModel):
    """Request schema for POST /voe/create_customer endpoint."""

    session_id: str = Field(..., min_length=1, max_length=100, description="Unique session identifier")
    user_id: str = Field(..., min_length=1, description="VOE user identifier")
    bar_event_id: str = Field(..., min_length=1, description="VOE bar event identifier")
    ticket_event_id: str = Field(..., min_length=1, description="VOE ticket event identifier")
    enterprise_id: str = Field(..., min_length=1, description="VOE enterprise identifier")
    customer_context: dict[str, Any] | None = Field(
        None, description="Optional JSON object with customer context data"
    )


# =============================================================================
# Agent Context Schemas
# =============================================================================


class ToolParameterSchema(BaseModel):
    """Schema for tool parameters."""

    name: str | None = None
    type: str | None = None
    description: str | None = None
    required: bool | None = None


class ToolSchema(BaseModel):
    """Schema for tools with parameters."""

    id: int
    title: str | None = None
    instructions: str | None = None
    complete_json: dict[str, Any] | None = None
    parameters: list[ToolParameterSchema] = []
    send_content_before_execution: bool = False


class StepSchema(BaseModel):
    """Schema for workflow steps."""

    id: int
    step: str | None = None
    relative_id: int | None = None


class SubAgentConnectionSchema(BaseModel):
    """Schema for sub-agent connections."""

    id: int
    target_sub_agent_id: int | None = None


class DecisionRuleSchema(BaseModel):
    """Schema for decision rules with connections."""

    id: int
    rule: str | None = None
    relative_id: int | None = None
    connections: list[SubAgentConnectionSchema] = []


class SubAgentSchema(BaseModel):
    """Schema for sub-agent configuration."""

    id: int
    name: str
    mission: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    temperature: float | None = None


class AgentSchema(BaseModel):
    """Schema for agent configuration."""

    id: int
    name: str
    identity: str | None = None
    voice_tone: str | None = None
    master_goal: str | None = None
    golden_rules: str | None = None
    negative_rules: str | None = None
    output_instructions: str | None = None
    output_type: str | None = None


class CustomerSchema(BaseModel):
    """Schema for customer session."""

    id: int
    sessionId: str
    agent_id: int | None = None
    sub_agent_id: int | None = None
    variable_prompt_status: bool | None = None
    variable_prompt_id: int | None = None
    customer_context: dict[str, Any] | None = None


class AgentContext(BaseModel):
    """Complete agent context for building prompts."""

    customer: CustomerSchema
    agent: AgentSchema
    sub_agent: SubAgentSchema
    steps: list[StepSchema] = []
    decision_rules: list[DecisionRuleSchema] = []
    tools: list[ToolSchema] = []


# =============================================================================
# OpenAI Payload Schemas
# =============================================================================


class OpenAIMessage(BaseModel):
    """Message in OpenAI format."""

    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    model_config = {"extra": "allow"}


class OpenAIPayload(BaseModel):
    """Complete payload for OpenAI API."""

    model: str
    temperature: float
    messages: list[OpenAIMessage]
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None


# =============================================================================
# OpenAI Response Schemas
# =============================================================================


class ToolCallFunction(BaseModel):
    """Function details within a tool call."""

    name: str
    arguments: str


class ToolCall(BaseModel):
    """Individual tool call from OpenAI response."""

    id: str
    type: str = "function"
    function: ToolCallFunction


class OpenAIChoice(BaseModel):
    """Single choice from OpenAI response."""

    index: int
    message: OpenAIMessage
    finish_reason: str


class OpenAIResponse(BaseModel):
    """Parsed response from OpenAI API."""

    id: str
    model: str
    choices: list[OpenAIChoice]
    usage: dict[str, Any] | None = None
    created: int = 0
    service_tier: str | None = None
    system_fingerprint: str | None = None


class TokenUsage(BaseModel):
    """Token usage data from an OpenAI response."""

    input_tokens: int = 0
    input_cached_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def merge(self, other: "TokenUsage") -> "TokenUsage":
        """Sum tokens from two calls (e.g. send_content_before_execution)."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            input_cached_tokens=self.input_cached_tokens + other.input_cached_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            model=self.model or other.model,
        )


def extract_token_usage(response: "OpenAIResponse") -> TokenUsage:
    """Extract token usage data from an OpenAI response."""
    usage = response.usage
    if not usage:
        return TokenUsage(model=response.model)

    cached = 0
    details = usage.get("prompt_tokens_details")
    if details:
        cached = details.get("cached_tokens", 0)

    return TokenUsage(
        input_tokens=usage.get("prompt_tokens", 0) - cached,
        input_cached_tokens=cached,
        output_tokens=usage.get("completion_tokens", 0),
        model=response.model,
    )


class ChatResponse(BaseModel):
    """Response schema for POST /chat endpoint."""

    session_id: str
    message: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str


# =============================================================================
# Tool Execution Schemas
# =============================================================================


class ToolResult(BaseModel):
    """Result from tool execution."""

    tool_call_id: str
    tool_name: str
    tool_type: str  # "interna" ou "externa"
    success: bool
    content: str
    invalidate_cache: bool = False  # Se True, força rebuild do context na próxima iteração
    rag_result: list[dict[str, Any]] | None = None  # pgvector search results (FAQ path)


class ToolExecutionContext(BaseModel):
    """Context for tool execution."""

    session_id: str
    company_id: int
    agent_id: int
    sub_agent_id: int
    customer_id: int | None = None

    # Dependencies for internal tools (RAG, etc.)
    db: Any | None = None  # AsyncSession - using Any for Pydantic compatibility
    openai_api_key: str | None = None

    # Chat history for tools that need conversation context
    chat_history: list[dict[str, Any]] | None = None

    # Callback to send messages to the lead (e.g., "Um momento por favor")
    on_send_messages: Any | None = None

    # Reference to ConversationTurn for objection_generating flag
    conversation_turn: Any | None = None

    model_config = {"arbitrary_types_allowed": True}


# =============================================================================
# Chat Completion Response Schemas (formato OpenAI completo)
# =============================================================================


class ChatCompletionMessage(BaseModel):
    """Message in chat completion response."""

    role: str
    content: dict[str, Any] | str | None = None
    refusal: str | None = None
    annotations: list[Any] = []


class ChatCompletionChoice(BaseModel):
    """Choice in chat completion response."""

    index: int
    message: ChatCompletionMessage
    logprobs: Any | None = None
    finish_reason: str


class PromptTokensDetails(BaseModel):
    """Details about prompt tokens."""

    cached_tokens: int = 0
    audio_tokens: int = 0


class CompletionTokensDetails(BaseModel):
    """Details about completion tokens."""

    reasoning_tokens: int = 0
    audio_tokens: int = 0
    accepted_prediction_tokens: int = 0
    rejected_prediction_tokens: int = 0


class UsageInfo(BaseModel):
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_details: PromptTokensDetails | None = None
    completion_tokens_details: CompletionTokensDetails | None = None


class ChatCompletionResponse(BaseModel):
    """Complete chat completion response (formato OpenAI)."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: UsageInfo | None = None
    service_tier: str | None = None
    system_fingerprint: str | None = None


# =============================================================================
# External Tool Schemas
# =============================================================================


class ExternalToolParameterSchema(BaseModel):
    """Schema para parâmetros de external tools."""

    name: str | None = None
    type: str | None = None  # text, int, float, bool, array, object
    array_type: str | None = None
    value: dict[str, Any] | None = None  # {"value": [...]} - sempre array
    source: str | None = None  # 'fixed', 'ai' ou 'customer_id'
    location: str | None = None  # path_parameters, query_parameters, headers, body
    mandatory: bool = False


class ExternalToolConfigSchema(BaseModel):
    """Schema para configuração completa de external tool."""

    id: int
    title: str
    method: str | None = None  # HTTP method
    endpoint: str | None = None  # URL base
    parameters: list[ExternalToolParameterSchema] = []
