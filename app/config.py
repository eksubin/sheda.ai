import os


class Settings:
    database_url: str = os.environ["DATABASE_URL"]
    redis_url: str = os.environ["REDIS_URL"]
    vapi_api_key: str = os.environ["VAPI_API_KEY"]
    vapi_public_key: str = os.environ.get("VAPI_PUBLIC_KEY", "")
    vapi_phone_number_id: str = os.environ["VAPI_PHONE_NUMBER_ID"]
    vapi_assistant_primary_id: str = os.environ["VAPI_ASSISTANT_PRIMARY_ID"]
    vapi_assistant_referral_id: str = os.environ["VAPI_ASSISTANT_REFERRAL_ID"]
    webhook_base_url: str = os.environ["WEBHOOK_BASE_URL"]
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")


settings = Settings()
