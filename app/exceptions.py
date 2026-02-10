"""Custom exceptions for the Star Agents Orchestrator."""


class AgentOrchestratorError(Exception):
    """Base exception for all application errors."""

    pass


class DatabaseError(AgentOrchestratorError):
    """Database operation errors."""

    pass


class TransientDatabaseError(DatabaseError):
    """Transient database errors that can be retried (connection issues, timeouts)."""

    pass


class ValidationError(AgentOrchestratorError):
    """Data validation errors."""

    pass


class NotFoundError(AgentOrchestratorError):
    """Resource not found errors."""

    pass


class OpenAIError(AgentOrchestratorError):
    """OpenAI API errors."""

    pass


class OpenAIRateLimitError(OpenAIError):
    """Rate limit exceeded error."""

    pass


class OpenAIAuthenticationError(OpenAIError):
    """Invalid or missing API key error."""

    pass


class OpenAITimeoutError(OpenAIError):
    """Request timeout error."""

    pass


class ToolExecutionError(AgentOrchestratorError):
    """Tool execution errors."""

    pass


class MaxIterationsExceededError(AgentOrchestratorError):
    """Maximum tool calling iterations exceeded."""

    pass
