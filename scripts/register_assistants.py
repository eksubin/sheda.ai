"""
Registers the two ShiftConfirm assistants + their tools with the Vapi API,
using the reference configs in vapi_config/*.json.

Usage:
    cp .env.example .env   # fill in VAPI_API_KEY + WEBHOOK_BASE_URL
    python scripts/register_assistants.py

Prints the resulting VAPI_ASSISTANT_PRIMARY_ID / VAPI_ASSISTANT_REFERRAL_ID
to paste into .env. Safe to re-run — each run creates new tools/assistants
in Vapi (Vapi has no upsert-by-name), so delete the old ones in the
dashboard if you don't want duplicates.
"""

import json
import os
import pathlib

import httpx
from dotenv import load_dotenv

load_dotenv()

VAPI_BASE_URL = "https://api.vapi.ai"
CONFIG_DIR = pathlib.Path(__file__).parent.parent / "vapi_config"

VAPI_API_KEY = os.environ["VAPI_API_KEY"]
WEBHOOK_BASE_URL = os.environ["WEBHOOK_BASE_URL"]

HEADERS = {"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"}

# which tools each assistant config's `model.tools` name-list should resolve to
ASSISTANT_FILES = ["assistant_primary.json", "assistant_referral.json"]


def register_tools(client: httpx.Client) -> dict[str, str]:
    tools_config = json.loads((CONFIG_DIR / "tools.json").read_text())
    tool_ids: dict[str, str] = {}

    for tool in tools_config["tools"]:
        tool["server"]["url"] = tool["server"]["url"].replace("{WEBHOOK_BASE_URL}", WEBHOOK_BASE_URL)
        response = client.post(f"{VAPI_BASE_URL}/tool", headers=HEADERS, json=tool)
        response.raise_for_status()
        created = response.json()
        name = tool["function"]["name"]
        tool_ids[name] = created["id"]
        print(f"  created tool '{name}' -> {created['id']}")

    return tool_ids


def register_assistant(client: httpx.Client, filename: str, tool_ids: dict[str, str]) -> str:
    config = json.loads((CONFIG_DIR / filename).read_text())

    tool_names = config["model"].pop("tools")
    config["model"]["toolIds"] = [tool_ids[name] for name in tool_names]
    config["server"]["url"] = config["server"]["url"].replace("{WEBHOOK_BASE_URL}", WEBHOOK_BASE_URL)

    response = client.post(f"{VAPI_BASE_URL}/assistant", headers=HEADERS, json=config)
    response.raise_for_status()
    created = response.json()
    print(f"  created assistant '{config['name']}' -> {created['id']}")
    return created["id"]


def main() -> None:
    with httpx.Client(timeout=30) as client:
        print("Registering tools...")
        tool_ids = register_tools(client)

        print("Registering assistants...")
        primary_id = register_assistant(client, "assistant_primary.json", tool_ids)
        referral_id = register_assistant(client, "assistant_referral.json", tool_ids)

    print("\nDone. Add these to .env:\n")
    print(f"VAPI_ASSISTANT_PRIMARY_ID={primary_id}")
    print(f"VAPI_ASSISTANT_REFERRAL_ID={referral_id}")


if __name__ == "__main__":
    main()
