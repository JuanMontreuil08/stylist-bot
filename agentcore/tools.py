import os
import json
import traceback
import uuid
import boto3
import requests
from strands import tool
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# Initialize Bedrock client
bedrock_agent = boto3.client('bedrock-agent-runtime', region_name='us-east-1')
KB_ID = os.getenv("KNOWLEDGE_BASE_ID")

# --- Perplexity online product search (inspired by PerplexiCart) ---
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")


class _ProductSource(BaseModel):
    title: str | None = None
    url: str
    snippet: str | None = None


class _ProductRecommendation(BaseModel):
    product_name: str
    summary: str
    pros: list[str]
    cons: list[str]
    estimated_price_range: str | None = None
    cited_sources: list[_ProductSource]


class _OnlineProductSearchResult(BaseModel):
    overall_summary: str
    recommendations: list[_ProductRecommendation]
    comparison: str  # Short comparison between the options (who to choose which, tradeoffs)
    general_tips: list[str] | None = None


def _call_perplexity_product_search(query: str, user_context: str | None, api_key: str) -> str:
    """Call Perplexity Sonar for general product search; returns a plain-text summary for the agent."""
    system_prompt = (
        "You are a helpful shopping advisor. The user is asking for product recommendations or research. "
        "Search the web for relevant products, reviews, and comparisons. "
        "Your response MUST be a JSON object with the exact schema provided. "
        "For each recommendation include: product_name, a short summary, pros, cons, estimated_price_range when possible, and cited_sources with at least one url per product (url is required; title and snippet optional). "
        "In the 'comparison' field write a short paragraph comparing the options: who should choose which product, main tradeoffs, and when to pick one over another. "
        "Keep recommendations actionable and concise. Cite sources for key claims. "
        "If the user provides extra context (budget, preferences, location), take it into account."
    )
    context_line = f" Additional context from the user: {user_context}." if user_context else ""
    user_content = f"Search for products or advice about: {query}.{context_line} Return the result in the required JSON format."

    schema_payload = {"schema": _OnlineProductSearchResult.model_json_schema()}
    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "search_language_filter": ["es"],
        "response_format": {"type": "json_schema", "json_schema": schema_payload},
        "temperature": 0.5,
        "web_search_options": {
            "user_location": {"country": "PE"},
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(PERPLEXITY_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        print(data)
        raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not raw_content:
            return "No response from search."
        result = _OnlineProductSearchResult.model_validate_json(raw_content)
    except requests.exceptions.Timeout:
        return "The product search timed out. Please try a shorter or simpler query."
    except requests.exceptions.HTTPError as e:
        msg = str(e.response.status_code)
        try:
            err = e.response.json()
            msg += f" - {err.get('error', {}).get('message', e.response.text)}"
        except Exception:
            msg += f" - {e.response.text}"
        return f"Search service error: {msg}"
    except (json.JSONDecodeError, Exception) as e:
        return f"Could not parse search results: {e}"

    # Build a clear summary so the agent can relay it with pros, cons, and exact URLs
    parts = [result.overall_summary.strip(), ""]
    for i, rec in enumerate(result.recommendations[:5], 1):
        parts.append(f"--- Producto {i}: {rec.product_name} ---")
        parts.append(rec.summary)
        if rec.pros:
            parts.append("Pros: " + "; ".join(rec.pros[:5]))
        if rec.cons:
            parts.append("Contras: " + "; ".join(rec.cons[:3]))
        if rec.estimated_price_range:
            parts.append(f"Precio aprox: {rec.estimated_price_range}")
        if rec.cited_sources:
            url = rec.cited_sources[0].url.strip()
            parts.append(f"Link (copiar exacto): {url}")
        parts.append("")
    if result.comparison and result.comparison.strip():
        comp = result.comparison.strip().replace("**", "")  # remove markdown for plain WhatsApp
        parts.append("Comparación entre opciones: " + comp)
    if result.general_tips:
        parts.append("\nTips: " + " | ".join(result.general_tips[:3]))
    return "\n".join(parts).strip()

@tool
def search_clothing_catalog(query: str) -> str:
    """Search the internal clothing catalog using AI-powered semantic search. Returns clothing items with detailed metadata (type, colors, style, formality, occasion, etc.) and image URLs from our curated collection."""
    print("[search_clothing_catalog] query:", repr(query))
    
    try:
        response = bedrock_agent.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={'text': query},
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': 10,
                    'overrideSearchType': 'HYBRID',
                    'rerankingConfiguration': {
                        'type': 'BEDROCK_RERANKING_MODEL',
                        'bedrockRerankingConfiguration': {
                            'modelConfiguration': {
                                'modelArn': 'arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0'
                            },
                            'numberOfRerankedResults': 3
                        }
                    }
                }
            }
        )
        
        results = []
        for item in response['retrievalResults']:
            metadata = item['metadata']
            results.append({
                'image_url': metadata.get('s3_url', ''),
                'tipo': metadata.get('tipo', ''),
                'colores': metadata.get('colores_principales', ''),
                'estilo': metadata.get('estilo', ''),
                'formalidad': metadata.get('formalidad', ''),
                'ocasion': metadata.get('ocasion', ''),
                'marca': metadata.get('marca', ''),
                'funcionalidad': metadata.get('funcionalidad', ''),
                'caracteristicas_distintivas': metadata.get('caracteristicas_distintivas', '')
            })
        
        print("[search_clothing_catalog] results:", results)
        return results
    
    except Exception as e:
        traceback.print_exc()
        return f"Error searching catalog: {e}"


@tool
def search_products_online(query: str, user_context: str | None = None) -> str:
    """Search the internet for product recommendations and reviews. Use for products NOT in our catalog (other clothing brands, electronics, skincare, etc.).

    Args:
        query: The main search question—what the user is looking for (e.g. "best running shoes", "crema hidratante piel seca", "chaqueta estilo bomber"). Put only the product/category and style here.
        user_context: Optional. Extra constraints the user mentioned: budget ("presupuesto 50€", "under 100"), preferences ("vegan", "sin perfume"), location ("en España"), or other details. Leave None if they did not give any.

    Returns a short summary with product names, pros/cons, and approximate prices. Do not use for clothing we may have—use search_clothing_catalog first for that."""
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        return "Online product search is not configured (missing PERPLEXITY_API_KEY). I can only search our clothing catalog."
    print("[search_products_online] query:", repr(query), "context:", repr(user_context))
    return _call_perplexity_product_search(query, user_context, api_key)


VOICE_BOT_URL = os.getenv("VOICE_BOT_URL", "").rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
TRYON_INPUTS_BUCKET = os.getenv("TRYON_INPUTS_BUCKET", "tryon-inputs").strip()
TRYON_CLOTHES_BUCKET = os.getenv("TRYON_CLOTHES_BUCKET", "tryon-clothes").strip()
TRYON_SIGNED_URL_TTL_SECONDS = int(os.getenv("TRYON_SIGNED_URL_TTL_SECONDS", "3600"))


def _sanitize_garment_slug(description: str, max_len: int = 25) -> str:
    """Turn garment description into a safe filename slug (lowercase, no spaces, limited length)."""
    if not (description or "").strip():
        return "prenda"
    s = "".join(c if c.isalnum() or c in "-_" else " " for c in (description or "").strip())
    s = "-".join(s.split()).lower()[:max_len].strip("-")
    return s or "prenda"


@tool
def initiate_voice_call(phone_number: str, opening_message: str) -> str:
    """Start a voice call to the user. Use it whenever the user asks to be called, follows up by phone, or wants to contact by voice.

    Args:
        phone_number: Phone number in E.164 format (ej. +51995132783).
        opening_message: Opening message that the bot will say when connecting the call. You must generate it from the conversation (e.g. "Hello, here Benito from The North Face. I'm calling you for your inquiry about sizes. How can I help you?"). Do not leave this field empty.
    """
    if not VOICE_BOT_URL:
        return "Voice calls are not configured (missing VOICE_BOT_URL in the environment)."
    opening_message = (opening_message or "").strip()
    if not opening_message:
        return "Error: opening_message is required. Generate an opening message for the call."
    try:
        r = requests.post(
            f"{VOICE_BOT_URL}/api/start-call",
            json={"phone_number": phone_number, "opening_message": opening_message},
            timeout=15,
        )
        data = r.json() if "application/json" in (r.headers.get("content-type") or "") else {}
        if data.get("ok"):
            return "Call initiated. We will contact you soon."
        return data.get("error", f"Error {r.status_code}") or "Call could not be initiated."
    except requests.RequestException as e:
        return f"Could not connect to the voice service: {e}"


@tool
def tryon_get_profile(phone_number: str) -> str:
    """Fetch try-on profile for a user and return signed URLs to inputs (selfie + full body).

    Args:
        phone_number: Phone number in E.164 format (e.g. +51995132783). Used as user_id.

    Returns:
        JSON string:
        {
          "ok": true,
          "exists": true|false,
          "user_id": "+519...",
          "has_selfie": true|false,
          "has_full_body": true|false,
          "selfie_path": "...",
          "full_body_path": "...",
          "selfie_url": "https://...signed...",
          "full_body_url": "https://...signed..."
        }
    """
    user_id = (phone_number or "").strip()
    if not user_id:
        return json.dumps({"ok": False, "error": "phone_number is required"})
    if not user_id.startswith("+"):
        user_id = "+" + user_id

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return json.dumps(
            {
                "ok": False,
                "error": "Supabase not configured (missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY).",
            }
        )

    try:
        from supabase import create_client  # type: ignore[import-not-found]

        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

        # Support either schema: phone_e164 OR user_id as the primary key column.
        data = None
        for col in ("phone_e164", "user_id"):
            try:
                resp = (
                    supabase.table("tryon_profiles")
                    .select("selfie_path,full_body_path")
                    .eq(col, user_id)
                    .limit(1)
                    .execute()
                )
                rows = getattr(resp, "data", None) or []
                if rows:
                    data = rows[0]
                    break
            except Exception:
                # Column might not exist; try next one.
                continue

        if not data:
            return json.dumps(
                {
                    "ok": True,
                    "exists": False,
                    "user_id": user_id,
                    "has_selfie": False,
                    "has_full_body": False,
                }
            )

        selfie_path = (data.get("selfie_path") or "").strip() or None
        full_body_path = (data.get("full_body_path") or "").strip() or None

        has_selfie = bool(selfie_path)
        has_full_body = bool(full_body_path)

        selfie_url = None
        full_body_url = None
        storage = supabase.storage.from_(TRYON_INPUTS_BUCKET)
        if selfie_path:
            selfie_url = storage.create_signed_url(selfie_path, TRYON_SIGNED_URL_TTL_SECONDS).get("signedURL")
        if full_body_path:
            full_body_url = storage.create_signed_url(full_body_path, TRYON_SIGNED_URL_TTL_SECONDS).get("signedURL")

        return json.dumps(
            {
                "ok": True,
                "exists": True,
                "user_id": user_id,
                "has_selfie": has_selfie,
                "has_full_body": has_full_body,
                "selfie_path": selfie_path,
                "full_body_path": full_body_path,
                "selfie_url": selfie_url,
                "full_body_url": full_body_url,
            }
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": f"Failed to fetch try-on profile: {e}"})


@tool
def tryon_upload_photo(
    phone_number: str,
    image_url: str,
    photo_type: str,
    garment_description: str = "",
) -> str:
    """Save a try-on photo for the user. Call this when the user sends an image and you have classified it.

    The image is stored in the bucket and table corresponding to the type:
    - selfie → tryon-inputs bucket, profile selfie_path
    - full_body → tryon-inputs bucket, profile full_body_path
    - garment → tryon-clothes bucket, filename from garment_description (e.g. polo-negro_abc123.jpg)

    Args:
        phone_number: User phone in E.164 (e.g. +51995132783).
        image_url: URL of the image (e.g. Supabase signed URL from raw bucket). Must be downloadable.
        photo_type: One of "selfie", "full_body", "garment".
        garment_description: Short description for garments only (e.g. "polo negro", "short beige"). Used to build the filename. Leave empty for selfie/full_body.

    Returns:
        Success message or error JSON.
    """
    phone_number = (phone_number or "").strip()
    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number
    image_url = (image_url or "").strip()
    photo_type = (photo_type or "").strip().lower()
    garment_description = (garment_description or "").strip()

    if not image_url:
        return json.dumps({"ok": False, "error": "image_url is required"})
    if photo_type not in ("selfie", "full_body", "garment"):
        return json.dumps({"ok": False, "error": f"photo_type must be selfie, full_body, or garment; got {photo_type!r}"})
    if photo_type == "garment" and not garment_description:
        return json.dumps({"ok": False, "error": "garment_description is required when photo_type is garment"})

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return json.dumps({"ok": False, "error": "Supabase not configured"})

    try:
        r = requests.get(image_url, timeout=30)
        r.raise_for_status()
        image_bytes = r.content
        if not image_bytes:
            return json.dumps({"ok": False, "error": "Image download returned empty body"})

        ct = (r.headers.get("content-type") or "").lower()
        ext = "jpeg"
        if "png" in ct:
            ext = "png"
        elif "webp" in ct:
            ext = "webp"

        from supabase import create_client  # type: ignore[import-not-found]
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

        if photo_type == "selfie":
            path = f"{phone_number}/selfie.{ext}"
            bucket = TRYON_INPUTS_BUCKET
            supabase.storage.from_(bucket).upload(path, image_bytes, {"content-type": ct or "image/jpeg"})
            row = {"phone_e164": phone_number, "selfie_path": path}
            try:
                supabase.table("tryon_profiles").upsert(row, on_conflict="phone_e164").execute()
            except Exception:
                try:
                    supabase.table("tryon_profiles").upsert({**row, "user_id": phone_number}, on_conflict="user_id").execute()
                except Exception:
                    supabase.table("tryon_profiles").insert(row).execute()
            return json.dumps({"ok": True, "message": "Selfie guardada correctamente.", "path": path})

        if photo_type == "full_body":
            path = f"{phone_number}/full_body.{ext}"
            bucket = TRYON_INPUTS_BUCKET
            supabase.storage.from_(bucket).upload(path, image_bytes, {"content-type": ct or "image/jpeg"})
            row = {"phone_e164": phone_number, "full_body_path": path}
            try:
                supabase.table("tryon_profiles").upsert(row, on_conflict="phone_e164").execute()
            except Exception:
                try:
                    supabase.table("tryon_profiles").upsert({**row, "user_id": phone_number}, on_conflict="user_id").execute()
                except Exception:
                    supabase.table("tryon_profiles").insert(row).execute()
            return json.dumps({"ok": True, "message": "Foto full body guardada correctamente.", "path": path})

        # garment
        slug = _sanitize_garment_slug(garment_description)
        uid = uuid.uuid4().hex[:12]
        path = f"{phone_number}/{slug}_{uid}.{ext}"
        bucket = TRYON_CLOTHES_BUCKET
        supabase.storage.from_(bucket).upload(path, image_bytes, {"content-type": ct or "image/jpeg"})
        return json.dumps({"ok": True, "message": f"Prenda guardada: {slug}.", "path": path})

    except requests.RequestException as e:
        return json.dumps({"ok": False, "error": f"Failed to download image: {e}"})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"ok": False, "error": str(e)})