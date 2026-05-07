"""
pptx-gen utility functions
"""

import ast
import base64
import io
import os
import json
import logging
import secrets
from typing import Any, Tuple

import backoff
import boto3
import jsonschema
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from jsonschema import validate
from PIL import Image
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize the Amazon Bedrock runtime client
config = Config(connect_timeout=60, read_timeout=60, retries={"max_attempts": 3, "mode": "adaptive"})
bedrock_client = boto3.client(service_name="bedrock-runtime", config=config)
bedrock_agent_client = boto3.client(service_name="bedrock-agent-runtime")

# Load guardrail configuration from environment variables (created via CDK)
guardrail_id = os.environ.get("GUARDRAIL_ID")
guardrail_version = os.environ.get("GUARDRAIL_VERSION")
if not guardrail_id or not guardrail_version:
    logger.warning("GUARDRAIL_ID or GUARDRAIL_VERSION not set. Guardrails will not be applied.")
    guardrail_id, guardrail_version = None, None
else:
    logger.info(f"Using guardrail from env: {guardrail_id}, version: {guardrail_version}")


class ImageError(Exception):
    "Custom exception for errors returned by Amazon Titan Image Generator G1"

    def __init__(self, message):
        self.message = message


def backoff_handler(details):
    """Handler from https://pypi.org/project/backoff/"""
    """print(
        "Backing off {wait:0.1f} seconds after {tries} tries "
        "calling function {target} with kwargs "
        "{kwargs}".format(**details)
    )"""


def secure_randint(a, b):
    return a + secrets.randbelow(b - a + 1)


@backoff.on_exception(
    backoff.expo,
    (ClientError, ReadTimeoutError),
    max_time=secure_randint(500, 1000),
    on_backoff=backoff_handler,
    giveup=lambda e: "ThrottlingException" not in str(e)
    and "ReadTimeoutError" not in str(e)
    and "ModelTimeoutException" not in str(e),
)
@retry(
    retry=retry_if_exception_type(ReadTimeoutError),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def invoke_llm_text(prompt: str = "", model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0") -> Tuple[Any, bool]:
    """Initialize the Amazon Bedrock runtime client. Invoke Claude 3 with the text prompt"""
    try:
        max_tokens = 512
        response = bedrock_client.invoke_model(
            modelId=model_id,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": prompt}],
                        }
                    ],
                }
            ),
        )
        result = json.loads(response.get("body").read())

    except ReadTimeoutError as e:
        logger.error(f"Timeout error, retrying: {str(e)}")
        raise  # This will trigger the retry
    except ClientError as err:
        logger.error(
            "Couldn't invoke Claude 3 Sonnet. Here's why: %s: %s",
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
        raise

    return result.get("content", []), result.get("usage", [])


def validate_text_length(slide_json: dict) -> dict:
    """Truncate text that exceeds length limits."""
    MAX_LENGTHS = {"title": 50, "subtitle": 75, "text": 300, "speaker_notes": 200}

    for field, max_length in MAX_LENGTHS.items():
        if field in slide_json and isinstance(slide_json[field], str):
            text = slide_json[field]
            if len(text) > max_length:
                # For bullet points, truncate each point
                if field == "text" and "***" in text:
                    bullets = text.split("***")
                    truncated_bullets = []
                    for bullet in bullets:
                        if bullet.strip():
                            truncated_bullet = bullet.strip()[:60]  # 60 chars per bullet
                            truncated_bullets.append(truncated_bullet)
                    slide_json[field] = "*** ".join(truncated_bullets)
                else:
                    # For other fields, simple truncation
                    slide_json[field] = text[:max_length]
                logger.info(f"Truncated {field} text from {len(text)} to {max_length} characters")

    return slide_json


def is_valid_text_gen_json(raw_json: str = {}) -> bool:
    """Validate the JSON string can be serialized into a python dict

    Args:
        raw_json (str): JSON string. Defaults to {}.

    Returns:
        bool: whether the json string is a valid python dictionary
    """

    def validateJSON(jsonData):
        try:
            json.loads(jsonData)
        except ValueError:
            return False
        return True

    isValid = validateJSON(raw_json)
    return isValid


def validate_slide_json(slide_json: dict = {}) -> bool:
    """Validate the schema of the slide json dictionary

    Args:
        slide_json (dict): slide dictionary. Defaults to {}.

    Returns:
        bool: whether the slide json dictionary is valid
    """
    try:
        # Check for a valid dictionary
        if not isinstance(slide_json, dict):
            logger.warning(f"Invalid slide_json type: {type(slide_json)}")
            slide_json = ast.literal_eval(str(slide_json))

        # Log the incoming data for debugging
        logger.info(f"Validating slide: Format={slide_json.get('slideFormat')}, Title={slide_json.get('title')}")

        # Describe what kind of json you expect.
        expected_schema = {
            "type": "object",
            "properties": {
                "slide_n": {"type": "number"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "text": {"type": "string"},
                "speaker_notes": {"type": "string"},
                "slideFormat": {"type": "string"},
            },
            "required": [
                "slide_n",
                "title",
                "subtitle",
                "speaker_notes",
                "slideFormat",
            ],
        }

        # Add "text" to required properties only if it's not an image-only slide or image and text slide
        slide_format = slide_json.get("slideFormat", "")
        if slide_format not in ["Slide with image only", "Slide with image and text"]:
            expected_schema["required"].append("text")
        else:
            # For image slides, ensure text is at least an empty string if present
            if "text" not in slide_json:
                slide_json["text"] = ""
                logger.info(f"Added empty text field for slide type: {slide_format}")

        # Validate and log results
        slide_json = ast.literal_eval(str(slide_json))
        logger.info(f"expected_schema: {expected_schema['properties'].keys()}, slide_json: {slide_json.keys()}")
        validate(instance=slide_json, schema=expected_schema)

        logger.info(f"Successfully validated slide {slide_json.get('slide_n')}")
        return True

    except jsonschema.exceptions.ValidationError as err:
        logger.error(f"JSON slide validation error for slide {slide_json.get('slide_n', 'unknown')}: {err}")
        # Attempt to fix common issues
        try:
            # Ensure all required fields exist with at least empty strings
            for field in ["title", "subtitle", "speaker_notes", "text"]:
                if field not in slide_json:
                    slide_json[field] = ""
                    logger.info(f"Added missing field '{field}' to slide")

            # Revalidate after fixes
            validate(instance=slide_json, schema=expected_schema)
            logger.info(f"Successfully fixed and validated slide {slide_json.get('slide_n')}")
            return True
        except Exception as fix_err:
            logger.error(f"Unable to fix validation issues: {fix_err}")
            return False

    except ValueError:
        logger.error("JSON slide validation error: Unknown")
        return False
    except Exception as e:
        logger.error(f"Unexpected error validating slide: {str(e)}")
        return False


@backoff.on_exception(
    backoff.expo,
    (ClientError, ReadTimeoutError),
    max_time=secure_randint(500, 1000),
    on_backoff=backoff_handler,
    giveup=lambda e: "ThrottlingException" not in str(e)
    and "ReadTimeoutError" not in str(e)
    and "ModelTimeoutException" not in str(e),
)
def generate_text(
    prompt: str = "", N_SLIDES: int = 1, model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"
) -> Tuple[list, dict]:
    """Invoke Claude 3 with the text prompt

    Args:
        prompt (str): text prompt. Defaults to "".
        N_SLIDES (int): number of slides to generate. Defaults to 1.
        model_id (str): the bedrock model to use. Defaults to "anthropic.claude-3-sonnet-20240229-v1:0".

    Returns:
        Tuple[list, dict]: the model response and usage
    """
    try:
        max_tokens = int(4096 / 15 * N_SLIDES)
        response = bedrock_client.invoke_model(
            modelId=model_id,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": prompt}],
                        }
                    ],
                }
            ),
        )

        # Process and print the response
        result = json.loads(response.get("body").read())
        input_tokens = result["usage"]["input_tokens"]
        output_tokens = result["usage"]["output_tokens"]
        output_list = result.get("content", [])
        invocation_details = {
            "Invocation details": {
                "Max tokens": max_tokens,
                "Input token length": input_tokens,
                "Output token length": output_tokens,
                "Responses Returned": len(output_list),
            }
        }
        logger.info(invocation_details)

    except ClientError as err:
        logger.error(
            "Couldn't invoke Claude 3 Sonnet. Here's why: %s: %s",
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
        raise

    return result.get("content", []), result.get("usage", [])


@backoff.on_exception(
    backoff.expo,
    (ClientError, ReadTimeoutError),
    max_time=secure_randint(500, 1000),
    on_backoff=backoff_handler,
    giveup=lambda e: "ThrottlingException" not in str(e)
    and "ReadTimeoutError" not in str(e)
    and "ModelTimeoutException" not in str(e),
)
def generate_text_kb(
    prompt: str = "", kb_id: str = "", N_SLIDES: int = 1, model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"
) -> str:
    """retrieve information from knowledge base and generate a response from bedrock LLM

    Args:
        prompt (str): text prompt. Defaults to "".
        kb_id (str): knowledge base ID. Defaults to "".
        N_SLIDES (int): number of slides to generate. Defaults to 1.
        model_id (str): the bedrock model to use in generating a response. Defaults to "anthropic.claude-3-sonnet-20240229-v1:0".

    Returns:
        str: text response
    """
    try:
        max_tokens = int(4096 / 15 * N_SLIDES)

        if guardrail_id is None:
            logger.warning("Guardrails are not configured. Proceeding without content filtering.")
            generation_config = {"inferenceConfig": {"textInferenceConfig": {"maxTokens": max_tokens}}}
        else:
            generation_config = {
                "guardrailConfiguration": {"guardrailId": guardrail_id, "guardrailVersion": guardrail_version},
                "inferenceConfig": {"textInferenceConfig": {"maxTokens": max_tokens}},
            }

        response = bedrock_agent_client.retrieve_and_generate(
            input={"text": prompt},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "generationConfiguration": generation_config,
                    "knowledgeBaseId": kb_id,
                    "modelArn": model_id,
                    "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 7}},
                },
            },
        )

        if response.get("guardrailAction") == "INTERVENED":
            logger.warning("Guardrail action: INTERVENED. Response may be incomplete or filtered.")
            return "INTERVENED"

        logger.info(
            f"Generated Response. Length: {len(response['output']['text'])}, Citations: {len(response['citations'])}"
        )

        result = response.get("output")

    except ClientError as err:
        logger.error(
            "Couldn't invoke Claude 3 Sonnet. Here's why: %s: %s",
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
        raise

    return result.get("text", [])


def check_text_generation_consistency(slides_list: list, N_SLIDES: int) -> Tuple[int, bool]:
    """
    Check if generated slides match requested count.

    Args:
        slides_list (list): List of generated slides
        N_SLIDES (int): Number of slides originally requested to be generated

    Returns:
        Tuple: Number of generated slides (int), Whether generated slides equals requested slides (bool)
    """
    generated_n_slides, is_consistent = len(slides_list), len(slides_list) == N_SLIDES
    if not is_consistent:
        logger.warning(f"Inconsistent number of slides! Requested: {N_SLIDES}, Generated: {generated_n_slides}")
    return generated_n_slides, is_consistent


def _create_blank_png_b64(width: int, height: int, r: int, g: int, b: int) -> str:
    """Create a solid-color PNG and return as base64 string."""
    import struct, zlib
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    raw = b''
    for y in range(height):
        raw += b'\x00' + bytes([r, g, b]) * width
    idat = chunk(b'IDAT', zlib.compress(raw))
    iend = chunk(b'IEND', b'')
    return base64.b64encode(header + ihdr + idat + iend).decode()


def generate_bedrock_image(
    img_prompt: str = "",
    current_slide_format_json: dict = {},
    image_placeholder=None,
    cwd: str = "",
    bkg: str = "",
    high_res_images: bool = False,
) -> None:
    """Generate an image using Stability AI inpaint model (text-to-image via blank canvas).

    Args:
        img_prompt (str): prompt for generating the image. Defaults to "".
        current_slide_format_json (dict): slide dictionary with image configuration values. Defaults to {}.
        image_placeholder (_type_): _description_. Defaults to None.
        cwd (str): current working directory. Defaults to "".
        bkg (str): path postfix. Defaults to "".
    """
    model_id = os.environ["IMAGE_MODEL_ID"]
    height = current_slide_format_json["image_height"]
    width = current_slide_format_json["image_width"]

    # Create blank white image and white mask (white = area to inpaint/replace)
    image_b64 = _create_blank_png_b64(width, height, 255, 255, 255)
    mask_b64 = _create_blank_png_b64(width, height, 255, 255, 255)

    body = json.dumps(
        {
            "prompt": img_prompt,
            "output_format": "jpeg",
            "image": image_b64,
            "mask": mask_b64,
            "seed": secrets.randbelow(int(1e9)),
        }
    )

    try:
        image_bytes = generate_image(model_id=model_id, body=body)
        with io.BytesIO(image_bytes) as bio:
            with Image.open(bio) as img:
                img.save(cwd + "/test_image" + bkg + ".jpg")
                if image_placeholder:
                    image_placeholder.insert_picture(cwd + "/test_image" + bkg + ".jpg")
    except ClientError as err:
        message = err.response["Error"]["Message"]
        logger.error(f"A client error occurred: {message}")
    except ImageError as err:
        logger.error(err.message)
    else:
        logger.info(f"Finished generating image with Bedrock model {model_id}.")


@backoff.on_exception(
    backoff.expo,
    (ClientError, ReadTimeoutError),
    max_time=secure_randint(500, 1000),
    on_backoff=backoff_handler,
    giveup=lambda e: "ThrottlingException" not in str(e)
    and "ReadTimeoutError" not in str(e)
    and "ModelTimeoutException" not in str(e),
)
def invoke_bedrock_model(model_id: str, body: dict) -> dict:
    """
    Invoke the Amazon Bedrock model for image generation.

    Args:
        model_id (str): The ID of the Bedrock model to use
        body (dict): The request body containing image generation parameters

    Returns:
        dict: The raw response from the Bedrock model

    Raises:
        ImageError: If there's an error invoking the model
    """
    try:
        response = bedrock_client.invoke_model(
            body=body,
            modelId=model_id,
            accept="application/json",
            contentType="application/json",
        )
        return json.loads(response.get("body").read())
    except ImageError as err:
        logger.error(err.message)


def process_image_response(response_body: dict) -> bytes:
    """
    Process the response from the Bedrock model and extract the image data.

    Args:
        response_body (dict): The response body from the Bedrock model

    Returns:
        bytes: The decoded image bytes

    Raises:
        ImageError: If there's an error in the response or during image processing
    """
    if error := response_body.get("error"):
        raise ImageError(f"Image generation error: {error}")

    try:
        # Support both 'images' (array) and 'image' (single) response formats
        base64_image = None
        if response_body.get("images"):
            base64_image = response_body["images"][0]
        elif response_body.get("image"):
            base64_image = response_body["image"]

        if not base64_image:
            raise ImageError("No image data found in response")

        base64_bytes = base64_image.encode("ascii")
        return base64.b64decode(base64_bytes)
    except ImageError:
        raise
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        raise ImageError(f"Failed to process image response: {str(e)}")


def generate_image(model_id: str, body: dict) -> bytes:
    """
    Generate an image using Amazon Titan Image Generator G1 model.

    Args:
        model_id (str): The ID of the Bedrock model to use
        body (dict): The request body containing image generation parameters

    Returns:
        bytes: The generated image as bytes

    Raises:
        ImageError: If there's an error during image generation or processing
    """
    logger.info(f"Generating image with Bedrock model {model_id}")

    try:
        response_body = invoke_bedrock_model(model_id, body)
        image_bytes = process_image_response(response_body)
        return image_bytes
    except ImageError as e:
        logger.error(f"Error generating image: {e}")
        raise  # Re-raise ImageError instances
    except Exception as e:
        logger.error(f"Unexpected error during image generation: {e}")
        raise ImageError(f"Unexpected error during image generation: {str(e)}")
