"""HTTP executor for external tools."""

import json
import logging
from typing import Any

import httpx

from app.models.schemas import ExternalToolConfigSchema, ExternalToolParameterSchema, ToolResult
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)


class HttpToolExecutor:
    """Executes external HTTP tools based on database configuration."""

    def __init__(self, timeout: int = 300) -> None:
        self.timeout = timeout

    async def execute(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_config: dict[str, Any],
        ai_arguments: dict[str, Any],
        customer_id: int | None = None,
    ) -> ToolResult:
        """
        Execute an external HTTP tool.

        Args:
            tool_call_id: The tool call ID from OpenAI
            tool_name: The tool name
            tool_config: Tool configuration from database
            ai_arguments: Arguments provided by the AI

        Returns:
            ToolResult with execution outcome
        """
        logger.info(f"[ExternalTool] Iniciando execução: {tool_name}")
        logger.debug(f"[ExternalTool] tool_config: {json.dumps(tool_config, default=str)}")
        logger.debug(f"[ExternalTool] ai_arguments: {json.dumps(ai_arguments, default=str)}")

        # Parse config
        config = ExternalToolConfigSchema(
            id=tool_config["id"],
            title=tool_config["title"],
            method=tool_config.get("method"),
            endpoint=tool_config.get("endpoint"),
            parameters=[
                ExternalToolParameterSchema(**p) for p in tool_config.get("parameters", [])
            ],
        )
        logger.info(
            f"[ExternalTool] Config parsed: method={config.method}, "
            f"endpoint={config.endpoint}, params_count={len(config.parameters)}"
        )

        # 1. Validate tool config (must have method and endpoint)
        error = self._validate_tool_config(config)
        if error:
            logger.error(f"[ExternalTool] Validação falhou: {error}")
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_type="externa",
                success=False,
                content=error,
            )

        # 2. Validate mandatory AI parameters
        error = self._validate_ai_arguments(config.parameters, ai_arguments)
        if error:
            logger.error(f"[ExternalTool] Parâmetro obrigatório faltando: {error}")
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_type="externa",
                success=False,
                content=error,
            )

        # 3. Build HTTP request
        request_config = self._build_request(config, ai_arguments, customer_id)
        logger.info(
            f"[ExternalTool] Request montado: {request_config['method']} {request_config['url']}"
        )
        logger.debug(f"[ExternalTool] Query params: {request_config.get('query_params')}")
        logger.debug(f"[ExternalTool] Headers: {request_config.get('headers')}")
        logger.debug(f"[ExternalTool] Body: {request_config.get('body')}")

        # 4. Execute HTTP request
        try:
            logger.info(f"[ExternalTool] Executando HTTP request...")
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=request_config["method"],
                    url=request_config["url"],
                    params=request_config.get("query_params"),
                    headers=request_config.get("headers"),
                    json=request_config.get("body") if request_config.get("body") else None,
                )

                logger.info(
                    f"[ExternalTool] Response: status={response.status_code}, "
                    f"success={response.is_success}, length={len(response.text)}"
                )
                logger.debug(f"[ExternalTool] Response body: {response.text[:500]}...")

                # Return full response as content
                return ToolResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_type="externa",
                    success=response.is_success,
                    content=response.text,
                )

        except httpx.TimeoutException as e:
            logger.error(f"[ExternalTool] Timeout após {self.timeout}s")
            send_critical_alert(
                "EXTERNAL_TOOL_TIMEOUT",
                "http_executor.py:execute",
                f"Tool '{tool_name}' timeout after {self.timeout}s",
                extra=f"tool={tool_name}, endpoint={config.endpoint}",
            )
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_type="externa",
                success=False,
                content=f"Timeout: requisição excedeu {self.timeout}s",
            )
        except httpx.RequestError as e:
            logger.error(f"[ExternalTool] Erro de conexão: {str(e)}")
            send_critical_alert(
                "EXTERNAL_TOOL_CONNECTION_ERROR",
                "http_executor.py:execute",
                e,
                extra=f"tool={tool_name}, endpoint={config.endpoint}",
            )
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_type="externa",
                success=False,
                content=f"Erro de conexão: {str(e)}",
            )

    def _validate_tool_config(self, config: ExternalToolConfigSchema) -> str | None:
        """Validate tool has method and endpoint."""
        if not config.method:
            return "Tool mal configurada: falta 'method'"
        if not config.endpoint:
            return "Tool mal configurada: falta 'endpoint'"
        return None

    def _validate_ai_arguments(
        self,
        parameters: list[ExternalToolParameterSchema],
        ai_arguments: dict[str, Any],
    ) -> str | None:
        """Validate mandatory AI parameters are provided."""
        for param in parameters:
            if param.mandatory and param.source == "ai":
                if param.name not in ai_arguments or ai_arguments[param.name] is None:
                    return f"Parâmetro obrigatório '{param.name}' não fornecido"
        return None

    def _build_request(
        self,
        config: ExternalToolConfigSchema,
        ai_arguments: dict[str, Any],
        customer_id: int | None = None,
    ) -> dict[str, Any]:
        """Build HTTP request from parameters."""
        request: dict[str, Any] = {
            "method": config.method.upper() if config.method else "GET",
            "url": config.endpoint or "",
            "path_params": {},
            "query_params": {},
            "headers": {},
            "body": {},
        }

        for param in config.parameters:
            value = self._extract_value(param, ai_arguments, customer_id)
            if value is None:
                continue

            typed_value = self._convert_type(value, param.type, param.array_type)

            if param.location == "path_parameters":
                request["path_params"][param.name] = typed_value
            elif param.location == "query_parameters":
                request["query_params"][param.name] = typed_value
            elif param.location == "headers":
                request["headers"][param.name] = str(typed_value)
            elif param.location == "body":
                request["body"][param.name] = typed_value

        # Process path parameters in URL
        for key, val in request["path_params"].items():
            request["url"] = request["url"].replace(f"{{{key}}}", str(val))

        # Clean up empty dicts
        if not request["query_params"]:
            request["query_params"] = None
        if not request["headers"]:
            request["headers"] = None
        if not request["body"]:
            request["body"] = None

        return request

    def _extract_value(
        self,
        param: ExternalToolParameterSchema,
        ai_arguments: dict[str, Any],
        customer_id: int | None = None,
    ) -> Any:
        """Extract parameter value from fixed, AI or customer_id source."""
        if param.source == "customer_id":
            return customer_id
        elif param.source == "fixed":
            # value is {"value": [...]} - always array
            if not param.value:
                return None
            values = param.value.get("value", [])
            if not values:
                return None
            if param.type == "array":
                return values  # return full array
            return values[0]  # return first element
        else:  # source == "ai"
            return ai_arguments.get(param.name)

    def _convert_type(
        self,
        value: Any,
        target_type: str | None,
        array_type: str | None = None,
    ) -> Any:
        """Convert value to the expected type."""
        if value is None:
            return None

        if target_type == "text":
            return str(value)
        elif target_type == "int":
            return int(value)
        elif target_type == "float":
            return float(value)
        elif target_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        elif target_type == "array":
            if not isinstance(value, list):
                value = [value]
            return [self._convert_type(v, array_type) for v in value]
        elif target_type == "object":
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                return json.loads(value)
            return value

        # Default: return as-is
        return value
