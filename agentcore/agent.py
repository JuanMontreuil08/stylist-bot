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
from agentcore.tools import search_clothing_catalog, search_products_online, initiate_voice_call
from utils.handle_kapso_image import convert_kapso_image_to_bytes
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
agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[search_clothing_catalog, search_products_online, initiate_voice_call]
)

@app.entrypoint
def strands_agent_bedrock(payload):
    """
    Invoke the agent with a payload. Supports prompt (text) and optional image_url.
    """
    image_url = payload.get("image_url")
    print("[strands_agent_bedrock] image_url:", image_url)
    prompt = (payload.get("prompt") or "").strip()

    if image_url:
        try:
            img_bytes, img_fmt = convert_kapso_image_to_bytes(image_url)
        except Exception:
            return "Cannot load image. Try again."
        content = []
        if prompt:
            content.append({"text": prompt})
        content.append({
            "image": {
                "format": img_fmt,
                "source": {"bytes": img_bytes}
            }
        })
        if not content or (len(content) == 1 and "image" in content[0]):
            content.insert(0, {"text": "¿What do you see in this image?"})
        response = agent(content)
    else:
        user_input = prompt or ""
        print("User input:", user_input)
        response = agent(user_input)

    return response.message["content"][0]["text"]

if __name__ == "__main__":
    app.run()