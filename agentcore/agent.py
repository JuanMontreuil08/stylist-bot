import os
import io
import uuid
from pathlib import Path
import httpx
import boto3
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel
from dotenv import load_dotenv
from agentcore.tools import (
    search_clothing_catalog,
    search_products_online,
    update_user_profile,
    initiate_voice_call,
    tryon_get_profile,
    tryon_upload_photo,
    tryon_search_garments,
    tryon_generate,
)
from utils.handle_kapso_image import convert_kapso_image_to_bytes
from agentcore.memory import make_session_manager
load_dotenv()


S3_IMAGE_BUCKET = os.getenv("S3_IMAGE_BUCKET")

app = BedrockAgentCoreApp()

# Initialize S3 client
s3_client = boto3.client(
            's3', region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
        )

# Load system prompt from file
_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip()

# Added region for Cross-Region Inference
model_id = "us.anthropic.claude-sonnet-4-6"
model = BedrockModel(
    model_id=model_id,
)
_AGENT_TOOLS = [
    search_clothing_catalog,
    search_products_online,
    update_user_profile,
    initiate_voice_call,
    tryon_get_profile,
    tryon_upload_photo,
    tryon_search_garments,
    tryon_generate,
]

# Per-user agent registry: phone_number → Agent instance.
# Each agent owns its own agent.messages list, so conversation history is
# fully isolated between users. History resets on server restart (acceptable
# for MVP — upgrade to AgentCoreMemorySessionManager for persistence at scale).
_user_agents: dict[str, Agent] = {}


def _get_agent(phone_number: str) -> Agent:
    """Return the Agent for this user, creating one on first contact."""
    if phone_number not in _user_agents:
        session_manager = make_session_manager(phone_number)
        _user_agents[phone_number] = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=_AGENT_TOOLS,
            session_manager=session_manager,
        )
        backend = "Bedrock AgentCore Memory" if session_manager else "in-process (no persistence)"
        print(f"[agent] new session for {phone_number} — memory: {backend} (total active: {len(_user_agents)})")
    return _user_agents[phone_number]


@app.entrypoint
def strands_agent_bedrock(payload):
    """
    Invoke the agent with a payload. Supports prompt (text), optional image_url and phone_number.
    When there is an image, the agent should classify it and call tryon_upload_photo with the provided phone_number and image_url.
    """
    image_url = payload.get("image_url")
    phone_number = (payload.get("phone_number") or "").strip() or "unknown"
    print("[strands_agent_bedrock] image_url:", (image_url[:80] + "..." if image_url and len(image_url) > 80 else image_url))
    prompt = (payload.get("prompt") or "").strip()

    agent = _get_agent(phone_number)

    if image_url:
        try:
            img_bytes, img_fmt = convert_kapso_image_to_bytes(image_url)
        except Exception:
            return "Cannot load image. Try again."
        content = []
        instruction = (
            "The user sent this image. Classify it into exactly one category: selfie, full_body, or garment. "
            "If it is garment, write garment_description EXACTLY in the strict format: <type>_<color>_<brand> (ASCII, lowercase, no accents, using ONLY underscores; NO commas or spaces). "
            "The <type> must be the real garment type (e.g. polo, pantalon, camiseta, chaqueta). "
            "The <color> must be a simple color (e.g. rojo, negro, beige, azul). "
            "The <brand> must be the brand if recognized; otherwise use 'marca_desconocida'. "
            "Examples: polo_rojo_lacoste, pantalon_beige_zara, camiseta_azul_nike. "
            "Then call tryon_upload_photo with phone_number=%r, image_url=%r, photo_type=<your classification>, garment_description=<only if garment; otherwise \"\">."
        ) % (phone_number, image_url)
        content.append({"text": instruction})
        if prompt:
            content.append({"text": prompt})
        content.append({
            "image": {
                "format": img_fmt,
                "source": {"bytes": img_bytes}
            }
        })
        response = agent(content)
    else:
        user_input = prompt or ""
        print(f"[agent] user={phone_number} messages_in_history={len(agent.messages)} input={user_input[:80]}")
        response = agent(user_input)

    return response.message["content"][0]["text"]

if __name__ == "__main__":
    app.run()