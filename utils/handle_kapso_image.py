import requests

def convert_kapso_image_to_bytes(image_url: str) -> tuple[bytes, str]:

    # Extract the image id from the kapso url (last part of the url)
    image_id = image_url.split("/")[-1].split(".")[0]

    # Read the image from the url
    response = requests.get(image_url, stream=True)
    response.raise_for_status()
    image_bytes = response.content

    return (image_bytes, "jpeg")