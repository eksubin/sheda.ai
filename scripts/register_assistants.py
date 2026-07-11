"""
Registers (or updates) the two Scheda AI assistants + their tools with the
Vapi API, using the reference configs in vapi_config/*.json.

Usage:
    cp .env.example .env   # fill in VAPI_API_KEY + WEBHOOK_BASE_URL
    python scripts/register_assistants.py

First run: creates tools + assistants, prints VAPI_ASSISTANT_PRIMARY_ID /
VAPI_ASSISTANT_REFERRAL_ID to paste into .env.

Subsequent runs: updates the same tools/assistants in place instead of
creating duplicates. Tool IDs are cached locally in
scripts/.vapi_tool_ids.json; assistant IDs are read back from .env
(VAPI_ASSISTANT_PRIMARY_ID / VAPI_ASSISTANT_REFERRAL_ID), so make sure
those are set before re-running.
"""

import json
import os
import pathlib

import httpx
from dotenv import load_dotenv

load_dotenv()

VAPI_BASE_URL = "https://api.vapi.ai"
CONFIG_DIR = pathlib.Path(__file__).parent.parent / "vapi_config"
TOOL_ID_CACHE = pathlib.Path(__file__).parent / ".vapi_tool_ids.json"

VAPI_API_KEY = os.environ["VAPI_API_KEY"]
WEBHOOK_BASE_URL = os.environ["WEBHOOK_BASE_URL"]

HEADERS = {"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"}

# maps each assistant config file to the .env var holding its existing Vapi ID (if any)
ASSISTANT_FILES = {
    "VAPI_ASSISTANT_PRIMARY_ID": "assistant_primary.json",
    "VAPI_ASSISTANT_REFERRAL_ID": "assistant_referral.json",
}


def load_tool_id_cache() -> dict[str, str]:
    if TOOL_ID_CACHE.exists():
        return json.loads(TOOL_ID_CACHE.read_text(encoding="utf-8"))
    return {}


def save_tool_id_cache(cache: dict[str, str]) -> None:
    TOOL_ID_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def register_tools(client: httpx.Client) -> dict[str, str]:
    tools_config = json.loads((CONFIG_DIR / "tools.json").read_text(encoding="utf-8"))
    cache = load_tool_id_cache()

    for tool in tools_config["tools"]:
        tool["server"]["url"] = tool["server"]["url"].replace("{WEBHOOK_BASE_URL}", WEBHOOK_BASE_URL)
        name = tool["function"]["name"]
        existing_id = cache.get(name)

        if existing_id:
            response = client.patch(f"{VAPI_BASE_URL}/tool/{existing_id}", headers=HEADERS, json=tool)
            response.raise_for_status()
            print(f"  updated tool '{name}' -> {existing_id}")
        else:
            response = client.post(f"{VAPI_BASE_URL}/tool", headers=HEADERS, json=tool)
            response.raise_for_status()
            existing_id = response.json()["id"]
            print(f"  created tool '{name}' -> {existing_id}")

        cache[name] = existing_id

    save_tool_id_cache(cache)
    return cache


def register_assistant(client: httpx.Client, env_key: str, filename: str, tool_ids: dict[str, str]) -> tuple[str, bool]:
    config = json.loads((CONFIG_DIR / filename).read_text(encoding="utf-8"))

    tool_names = config["model"].pop("tools")
    config["model"]["toolIds"] = [tool_ids[name] for name in tool_names]
    config["server"]["url"] = config["server"]["url"].replace("{WEBHOOK_BASE_URL}", WEBHOOK_BASE_URL)

    existing_id = os.environ.get(env_key)
    if existing_id:
        response = client.patch(f"{VAPI_BASE_URL}/assistant/{existing_id}", headers=HEADERS, json=config)
        response.raise_for_status()
        print(f"  updated assistant '{config['name']}' -> {existing_id}")
        return existing_id, False

    response = client.post(f"{VAPI_BASE_URL}/assistant", headers=HEADERS, json=config)
    response.raise_for_status()
    created_id = response.json()["id"]
    print(f"  created assistant '{config['name']}' -> {created_id}")
    return created_id, True


def main() -> None:
    with httpx.Client(timeout=30) as client:
        print("Registering tools...")
        tool_ids = register_tools(client)

        print("Registering assistants...")
        new_ids = {}
        for env_key, filename in ASSISTANT_FILES.items():
            assistant_id, is_new = register_assistant(client, env_key, filename, tool_ids)
            if is_new:
                new_ids[env_key] = assistant_id

    if new_ids:
        print("\nNew assistants created — add these to .env:\n")
        for key, value in new_ids.items():
            print(f"{key}={value}")
    else:
        print("\nDone — existing assistants updated in place, no .env changes needed.")


if __name__ == "__main__":
    main()
