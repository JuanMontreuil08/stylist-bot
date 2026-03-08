import boto3
import json
import base64
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize clients
bedrock_runtime = boto3.client('bedrock-runtime')
s3 = boto3.client('s3')

# Attributes
ATTRS = ["tipo", "colores_principales", "estilo", "formalidad", "ocasion", "clima", "material", "marca", "funcionalidad", "caracteristicas_distintivas"]


def upload_image_to_s3(image_url, bucket_name, s3_key):
    with open(image_url, 'rb') as f:
        extra = {"ContentType": "image/jpeg"}

        if s3_key.lower().endswith(".png"):
            extra["ContentType"] = "image/png"

        s3.upload_fileobj(f, bucket_name, s3_key, ExtraArgs=extra)

    return f"s3://{bucket_name}/{s3_key}"


def generate_image_caption(image_url):
    with open(image_url, 'rb') as f:
        image_bytes = f.read()
    prompt = """
    Eres un experto en moda y te encargas de describir los atributos de una prenda de vestir con el objetivo de que una persona común pueda entender la prenda y diferenciarla de otras.
    Describe esta prenda en JSON con estas claves (español): tipo, colores_principales, estilo, formalidad, ocasion, clima, material, marca, funcionalidad, caracteristicas distintivas. Solo JSON, sin markdown. Evita ser ambiguo al describir la prenda. Se lo más específico posible. No utilices listas o arrays. Los valores deben ser strings. Si un valor es una lista, conviertelo a una cadena de texto separada por comas.
    """
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}},
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


def process_and_upload_image(image_url, bucket_name, s3_key):
    meta = generate_image_caption(image_url)
    s3_url = upload_image_to_s3(image_url, bucket_name, s3_key)

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




