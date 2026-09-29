"""reasoning_effort precedence in ContextBuilder.build and RAG sampling, repos mocked (no DB).

    python tests/test_reasoning_effort.py
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, ".")

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
os.environ.setdefault("API_KEY", "test-key")

from app.repositories.prompt import PromptWithConfig  # noqa: E402
from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.tools.internal.rag.tool import _sampling  # noqa: E402


def _builder(
    sub_agent_effort: str | None,
    variable_prompt: bool = False,
    prompt_effort: str | None = None,
) -> ContextBuilder:
    agent_repo = AsyncMock()
    agent_repo.get_full_context.return_value = {
        "customer": {
            "id": 1,
            "sessionId": "s",
            "variable_prompt_status": variable_prompt,
            "variable_prompt_id": 9 if variable_prompt else None,
        },
        "agent": {"id": 1, "title": "A"},
        "sub_agent": {
            "id": 1,
            "title": "S",
            "model": "gpt-5.5",
            "reasoning_effort": sub_agent_effort,
        },
    }
    chat_repo = AsyncMock()
    chat_repo.get_history_with_orphan_handling.return_value = []
    prompt_repo = AsyncMock()
    prompt_repo.get_prompt_by_id.return_value = PromptWithConfig(
        prompt="p", model="gpt-4o-mini", temperature=0.3, reasoning_effort=prompt_effort
    )
    return ContextBuilder(agent_repo, chat_repo, prompt_repo)


async def main() -> None:
    p = await _builder("low").build("s", 1)
    assert (p.model, p.reasoning_effort) == ("gpt-5.5", "low"), p

    p = await _builder(None).build("s", 1)
    assert p.reasoning_effort is None, p

    p = await _builder("low").build("s", 1, reasoning_effort_override="high")
    assert p.reasoning_effort == "high", p

    # Sub-agent effort must not leak onto a model it wasn't set for (gpt-4.x -> 400)
    p = await _builder("low").build("s", 1, model_override="gpt-4.1")
    assert (p.model, p.reasoning_effort) == ("gpt-4.1", None), p

    p = await _builder("low", variable_prompt=True).build("s", 1)
    assert (p.model, p.reasoning_effort) == ("gpt-4o-mini", None), p

    p = await _builder("low", variable_prompt=True, prompt_effort="medium").build("s", 1)
    assert p.reasoning_effort == "medium", p

    cfg = PromptWithConfig(prompt="p", model="m", temperature=0.3)
    assert _sampling(cfg) == {"temperature": 0.3}
    assert _sampling(cfg._replace(reasoning_effort="low")) == {"reasoning_effort": "low"}

    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
