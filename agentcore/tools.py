import os
import json
import traceback
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