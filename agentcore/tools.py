import os
import json
import traceback
import uuid
import io
import tempfile
import boto3
import requests
from strands import tool
from dotenv import load_dotenv
import unicodedata
from openvto import OpenVTO
from openvto.types import ImageModel
from PIL import Image

load_dotenv()

# Initialize Bedrock client
bedrock_agent = boto3.client('bedrock-agent-runtime', region_name='us-east-1')
KB_ID = os.getenv("KNOWLEDGE_BASE_ID")

# --- SerpAPI Google Shopping product search ---
SERPAPI_URL = "https://serpapi.com/search"


def _call_serpapi_product_search(query: str, user_context: str | None, api_key: str) -> str:
    """Call SerpAPI Google Shopping for product search; returns verified product data for the agent."""
    combined_query = f"{query} {user_context}" if user_context else query
    params = {
        "engine": "google_shopping",
        "q": combined_query,
        "hl": "es",
        "num": 3,
        "api_key": api_key,
    }

    try:
        response = requests.get(SERPAPI_URL, params=params)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as e:
        return f"Search service error: {e.response.status_code} - {e.response.text[:200]}"
    except Exception as e:
        return f"Could not fetch search results: {e}"

    results = data.get("shopping_results", [])
    print(f"[search_products_online] SerpAPI returned {len(results)} results for: {repr(combined_query)}")

    if not results:
        return "No products found on Google Shopping for this query. Try rephrasing or use search_clothing_catalog."

    parts = [f"Google Shopping results for: {combined_query}", ""]
    for i, item in enumerate(results[:3], 1):
        title = item.get("title", "")
        price = item.get("price", "Price not available")
        source = item.get("source", "")
        link = item.get("product_link", "") or item.get("link", "")
        rating = item.get("rating")
        reviews = item.get("reviews")

        parts.append(f"--- Product {i}: {title} ---")
        parts.append(f"Price: {price}")
        if source:
            parts.append(f"Store: {source}")
        if link:
            parts.append(f"URL: {link}")
        if rating:
            review_str = f" ({reviews} reviews)" if reviews else ""
            parts.append(f"Rating: {rating}/5{review_str}")
        parts.append("")

    parts.append(
        "INSTRUCTION: The prices and URLs above are real verified data from Google Shopping — "
        "share them exactly as they appear, do not modify them. "
        "Use your own knowledge about these brands/products to add pros, cons, and recommendations."
    )
    return "\n".join(parts).strip()

@tool
def search_clothing_catalog(query: str) -> str:
    """Search the internal clothing catalog using AI-powered semantic search. Returns clothing items with detailed metadata (type, colors, style, formality, occasion, etc.) and image URLs from our curated collection. IMPORTANT: always translate the query to English before calling this tool, regardless of the language the user wrote in."""
    print("[search_clothing_catalog] query:", repr(query))
    from agentcore.context import send_search_ack
    send_search_ack()
    
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

    IMPORTANT: Always translate both `query` and `user_context` to English before calling this tool,
    regardless of the language the user wrote in. English queries return significantly better Google Shopping results.
    Example: user says "casaca north face gris hombre" → query="north face gray men's jacket"

    Args:
        query: The main search question in ENGLISH (e.g. "gray north face men's jacket", "moisturizing cream dry skin", "bomber style jacket"). Product/category and style only.
        user_context: Optional. Brief context in ENGLISH (1 line) from the user profile. Include: gender, size (S/M/L/XL), and style. Example: "men size M casual". Do NOT include budget, use cases, colors, brands, or overly specific details. Leave None if no profile context exists.

    Returns real product listings from Google Shopping with verified prices and URLs. Do not use for clothing we may have—use search_clothing_catalog first for that."""
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return "Online product search is not configured (missing SERPAPI_KEY). I can only search our clothing catalog."
    print("[search_products_online] query:", repr(query), "context:", repr(user_context))

    from agentcore.context import send_search_ack
    send_search_ack()

    return _call_serpapi_product_search(query, user_context, api_key)


_SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

# Array fields in user_profiles that accept comma-separated values
_ARRAY_FIELDS = {"style_tags", "favorite_colors", "favorite_brands"}
# All updatable fields
_PROFILE_FIELDS = {
    "name", "gender", "style_tags", "sizes_text",
    "budget_range", "favorite_colors", "favorite_brands", "city",
}


@tool
def update_user_profile(phone_number: str, field: str, value: str) -> str:
    """Update a field in the user's style profile. Call this when the user mentions a preference,
    size change, new brand, budget update, or any personal detail worth remembering.

    Args:
        phone_number: User's phone in E.164 format (e.g. +51995132783).
        field: Field to update. One of: name, gender, style_tags, sizes_text, budget_range,
               favorite_colors, favorite_brands, city.
        value: New value as a plain string. For array fields (style_tags, favorite_colors,
               favorite_brands) use comma-separated values, e.g. "casual, deportivo".
    """
    phone_number = (phone_number or "").strip()
    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number
    field = (field or "").strip().lower()
    value = (value or "").strip()

    if field not in _PROFILE_FIELDS:
        return f"Unknown field '{field}'. Valid fields: {', '.join(sorted(_PROFILE_FIELDS))}"
    if not value:
        return "value cannot be empty."
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return "Profile storage not configured."

    try:
        from supabase import create_client
        sb = create_client(_SUPABASE_URL, _SUPABASE_KEY)
        parsed_value: list[str] | str
        if field in _ARRAY_FIELDS:
            parsed_value = [v.strip() for v in value.split(",") if v.strip()]
        else:
            parsed_value = value
        sb.table("user_profiles").upsert(
            {"user_id": phone_number, field: parsed_value},
            on_conflict="user_id",
        ).execute()
        return f"Profile updated: {field} = {parsed_value}"
    except Exception as e:
        return f"Could not update profile: {e}"


VOICE_BOT_URL = os.getenv("VOICE_BOT_URL", "").rstrip("/")
S3_IMAGE_BUCKET = os.getenv("S3_IMAGE_BUCKET", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
TRYON_INPUTS_BUCKET = os.getenv("TRYON_INPUTS_BUCKET", "tryon-inputs").strip()
TRYON_CLOTHES_BUCKET = os.getenv("TRYON_CLOTHES_BUCKET", "tryon-clothes").strip()
TRYON_RESULTS_BUCKET = os.getenv("TRYON_RESULTS_BUCKET", "tryon-results").strip()
TRYON_SIGNED_URL_TTL_SECONDS = int(os.getenv("TRYON_SIGNED_URL_TTL_SECONDS", "3600"))


def _sanitize_garment_slug(description: str, max_len: int = 25) -> str:
    """Turn garment description into a safe filename slug (ASCII-only, lowercase, no spaces, limited length)."""
    if not (description or "").strip():
        return "prenda"
    # Normalize to ASCII (e.g. "selección" -> "seleccion")
    s = unicodedata.normalize("NFKD", (description or "").strip())
    s = s.encode("ascii", "ignore").decode("ascii")
    # Keep underscores as the only separator; convert any hyphens to underscores.
    s = s.replace("-", "_")
    s = "".join(c if c.isalnum() or c == "_" else " " for c in s)
    s = "_".join(s.split()).lower()[:max_len].strip("_")
    return s or "garment"


@tool
def initiate_voice_call(phone_number: str, opening_message: str) -> str:
    """Start a voice call to the user. Use it whenever the user asks to be called, follows up by phone, or wants to contact by voice.

    Args:
        phone_number: Phone number in E.164 format (e.g. +51995132783).
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
    - garment → tryon-clothes bucket, filename from garment_description (e.g. polo_rojo_lacoste_abc123.jpg)

    Args:
        phone_number: User phone in E.164 (e.g. +51995132783).
        image_url: URL of the image (e.g. Supabase signed URL from raw bucket). Must be downloadable.
        photo_type: One of "selfie", "full_body", "garment".
        garment_description: Garment name in strict format `tipo_color_marca` (e.g. `polo_rojo_lacoste`, `pantalon_beige_zara`, `camiseta_azul_nike`). Used to build the filename. Leave empty for selfie/full_body.

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
            file_opts = {"content-type": ct or "image/jpeg", "upsert": "true"}
            supabase.storage.from_(bucket).upload(path, image_bytes, file_opts)
            row = {"user_id": phone_number, "selfie_path": path}
            try:
                supabase.table("tryon_profiles").upsert(row, on_conflict="user_id").execute()
            except Exception:
                supabase.table("tryon_profiles").insert(row).execute()
            return json.dumps({"ok": True, "message": "Selfie saved successfully. If you already had one, it was updated.", "path": path})

        if photo_type == "full_body":
            path = f"{phone_number}/full_body.{ext}"
            bucket = TRYON_INPUTS_BUCKET
            file_opts = {"content-type": ct or "image/jpeg", "upsert": "true"}
            supabase.storage.from_(bucket).upload(path, image_bytes, file_opts)
            row = {"user_id": phone_number, "full_body_path": path}
            try:
                supabase.table("tryon_profiles").upsert(row, on_conflict="user_id").execute()
            except Exception:
                supabase.table("tryon_profiles").insert(row).execute()
            return json.dumps({"ok": True, "message": "Full body photo saved successfully. If you already had one, it was updated.", "path": path})

        # garment
        slug = _sanitize_garment_slug(garment_description)
        uid = uuid.uuid4().hex[:12]
        path = f"{phone_number}/{slug}_{uid}.{ext}"
        bucket = TRYON_CLOTHES_BUCKET
        supabase.storage.from_(bucket).upload(path, image_bytes, {"content-type": ct or "image/jpeg"})
        return json.dumps({"ok": True, "message": f"Garment saved: {slug}.", "path": path})

    except requests.RequestException as e:
        return json.dumps({"ok": False, "error": f"Failed to download image: {e}"})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"ok": False, "error": str(e)})


@tool
def tryon_generate(
    phone_number: str,
    selfie_url: str,
    full_body_url: str,
    polo_url: str,
    pantalon_url: str,
) -> str:
    phone_number = (phone_number or "").strip()
    if not phone_number:
        return json.dumps({"ok": False, "error": "phone_number is required"})

    # ✅ Escribir credenciales ANTES de cualquier import o uso de Google
    creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not creds_json:
        return json.dumps({"ok": False, "error": "GOOGLE_APPLICATION_CREDENTIALS_JSON not set"})

    creds_path = "/tmp/service_account.json"
    with open(creds_path, "w") as f:
        f.write(creds_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

    # ✅ Validar que el JSON es válido
    try:
        creds_data = json.loads(creds_json)
        print(f"[tryon_generate] GCP project: {creds_data.get('project_id', 'unknown')}")
    except json.JSONDecodeError as e:
        return json.dumps({"ok": False, "error": f"Invalid JSON in GOOGLE_APPLICATION_CREDENTIALS_JSON: {e}"})

    try:
        if not phone_number.startswith("+"):
            phone_number = "+" + phone_number

        # Re-sign selfie/full_body inside the tool to avoid InvalidJWT caused by
        # long signed URLs being "transported" through the LLM.
        selfie_storage_url = None
        full_body_storage_url = None

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            return json.dumps({"ok": False, "error": "Supabase not configured"})

        from supabase import create_client  # type: ignore[import-not-found]

        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        profile_row = None
        for col in ("phone_e164", "user_id"):
            try:
                resp = (
                    supabase.table("tryon_profiles")
                    .select("selfie_path,full_body_path")
                    .eq(col, phone_number)
                    .limit(1)
                    .execute()
                )
                rows = getattr(resp, "data", None) or []
                if rows:
                    profile_row = rows[0]
                    break
            except Exception:
                continue

        if profile_row:
            selfie_db_path = (profile_row.get("selfie_path") or "").strip() or None
            full_body_db_path = (profile_row.get("full_body_path") or "").strip() or None

            storage = supabase.storage.from_(TRYON_INPUTS_BUCKET)
            if selfie_db_path:
                res = storage.create_signed_url(selfie_db_path, TRYON_SIGNED_URL_TTL_SECONDS)
                selfie_storage_url = (res or {}).get("signedURL") or (res or {}).get("signedUrl")

            if full_body_db_path:
                res = storage.create_signed_url(full_body_db_path, TRYON_SIGNED_URL_TTL_SECONDS)
                full_body_storage_url = (res or {}).get("signedURL") or (res or {}).get("signedUrl")

        # Fallback: use incoming URLs only if we couldn't re-sign from profile.
        if not selfie_storage_url and selfie_url:
            selfie_storage_url = selfie_url
        if not full_body_storage_url and full_body_url:
            full_body_storage_url = full_body_url

        def _to_download_url(url: str) -> str:
            url = (url or "").strip()
            if url.startswith("s3://"):
                remainder = url[len("s3://"):]
                bucket, _, key = remainder.partition("/")
                if bucket and key is not None:
                    return f"https://{bucket}.s3.amazonaws.com/{key}"
            return url

        def _download_to_temp_image(url: str, prefix: str) -> str:
            url = _to_download_url((url or "").strip())
            if not url:
                raise ValueError(f"Missing {prefix} image URL")
            r = requests.get(url, timeout=45)
            r.raise_for_status()
            img_bytes = r.content or b""
            if not img_bytes:
                raise ValueError(f"Empty image downloaded for {prefix}")

            ct = (r.headers.get("content-type") or "").lower()
            tmp_ext = "jpg"
            if "png" in ct:
                tmp_ext = "png"

            with tempfile.NamedTemporaryFile(suffix=f".{tmp_ext}", delete=False) as f:
                tmp_path = f.name
                if tmp_ext == "jpg":
                    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                    img.save(tmp_path, format="JPEG")
                else:
                    f.write(img_bytes)
            return tmp_path

        clothes_paths: list[str] = []

        missing = []
        if not selfie_storage_url:
            missing.append("selfie")
        if not full_body_storage_url:
            missing.append("full_body")
        if missing:
            return json.dumps(
                {"ok": False, "error": f"Missing required profile photos in tryon_profiles: {', '.join(missing)}"}
            )

        selfie_path = _download_to_temp_image(selfie_storage_url, "selfie")
        full_body_path = _download_to_temp_image(full_body_storage_url, "full_body")

        polo_url = (polo_url or "").strip()
        pantalon_url = (pantalon_url or "").strip()

        if polo_url:
            clothes_paths.append(_download_to_temp_image(polo_url, "polo"))
        if pantalon_url:
            clothes_paths.append(_download_to_temp_image(pantalon_url, "pantalon"))

        if not clothes_paths:
            return json.dumps({"ok": False, "error": "At least one garment URL is required."})

        # ✅ Usar FLASH en lugar de NANO_BANANA — más estable y ampliamente disponible
        vto = OpenVTO(provider="google", image_model=ImageModel.NANO_BANANA.value)
        
        avatar = vto.generate_avatar(selfie=selfie_path, posture=full_body_path)
        tryon = vto.generate_tryon(avatar=avatar, clothes=clothes_paths)

        if not hasattr(tryon, "image") or tryon.image is None:
            return json.dumps({"ok": False, "error": "OpenVTO did not return an output image"})

        result_bytes = tryon.image

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            return json.dumps({"ok": False, "error": "Supabase not configured"})
        if not TRYON_RESULTS_BUCKET:
            return json.dumps({"ok": False, "error": "TRYON_RESULTS_BUCKET not configured"})

        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        out_path = f"{phone_number}/tryon_{uuid.uuid4().hex}.jpg"
        supabase.storage.from_(TRYON_RESULTS_BUCKET).upload(
            out_path,
            result_bytes,
            {"content-type": "image/jpeg"},
        )
        res = supabase.storage.from_(TRYON_RESULTS_BUCKET).create_signed_url(
            out_path, TRYON_SIGNED_URL_TTL_SECONDS
        )
        signed = (res or {}).get("signedURL") or (res or {}).get("signedUrl")
        if not signed:
            return json.dumps({"ok": False, "error": "Could not create signed URL"})
        return signed

    except Exception as e:
        traceback.print_exc()
        return json.dumps({"ok": False, "error": str(e)})


def _normalize_token(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.replace("-", "_").replace(" ", "_")
    return s


def _predominant_color(color: str) -> str:
    # If there are multiple colors separated by underscores, keep the first one.
    norm = _normalize_token(color)
    return (norm.split("_")[0] if norm else "").strip()


@tool
def tryon_search_garments(
    phone_number: str,
    garment_type: str,
    color: str,
    brand: str,
    min_match_parts: int = 2,
    limit: int = 5,
) -> str:
    """
    Search user garments in Supabase Storage by filename tokens: <type>_<color>_<brand>_<uuid>.<ext>.
    MVP: return matches where at least `min_match_parts` of (type,color,brand) match.

    The `color` passed in by the agent can contain multiple colors; keep only the predominant one.
    """
    phone_number = (phone_number or "").strip()
    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number
    garment_type = _normalize_token(garment_type)
    color_q = _predominant_color(color)
    brand_q = (_normalize_token(brand).split("_")[0] if brand else "").strip()

    if not phone_number or not garment_type:
        return json.dumps({"ok": False, "error": "phone_number and garment_type are required"})

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return json.dumps({"ok": False, "error": "Supabase not configured"})

    try:
        from supabase import create_client  # type: ignore[import-not-found]

        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

        prefix = f"{phone_number}/"
        objects = supabase.storage.from_(TRYON_CLOTHES_BUCKET).list(path=prefix) or []

        matches: list[dict] = []
        for obj in objects:
            # supabase-py can return dict keys like: name, id, path (depends on client version)
            name = (obj or {}).get("name") or (obj or {}).get("id") or (obj or {}).get("path")
            if not name:
                continue

            filename = str(name)
            # If list() returns only filename, it won't include the prefix; if it includes full key, it will.
            if filename.startswith(prefix):
                filename = filename[len(prefix):]

            if "/" in filename:
                # In case list returns nested paths, skip.
                continue

            if "." not in filename:
                continue

            base = filename.rsplit(".", 1)[0]  # slug + _ + uuid
            parts = base.split("_")
            if len(parts) < 4:
                continue

            # Assume last token is uuid; everything before is slug: <type>_<color>_<brand>
            slug_parts = parts[:-1]
            if len(slug_parts) < 3:
                continue

            type_s = _normalize_token(slug_parts[0])
            brand_s = _normalize_token(slug_parts[-1]).split("_")[0] if slug_parts[-1] else ""
            color_combined = "_".join(slug_parts[1:-1])
            color_s = _predominant_color(color_combined)

            score = 0
            # MVP (pragmatic): treat "camiseta" as "polo" during search for polos.
            type_match = False
            if type_s == garment_type:
                type_match = True
            elif garment_type == "polo" and type_s == "camiseta":
                type_match = True

            if type_match:
                score += 1
            if color_s and color_q and color_s == color_q:
                score += 1
            if brand_s and brand_q and brand_s == brand_q:
                score += 1

            if score >= int(min_match_parts):
                storage_path = f"{phone_number}/{filename}"
                s3_uri = f"s3://{TRYON_CLOTHES_BUCKET}/{storage_path}"
                display_type = type_s
                if garment_type == "polo" and type_s == "camiseta":
                    display_type = "polo"
                description = f"{display_type}_{color_s}_{brand_s}".strip("_")
                matches.append(
                    {
                        "description": description,
                        "storage_path": storage_path,
                        "s3_uri": s3_uri,
                        "score": score,
                    }
                )

        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        matches = matches[: max(0, int(limit))]

        storage = supabase.storage.from_(TRYON_CLOTHES_BUCKET)
        for m in matches:
            storage_path = m.get("storage_path")
            if not storage_path:
                continue
            try:
                res = storage.create_signed_url(storage_path, TRYON_SIGNED_URL_TTL_SECONDS)
                signed = (res or {}).get("signedURL") or (res or {}).get("signedUrl")
                if signed:
                    m["supabase_url"] = signed
            except Exception:
                # If signed URL fails, still return the storage_path.
                pass

        return json.dumps(
            {
                "ok": True,
                "phone_number": phone_number,
                "bucket": TRYON_CLOTHES_BUCKET,
                "query": {
                    "garment_type": garment_type,
                    "color": color_q,
                    "brand": brand_q,
                },
                "total_matches": len(matches),
                "matches": matches,
            }
        )

    except Exception as e:
        traceback.print_exc()
        return json.dumps({"ok": False, "error": str(e)})