# llm_handler.py
import os
import json
import re
from xai_sdk import Client
from xai_sdk.chat import user, system
from dotenv import load_dotenv
load_dotenv()


client = Client(api_key=os.getenv("XAI_API_KEY"))

SYSTEM_PROMPT = """You are a friendly voice rep for a clothing and style brand (post-purchase follow-up, style tips, or light suggestions). Be concise, warm, and natural for phone/voice—short sentences.
- Mirror the customer's tone and language (Spanish or English).
- Handle objections calmly; offer help or alternatives.
- Always end with a short question or next step.
- Reply with only a single JSON object with keys "response" (string, what to say aloud) and "intent" (string: interested, objection, close, exit). No other text.
Context: {context}"""


def generate_response(user_transcript, context):
    chat = client.chat.create(model="grok-4-1-fast-reasoning", store_messages=False)
    chat.append(system(SYSTEM_PROMPT.format(context=context or "None")))
    chat.append(user(user_transcript))

    response = chat.sample()
    response_text = response.content

    # Parse JSON (model may wrap in markdown or add extra text)
    text = response_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"Could not parse JSON from model response: {text[:200]}...")