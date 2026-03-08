from k_bases import process_and_upload_image
from dotenv import load_dotenv
import os
from PIL import Image

load_dotenv()

S3_IMAGE_BUCKET = os.getenv("S3_IMAGE_BUCKET")

# count corrupted images and processed
processed = 0
errors = 0

"""
#try one image
for image in os.listdir("/Users/juanmontreuil/Desktop/Startup v2/images"):
    try:
        with Image.open(f"/Users/juanmontreuil/Desktop/Startup v2/images/{image}") as img:
            img.verify()

        result = process_and_upload_image(
            image_url=f"/Users/juanmontreuil/Desktop/Startup v2/images/{image}",
            bucket_name=S3_IMAGE_BUCKET,
            s3_key=f"prendas/{image}"
        )

        processed += 1
        print(f"Processed {processed} images")
    except Exception as e:
        errors += 1
        print(f"Error processing {image}: {e}")

print(f"Processed {processed} images")
print(f"Errors {errors} images")
"""

image_url = "/Users/juanmontreuil/Desktop/Startup v2/images/pantalon.png"
result = process_and_upload_image(
    image_url=image_url,
    bucket_name=S3_IMAGE_BUCKET,
    s3_key="prendas/pantalon.png"
)

print(result)