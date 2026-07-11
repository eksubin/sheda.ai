import httpx

from app.config import settings

VAPI_BASE_URL = "https://api.vapi.ai"


async def create_call(assistant_id: str, phone_number: str, variable_values: dict) -> dict:
    """Places an outbound call via Vapi's /call API using the imported Twilio number."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{VAPI_BASE_URL}/call",
            headers={"Authorization": f"Bearer {settings.vapi_api_key}"},
            json={
                "assistantId": assistant_id,
                "phoneNumberId": settings.vapi_phone_number_id,
                "customer": {"number": phone_number},
                "assistantOverrides": {"variableValues": variable_values},
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
