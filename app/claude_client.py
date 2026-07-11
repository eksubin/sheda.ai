import anthropic

from app.config import settings

MODEL = "claude-opus-4-8"


async def chat_reply(system_prompt: str, message: str) -> str:
    """Single-turn reply from Claude for the dashboard's AI chat panel."""
    async with anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )
        return next((block.text for block in response.content if block.type == "text"), "")
