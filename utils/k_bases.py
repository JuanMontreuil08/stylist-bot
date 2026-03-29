import boto3
import json
import base64
import io
import os
import uuid as _uuid
from dotenv import load_dotenv
from botocore.exceptions import ClientError

load_dotenv()

# Initialize clients
bedrock_runtime = boto3.client('bedrock-runtime')
s3 = boto3.client('s3')
bedrock_agent_mgmt = boto3.client('bedrock-agent', region_name='us-east-1')

# Attributes
ATTRS = ["tipo", "colores_principales", "estilo", "formalidad", "ocasion", "clima", "material", "marca", "funcionalidad", "caracteristicas_distintivas"]

KB_ID = os.getenv("KNOWLEDGE_BASE_ID", "WWTCK7YTRW")
KB_DATA_SOURCE_ID = os.getenv("KB_DATA_SOURCE_ID", "LN9QCVQH87")


def upload_image_to_s3(image_bytes: bytes, bucket_name: str, s3_key: str, content_type: str = "image/jpeg") -> str:
    s3.upload_fileobj(io.BytesIO(image_bytes), bucket_name, s3_key, ExtraArgs={"ContentType": content_type})
    return f"s3://{bucket_name}/{s3_key}"


def generate_image_caption(image_bytes: bytes, content_type: str = "image/jpeg") -> dict:
    prompt = """
    You are a fashion expert. Describe the attributes of this garment so that anyone can clearly understand and distinguish it from others.
    Respond in JSON with these exact keys: tipo, colores_principales, estilo, formalidad, ocasion, clima, material, marca, funcionalidad, caracteristicas_distintivas. JSON only, no markdown. Be specific and avoid ambiguity. Do not use lists or arrays — all values must be strings. If a value would be a list, join it as a comma-separated string. Write all values in English.
    """
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": base64.b64encode(image_bytes).decode()}},
                {"type": "text", "text": prompt}
            ]
        }]
    })
    response = bedrock_runtime.invoke_model(modelId="us.anthropic.claude-sonnet-4-6", body=body)
    raw = json.loads(response['body'].read())['content'][0]['text'].strip()
    # Extract JSON if it comes wrapped in ```
    if "{" in raw:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if end > start:
            raw = raw[start:end]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    out = {k: str(parsed.get(k, "")) for k in ATTRS}
    out["features"] = raw
    return out


def process_and_upload_image(image_bytes: bytes, bucket_name: str, s3_key: str, content_type: str = "image/jpeg") -> dict:
    meta = generate_image_caption(image_bytes, content_type=content_type)
    s3_url = upload_image_to_s3(image_bytes, bucket_name, s3_key, content_type=content_type)

    # Bedrock Knowledge Bases expects a flat dictionary of key: value
    metadata_attributes = {}

    for k, v in meta.items():
        if k == "features":
            continue

        # Data cleaning: Bedrock KB prefers clean strings or numbers
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)

        # Convert to string and clean extra characters
        v = str(v).replace("[", "").replace("]", "").replace("'", "")

        metadata_attributes[k] = v

    # Add the S3 URL
    metadata_attributes["s3_url"] = s3_url

    # The final structure should be only "metadataAttributes" with the flat dictionary
    metadata_body = {
        "metadataAttributes": metadata_attributes
    }

    meta_key = f"{s3_key}.metadata.json"

    # Upload the JSON to S3
    s3.put_object(
        Bucket=bucket_name,
        Key=meta_key,
        Body=json.dumps(metadata_body, ensure_ascii=False),
        ContentType="application/json"
    )

    # Tagging the original file
    s3.put_object_tagging(
        Bucket=bucket_name,
        Key=s3_key,
        Tagging={"TagSet": [{"Key": "analyzed", "Value": "true"}]}
    )

    return {
        "metadata": meta,
        "s3_url": s3_url,
        "metadata_file": f"s3://{bucket_name}/{meta_key}"
    }


def trigger_kb_sync(description: str = "") -> dict:
    """Trigger a Bedrock KB ingestion job to sync newly uploaded S3 files."""
    try:
        resp = bedrock_agent_mgmt.start_ingestion_job(
            knowledgeBaseId=KB_ID,
            dataSourceId=KB_DATA_SOURCE_ID,
            clientToken=_uuid.uuid4().hex + "0",
            description=description or "Web UI upload sync"
        )
        job = resp.get("ingestionJob", {})
        return {"ingestion_job_id": job.get("ingestionJobId"), "status": job.get("status")}
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConflictException':
            # Another job already running — file will be picked up next time
            return {"ingestion_job_id": None, "status": "SKIPPED_ALREADY_RUNNING"}
        raise
