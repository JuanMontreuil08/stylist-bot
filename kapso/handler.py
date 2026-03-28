"""
Kapso webhook: recibe mensaje, llama al agente, envía respuesta.
Texto en un mensaje; si hay ruta S3, envía la imagen en mensaje aparte.
Cuando el usuario envía una imagen, se sube al bucket raw de Supabase y se pasa al agente la URL estable + phone_number.
"""
import re
import os
import uuid
import httpx
import requests
import boto3
from dotenv import load_dotenv
from kapso.config import KAPSO_API_KEY, KAPSO_API_BASE, WHATSAPP_API_VERSION
import traceback

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
TRYON_RAW_BUCKET = os.getenv("TRYON_RAW_BUCKET", "tryon-raw").strip()
TRYON_SIGNED_URL_TTL = int(os.getenv("TRYON_SIGNED_URL_TTL_SECONDS", "3600"))

# Regex: [s3://bucket/key] (con corchetes, como pide el system prompt) o s3://bucket/key
# Incluir corchetes en el match para no dejar "[" o "]" sueltos en el texto
S3_URI_BRACKET_RE = re.compile(r'\[\s*s3://([^/\s]+)/([^\s\]]+)\s*\]')
S3_URI_BARE_RE = re.compile(r's3://([^/\s]+)/([^\s]+)')
SUPABASE_STORAGE_OBJECT_URL_RE = re.compile(r'https://[^\s]+/storage/v1/object/[^\s]+')

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
    """Separa texto de URIs S3 y URLs firmadas de Supabase. Retorna (texto_limpio, lista_urls)."""
    urls = []

    # 0) Supabase signed object URLs (ej: https://.../storage/v1/object/<bucket>/<key>?token=...)
    for m in SUPABASE_STORAGE_OBJECT_URL_RE.finditer(reply):
        url = m.group(0).rstrip(").,];")
        urls.append(url)
    text = SUPABASE_STORAGE_OBJECT_URL_RE.sub("", reply)

    # 1) Formato [s3://bucket/key] (incluye corchetes para no dejar "[" o "]" sueltos)
    for m in S3_URI_BRACKET_RE.finditer(text):
        bucket, key = m.group(1), m.group(2)
        public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        urls.append(public_url)
        print(f"[DEBUG] S3 (bracketed) s3://{bucket}/{key} -> {public_url}")
    text = S3_URI_BRACKET_RE.sub("", text)

    # 2) Por si el agente devuelve s3:// sin corchetes
    for m in S3_URI_BARE_RE.finditer(text):
        bucket, key = m.group(1), m.group(2)
        public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        urls.append(public_url)
        print(f"[DEBUG] S3 (bare) s3://{bucket}/{key} -> {public_url}")
    text = S3_URI_BARE_RE.sub("", text)

    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    print(f"[DEBUG] Found {len(urls)} URIs in reply")
    return text, urls


def process_webhook_payload(payload: dict, agent_fn, event: str = "whatsapp.message.received"):
    if event != "whatsapp.message.received":
        return

    msg = payload.get("message") or {}
    message_id = msg.get("id")
    if message_id and message_id in _processed_message_ids:
        return

    # Extract text — also handle interactive reply payloads (button_reply / list_reply)
    text = (msg.get("text") or {}).get("body") or (msg.get("kapso") or {}).get("content") or ""
    text = (text or "").strip()

    interactive_id: str | None = None
    if msg.get("type") == "interactive":
        interactive = msg.get("interactive") or {}
        int_type = interactive.get("type")
        if int_type == "button_reply":
            br = interactive.get("button_reply") or {}
            interactive_id = br.get("id")
            if not text:
                text = br.get("title") or ""
        elif int_type == "list_reply":
            lr = interactive.get("list_reply") or {}
            interactive_id = lr.get("id")
            if not text:
                text = lr.get("title") or ""

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

    phone_e164 = "+" + to if to and not to.startswith("+") else to

    # ── Onboarding: intercept new users before they reach the agent ────────────
    from kapso.onboarding import needs_onboarding, process_onboarding, get_profile
    if needs_onboarding(phone_e164):
        process_onboarding(phone_e164, phone_number_id, to, text, interactive_id)
        return

    agent_payload = {"prompt": text or "", "phone_number": phone_e164}

    # ── Inject user profile into prompt so agent personalizes every reply ──────
    try:
        profile = get_profile(phone_e164)
        if profile:
            name    = profile.get("name") or ""
            gender  = profile.get("gender") or ""
            style   = ", ".join(profile.get("style_tags") or [])
            sizes   = profile.get("sizes_text") or ""
            budget  = profile.get("budget_range") or ""
            colors  = ", ".join(profile.get("favorite_colors") or [])
            brands  = ", ".join(profile.get("favorite_brands") or [])
            city    = profile.get("city") or ""
            profile_ctx = (
                f"[Perfil del usuario — phone: {phone_e164}"
                + (f", nombre: {name}" if name else "")
                + (f", género: {gender}" if gender else "")
                + (f", estilo: {style}" if style else "")
                + (f", tallas: {sizes}" if sizes else "")
                + (f", presupuesto: {budget}" if budget else "")
                + (f", colores favoritos: {colors}" if colors else "")
                + (f", marcas favoritas: {brands}" if brands else "")
                + (f", ciudad: {city}" if city else "")
                + "]\n\n"
            )
            agent_payload["prompt"] = profile_ctx + (agent_payload.get("prompt") or "")
    except Exception as e:
        print(f"[handler] profile injection error (ignored): {e}")

    if image_url:
        if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and TRYON_RAW_BUCKET:
            try:
                r = requests.get(image_url, timeout=30)
                r.raise_for_status()
                image_bytes = r.content
                if image_bytes:
                    from supabase import create_client  # type: ignore[import-not-found]
                    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
                    ct = (r.headers.get("content-type") or "").lower()
                    ext = "jpeg"
                    if "png" in ct:
                        ext = "png"
                    elif "webp" in ct:
                        ext = "webp"
                    path = f"{phone_e164}/_incoming_{uuid.uuid4().hex[:12]}.{ext}"
                    supabase.storage.from_(TRYON_RAW_BUCKET).upload(
                        path, image_bytes, {"content-type": ct or "image/jpeg"}
                    )
                    res = supabase.storage.from_(TRYON_RAW_BUCKET).create_signed_url(
                        path, TRYON_SIGNED_URL_TTL
                    )
                    signed = (res or {}).get("signedURL") or (res or {}).get("signedUrl")
                    if signed:
                        image_url = signed
            except Exception as e:
                print(f"[DEBUG] Upload to raw bucket failed, using Kapso URL: {e}")
        agent_payload["image_url"] = image_url

    # Send a quick acknowledgment so the user knows the bot is working
    # (important for Perplexity queries that can take 10–20s)
    _ack_base = f"{KAPSO_API_BASE}/meta/whatsapp/{WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    _ack_headers = {"Content-Type": "application/json", "X-API-Key": KAPSO_API_KEY}
    try:
        httpx.post(
            _ack_base,
            json={"messaging_product": "whatsapp", "to": to, "type": "text",
                  "text": {"body": "Buscando las mejores opciones para ti... 🔍"}},
            headers=_ack_headers,
            timeout=5.0,
        )
    except Exception:
        pass  # ack failure must never block the main reply

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
