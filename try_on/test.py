"""
Test mínimo para probar OpenVTO (Prompt-Haus/OpenVTO).
Requiere: pip install openvto
Y para usar el provider "google": variables de entorno de Google Vertex AI
(por ejemplo GOOGLE_APPLICATION_CREDENTIALS o GOOGLE_SERVICE_ACCOUNT_KEY).
"""

from openvto import OpenVTO
from openvto.types import ImageModel
from dotenv import load_dotenv

load_dotenv()

def main():
    print("Inicializando cliente OpenVTO (provider=google)...")
    vto = OpenVTO(provider="google", image_model=ImageModel.NANO_BANANA.value)

    # Rutas de ejemplo: sustituye por tus propias imágenes o por assets del repo
    selfie_path = "images/selfie.jpeg"
    posture_path = "images/full_body.jpeg"
    clothes_paths = ["images/shirt.jpeg", "images/pantalon.png"]

    print("Generando avatar (selfie + postura)...")
    avatar = vto.generate_avatar(
        selfie=selfie_path,
        posture=posture_path,
    )
    print("Avatar generado:", type(avatar))

    print("Generando try-on (avatar + prendas)...")
    tryon = vto.generate_tryon(
        avatar=avatar,
        clothes=clothes_paths,
    )
    print("Try-on generado:", type(tryon))

    # Opcional: guardar resultado si tryon tiene .image o similar
    if hasattr(tryon, "image") and tryon.image is not None:
        out_path = "images/try_on_result.jpg"
        with open(out_path, "wb") as f:
            f.write(tryon.image)
        print("Imagen guardada en", out_path)

    print("Listo.")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print("Error: no se encontraron las imágenes.", e)
        print("Pon selfie.jpg, fullbody.jpg, shirt.jpg y pants.jpg en este directorio o usa rutas tuyas.")
    except Exception as e:
        print("Error:", e)
        print("Comprueba GOOGLE_APPLICATION_CREDENTIALS (o credenciales de Vertex AI).")
