"""Self-check: categories tool ponta a ponta contra o banco real."""
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from app.db.database import AsyncSessionLocal
from app.models.schemas import ToolExecutionContext
from app.services.tool_handler import ToolHandler
from app.services.context_builder import ContextBuilder



async def main():
    async with AsyncSessionLocal() as db:
        h = ToolHandler()
        assert "categories" in h.INTERNAL_TOOLS
        ctx = ToolExecutionContext(session_id="t", company_id=5, agent_id=39, sub_agent_id=137, db=db)
        r = await h._tools["categories"].execute({}, ctx)
        assert r.success and "7Ball" in r.content, r.content
        assert "Reebok" not in r.content, "catalogo nao pode conter Reebok"
        assert r.content.count("\n#### ") == 37, r.content.count("\n#### ")

        # empresa sem catalogo -> falha limpa, sem exception
        r2 = await h._tools["categories"].execute({}, ctx.model_copy(update={"company_id": 999}))
        assert not r2.success and "Nenhum" in r2.content, r2.content

        # tool chega ao formato OpenAI a partir do banco
        rows = (await db.execute(text("select title, complete_json from tools where company_id=5"))).fetchall()
        from app.models.schemas import ToolSchema
        openai_tools = ContextBuilder._format_tools_for_openai(None,
            [ToolSchema(id=1, title=r[0], complete_json=r[1]) for r in rows])
        assert any(t["function"]["name"] == "categories" for t in openai_tools), openai_tools
        assert openai_tools[0]["function"]["parameters"] == {"type": "object", "properties": {}, "required": []}
    print("OK - categories tool funciona ponta a ponta")

asyncio.run(main())
