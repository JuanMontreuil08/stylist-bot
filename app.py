"""
Run from project root: uvicorn app:app --reload
Kapso webhook -> agent -> reply.
"""
import json
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from dotenv import load_dotenv
load_dotenv()

from agentcore.agent import strands_agent_bedrock
from kapso.handler import process_webhook_payload

app = FastAPI()


@app.get("/")
def root():
    return {"ok": True, "webhook": "POST /webhooks/whatsapp"}


@app.post("/webhooks/whatsapp")
async def webhook(request: Request):
    body = await request.body()
    payload = json.loads(body.decode("utf-8"))
    event = request.headers.get("X-Webhook-Event") or "whatsapp.message.received"
    process_webhook_payload(payload, strands_agent_bedrock, event=event)
    return PlainTextResponse("OK", status_code=200)
