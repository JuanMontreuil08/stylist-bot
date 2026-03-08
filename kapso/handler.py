"""
Kapso webhook: recibe mensaje, llama al agente, envía respuesta.
Texto en un mensaje; si hay ruta S3, envía la imagen en mensaje aparte.
"""
import re
import os
import httpx
import boto3
from dotenv import load_dotenv
from kapso.config import KAPSO_API_KEY, KAPSO_API_BASE, WHATSAPP_API_VERSION
import traceback

load_dotenv()

# Regex: [s3://bucket/key] (con corchetes, como pide el system prompt) o s3://bucket/key
# Incluir corchetes en el match para no dejar "[" o "]" sueltos en el texto
S3_URI_BRACKET_RE = re.compile(r'\[\s*s3://([^/\s]+)/([^\s\]]+)\s*\]')
S3_URI_BARE_RE = re.compile(r's3://([^/\s]+)/([^\s]+)')

s3_client = boto3.client(
    "s3",
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

# Idempotencia: evitar procesar el mismo mensaje dos veces (Kapso a veces envía el webhook duplicado)
_PROCESSED_IDS_MAX = 5000
_processed_message_ids = set()


def _presigned_url(bucket: str, key: str):
    try:
        return s3_client.generate_presigned_url(
            "get_object", 
            Params={"Bucket": bucket, "Key": key}, 
            ExpiresIn=3600
        )
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None


def _split_text_and_s3(reply: str):
    """Separa texto de URIs S3. Retorna (texto_limpio, lista_urls_publicas)."""
    urls = []

    # 1) Formato [s3://bucket/key] (incluye corchetes para no dejar "[" o "]" sueltos)
    for m in S3_URI_BRACKET_RE.finditer(reply):
        bucket, key = m.group(1), m.group(2)
        public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        urls.append(public_url)
        print(f"[DEBUG] S3 (bracketed) s3://{bucket}/{key} -> {public_url}")
    text = S3_URI_BRACKET_RE.sub("", reply)

    # 2) Por si el agente devuelve s3:// sin corchetes
    for m in S3_URI_BARE_RE.finditer(text):
        bucket, key = m.group(1), m.group(2)
        public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        urls.append(public_url)
        print(f"[DEBUG] S3 (bare) s3://{bucket}/{key} -> {public_url}")
    text = S3_URI_BARE_RE.sub("", text)

    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    print(f"[DEBUG] Found {len(urls)} S3 URIs in reply")
    return text, urls


def process_webhook_payload(payload: dict, agent_fn, event: str = "whatsapp.message.received"):
    if event != "whatsapp.message.received":
        return

    msg = payload.get("message") or {}
    message_id = msg.get("id")
    if message_id and message_id in _processed_message_ids:
        return

    text = (msg.get("text") or {}).get("body") or (msg.get("kapso") or {}).get("content") or ""
    text = (text or "").strip()
    image_url = (msg.get("type") == "image" and (msg.get("image") or {}).get("link")) or None
    if not text and not image_url:
        return

    phone_number_id = payload.get("phone_number_id")
    to = ((payload.get("conversation") or {}).get("phone_number") or "").lstrip("+").replace(" ", "")
    if not phone_number_id or not to:
        return

    if message_id:
        _processed_message_ids.add(message_id)
        if len(_processed_message_ids) > _PROCESSED_IDS_MAX:
            _processed_message_ids.clear()
            _processed_message_ids.add(message_id)

    agent_payload = {"prompt": text or ""}
    if image_url:
        agent_payload["image_url"] = image_url

    try:
        reply = agent_fn(agent_payload)
    except Exception:
        traceback.print_exc()
        reply = "Hubo un error. Intenta de nuevo."
    if reply is None:
        reply = "No pude generar una respuesta."
    reply = str(reply).strip()

    text_clean, image_urls = _split_text_and_s3(reply)
    base = f"{KAPSO_API_BASE}/meta/whatsapp/{WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    headers = {"Content-Type": "application/json", "X-API-Key": KAPSO_API_KEY}

    print(f"[DEBUG] Text: {text_clean[:100]}...")
    print(f"[DEBUG] Images to send: {len(image_urls)}")

    # Enviar texto
    if text_clean:
        resp = httpx.post(
            base, 
            json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text_clean[:4096]}}, 
            headers=headers, 
            timeout=15.0
        )
        print(f"[DEBUG] Text response: {resp.status_code}")

    # Enviar imágenes
    for i, link in enumerate(image_urls):
        print(f"[DEBUG] Sending image {i+1}/{len(image_urls)}: {link[:100]}...")
        resp = httpx.post(
            base, 
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "image",
                "image": {"link": link},
            }, 
            headers=headers, 
            timeout=15.0
        )
        print(f"[DEBUG] Image {i+1} response: {resp.status_code} - {resp.text[:200]}")
