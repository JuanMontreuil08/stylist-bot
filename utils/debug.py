import httpx

url = "https://imagenes-generales-018787439815.s3.us-east-1.amazonaws.com/prendas/cortaviento_gris.png"
r = httpx.head(url, follow_redirects=True)
content_type = (r.headers.get("content-type") or "").lower().split(";")[0].strip()

if content_type in ("image/jpeg", "image/png"):
    print("OK:", content_type)
else:
    print("No es imagen válida:", content_type)