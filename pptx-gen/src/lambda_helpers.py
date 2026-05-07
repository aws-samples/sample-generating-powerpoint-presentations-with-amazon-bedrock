"""
Lambda helper functions
"""

import ast
import json
import logging
import os
import re
import uuid
import tempfile
from typing import Any, Dict, List, Tuple

import boto3
from botocore.config import Config
from pptx import Presentation
from src.prompt import (
    create_initial_prompt,
    fix_initial_json_prompt,
    fix_slide_json_prompt,
)
from src.session_state_template import SessionState
from src.utils import (
    check_text_generation_consistency,
    generate_text,
    generate_text_kb,
    invoke_llm_text,
    validate_slide_json,
)

s3_client = boto3.client("s3", config=Config(signature_version="s3v4"))
lambda_tmp = tempfile.mkdtemp()

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def flatten_input_event(event: dict) -> Dict:
    """
    Flatten a dictionary containing a parameters list into a simple key-value dictionary.

    Args:
        data (dict): Dictionary containing a parameters list of dictionaries

    Returns:
        dict: Flattened dictionary with direct key-value pairs
    """
    logger.info(f"Flattening input event: {event}")

    if not event:
        logger.error("Lambda input event is empty")
        raise ValueError("Input lambda event must not be empty")

    if not isinstance(event, dict) or "parameters" not in event:
        error_msg = f"Lambda input event does not contain a field named 'parameters'"
        logger.error(error_msg, exc_info=True)
        raise ValueError("Input lambda event must contain a 'parameters' field")

    result = {}
    for param in event.get("parameters", []):
        if isinstance(param, dict) and "name" in param and "value" in param:
            result[param["name"]] = param["value"]

    logger.info(f"Flattened input event: {result}")

    return result


def validate_event(event: Dict[str, Any]) -> Tuple[SessionState, Dict[str, Any]]:
    """
    Validate input event parameters and create session state.

    Expected event structure:
    {
        "tenant_id": "285fe629-ab2f-456f-af76-f29183cc989c",
        "kb_id": "TGLJABQLG8",
        "topic": "Benefits of cloud computing with AWS.",
        "slides": 6,
        "model": "Sonnet",
        "agenda": true,
        "thankyou": true,
        "images": "Low resolution",
        "background": true,
        "background_prompt": "abstract technology background",
        "thumbnails": false,
        "name": "J. Doe",
        "title": "Chief Presentation Officer",
        "company": "AnyCompany",
        "email": "j.doe@anycompany.com"
    }
    """
    logger.info("Validating event parameters")

    # Validate tenant_id format (UUID)
    tenant_id = event.get("tenant_id", "")
    if tenant_id and not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', tenant_id):
        logger.error(f"Invalid tenant_id format: {tenant_id}")
        return None, {"error": "Invalid tenant_id format. Must be a valid UUID."}

    # Validate kb_id format (alphanumeric, 10 chars)
    kb_id = event.get("kb_id", "")
    if kb_id and not re.match(r'^[A-Za-z0-9]{1,20}$', kb_id):
        logger.error(f"Invalid kb_id format: {kb_id}")
        return None, {"error": "Invalid kb_id format. Must be alphanumeric, max 20 characters."}

    # Validate n_slides range
    n_slides = event.get("slides", 6)
    try:
        n_slides = int(n_slides)
        if n_slides < 2 or n_slides > 20:
            return None, {"error": "Number of slides must be between 2 and 20."}
    except (ValueError, TypeError):
        return None, {"error": "Invalid slides value. Must be a number."}

    ss = SessionState(
        tenant_id=event.get("tenant_id"),
        kb_id=event.get("kb_id"),
        topic=event.get("topic"),
        n_slides=event.get("slides", 6),
        selected_llm=event.get("model", "Sonnet"),
        chosen_llm=os.environ["MODEL_ID"],
        create_agenda_checkbox=event.get("agenda", False),
        create_thankyou_checkbox=event.get("thankyou", False),
        selected_generate_images=event.get("images", "Low resolution"),
        selected_generate_bkg=event.get("background", False),
        bkg_prompt=event.get(
            "background_prompt",
            "digital presentation wallpaper, dark blue tone, uniform color, corner gradient towards orange",
        ),
        generate_thumbnails=event.get("thumbnails", False),
        your_full_name=event.get("name", "J. Doe"),
        your_title=event.get("title", "Chief Presentation Officer"),
        your_company=event.get("company", "AnyCompany"),
        your_contact_info=event.get("email", "j.doe@anycompany.com"),
        debug_mode=event.get("debug_mode", False),
    )

    if not ss.topic or not ss.tenant_id or not ss.kb_id:
        logger.error("Tenant ID, Knowledge Base ID, and Topic are required")
        return None, {"error": "Tenant ID, Knowledge Base ID, and Topic are required"}

    logger.info(f"Validated event parameters: {ss}")
    return ss, None


def format_bedrock_response(event: Dict[str, Any], body_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a dictionary for the Bedrock response. Amazon Bedrock expects
    a response from your Lambda function that matches the following format.
    The response consists of parameters returned from the API operation. The agent can
    use the response from the Lambda function for further orchestration or to help
    it return a response to the customer.

    Args:
        event (Dict[str, Any]): The original event dictionary
        body_response (Dict[str, Any]): The response body dictionary

    Returns:
        Dict[str, Any]: A dictionary containing the response status code, headers, and body
    """
    logger.info("Formatting response for Bedrock")

    try:
        if body_response.get("guardrailAction") == "INTERVENED":
            bedrock_response = {
                "responseState": "REPROMPT",
                "responseBody": {"TEXT": {"body": json.dumps(body_response)}},
            }
        elif body_response.get("error") == "Tenant ID, Knowledge Base ID, and Topic are required":
            bedrock_response = {
                "responseState": "FAILURE",
                "responseBody": {"TEXT": {"body": json.dumps(body_response)}},
            }
        else:
            bedrock_response = {"responseBody": {"TEXT": {"body": json.dumps(body_response)}}}
        # bedrock_response = {"responseBody": {"TEXT": {"body": json.dumps(body_response)}}}
        function_response = {
            "actionGroup": event.get("actionGroup", ""),
            "function": event.get("function", ""),
            "functionResponse": bedrock_response,
        }
        session_attributes = event.get("sessionAttributes", {})
        prompt_session_attributes = event.get("promptSessionAttributes", {})

        action_response = {
            "messageVersion": "1.0",
            "response": function_response,
            "sessionAttributes": session_attributes,
            "promptSessionAttributes": prompt_session_attributes,
        }

        logger.info(f"Formatted Bedrock response: {action_response}")
        return action_response

    except Exception as e:
        logger.error(f"Error formatting Bedrock response: {str(e)}")
        action_response = {"messageVersion": "1.0", "response": {"error": "Error formatting Bedrock response."}}
        logger.info(f"Formatted Bedrock response: {action_response}")
        raise


def generate_initial_text(ss: SessionState) -> Tuple[str, bool]:
    """Generate initial text content for the presentation."""
    initial_summary = generate_text_kb(prompt=ss.topic, kb_id=ss.kb_id, N_SLIDES=ss.n_slides, model_id=ss.chosen_llm)
    logger.info(f"initial_summary: {initial_summary}")

    if initial_summary == "INTERVENED":
        return initial_summary, False

    initial_prompt = create_initial_prompt(N_SLIDES=ss.n_slides, summary=initial_summary)
    text_gen_result, usage = generate_text(prompt=initial_prompt, N_SLIDES=ss.n_slides, model_id=ss.chosen_llm)
    ss.n_input_tokens += usage["input_tokens"]
    ss.n_output_tokens += usage["output_tokens"]

    if len(text_gen_result) != 1:
        logger.warning("There was a problem with the answer from Claude, try again")
        return "", False

    logger.info(f"Raw result: {text_gen_result}")
    return text_gen_result[0]["text"], True


def fix_json_content(raw_json: str, ss: SessionState) -> Tuple[str, bool]:
    """Attempt to fix and validate JSON content."""

    def clean_json_string(json_str: str) -> str:
        """Clean JSON string by removing markdown markers and whitespace"""
        return json_str.strip().lstrip("```json").rstrip("```").strip()

    def manual_json_repair(json_str: str) -> str:
        """Manual JSON string repair attempts"""
        # Remove any trailing commas before closing braces or brackets
        json_str = re.sub(r",(\s*[}\]])", r"\1", json_str)
        # Add missing quotes around keys
        json_str = re.sub(r"([{,]\s*)(\w+)(:)", r'\1"\2"\3', json_str)
        # Replace single quotes with double quotes
        json_str = json_str.replace("'", '"')
        return json_str

    def try_parse_json(json_str: str) -> Tuple[str, bool]:
        """Attempt to parse and validate JSON"""
        try:
            parsed_json = json.loads(json_str)
            return json.dumps(parsed_json, indent=2), True
        except json.JSONDecodeError as e:
            logger.error(f"JSON Parse Error: {str(e)}")
            return json_str, False

    logger.info("Attempting to validate and fix JSON content...")

    # First attempt: Try to parse the cleaned JSON
    cleaned_json = clean_json_string(raw_json)
    formatted_json, is_valid = try_parse_json(cleaned_json)

    if is_valid:
        logger.info("JSON successfully validated on first attempt!")
        return formatted_json, True

    # Second attempt: Try manual repair
    repaired_json = manual_json_repair(cleaned_json)
    formatted_json, is_valid = try_parse_json(repaired_json)

    if is_valid:
        logger.info("JSON successfully repaired manually!")
        return formatted_json, True

    # If manual repair fails, attempt fixes using the model
    max_fix_attempts = 2
    for i in range(max_fix_attempts):
        logger.info(f"Attempt {i + 1} to fix JSON using model...")

        try:
            # Generate fixed JSON using the model
            raw_gen_result_fixed, usage = generate_text(
                prompt=fix_initial_json_prompt(raw_json),
                N_SLIDES=ss.n_slides,
                model_id=ss.chosen_llm,
            )

            # Update token usage
            ss.n_input_tokens += usage["input_tokens"]
            ss.n_output_tokens += usage["output_tokens"]

            # Get the fixed JSON content and clean it
            fixed_json = raw_gen_result_fixed[0]["text"]
            cleaned_fixed_json = clean_json_string(fixed_json)

            # Try to repair and parse the fixed JSON
            repaired_json = manual_json_repair(cleaned_fixed_json)
            formatted_json, is_valid = try_parse_json(repaired_json)

            if is_valid:
                logger.info("JSON successfully fixed!")
                return formatted_json, True

            logger.warning("Generated JSON still contains errors, will attempt another fix...")
            # Print first 200 characters of problematic JSON for debugging
            logger.info(f"Problematic JSON preview: {cleaned_fixed_json[:200]}...")

        except Exception as e:
            logger.error(f"Error during fix attempt {i + 1}: {str(e)}")

    logger.error("\nFailed to fix JSON after all attempts")
    return raw_json, False


def validate_json_structure(raw_json: str) -> Tuple[Dict, bool]:
    """Validate and parse JSON structure."""
    try:
        return ast.literal_eval(raw_json), True
    except ValueError:
        try:
            # Bad hack to try to force valid json in case of truncation
            return ast.literal_eval(raw_json + '\\"}]}'), True
        except ValueError:
            logger.error("Errors encountered in the generation, please try again")
            return {}, False


# def generate_presentation_content(ss: SessionState) -> Tuple[List[Dict], bool]:
#    """Main presentation generation loop."""
#    print("GENERATING PRESENTATION... The application will try to self-heal in case of errors")
#
#    max_generation_attempts = 2
#    generation_attempts = 0
#    generated_content = []
#    success = False
#
#    while generation_attempts < max_generation_attempts:
#        # Generate initial content
#        raw_generated_json, gen_success = generate_initial_text(ss)
#        if not gen_success:
#            generation_attempts += 1
#            continue
#
#        # Fix and validate JSON
#        raw_generated_json, json_valid = fix_json_content(raw_generated_json, ss)
#        if not json_valid:
#            generation_attempts += 1
#            continue
#
#        # Parse JSON structure
#        validated_json_content, struct_valid = validate_json_structure(raw_generated_json)
#        if not struct_valid:
#            ss.valid_generation = False
#            generation_attempts += 1
#            continue
#
#        # Check slides consistency
#        json_content_list = validated_json_content.get("slides", [])
#        _, is_consistent = check_text_generation_consistency(json_content_list, ss.n_slides)
#
#        if is_consistent:
#            ss.valid_generation = True
#            generated_content = json_content_list
#            success = True
#            break
#
#        generation_attempts += 1
#
#    if not success:
#        return None, {
#            "error": f"Failed to generate valid presentation content after {max_generation_attempts} attempts"
#        }
#
#    return generated_content, None


def generate_presentation_content(ss: SessionState) -> Tuple[List[Dict], bool]:
    """Main presentation generation loop."""
    logger.info("Generating presentation.. The application will try to self-heal in case of errors")

    max_generation_attempts = 2
    generation_attempts = 0
    generated_content = []
    success = False

    # Adjust number of slides based on agenda and thank you slides
    actual_slides_needed = ss.n_slides
    if ss.create_agenda_checkbox:
        actual_slides_needed -= 1
        logger.info(f"Adjusting for agenda slide. Slides needed: {actual_slides_needed}")
    if ss.create_thankyou_checkbox:
        actual_slides_needed -= 1
        logger.info(f"Adjusting for thank you slide. Slides needed: {actual_slides_needed}")

    while generation_attempts < max_generation_attempts:
        # Generate initial content with adjusted slide count
        ss.n_slides = actual_slides_needed  # Temporarily adjust n_slides
        raw_generated_json, gen_success = generate_initial_text(ss)
        ss.n_slides = (
            actual_slides_needed + (1 if ss.create_agenda_checkbox else 0) + (1 if ss.create_thankyou_checkbox else 0)
        )  # Restore original n_slides

        if not gen_success:
            if raw_generated_json == "INTERVENED":
                return None, {"guardrailAction": "INTERVENED"}
            generation_attempts += 1
            continue

        # Fix and validate JSON
        raw_generated_json, json_valid = fix_json_content(raw_generated_json, ss)
        if not json_valid:
            generation_attempts += 1
            continue

        # Parse JSON structure
        validated_json_content, struct_valid = validate_json_structure(raw_generated_json)
        if not struct_valid:
            ss.valid_generation = False
            generation_attempts += 1
            continue

        # Check slides consistency against adjusted slide count
        json_content_list = validated_json_content.get("slides", [])
        _, is_consistent = check_text_generation_consistency(json_content_list, actual_slides_needed)

        if is_consistent:
            ss.valid_generation = True
            generated_content = json_content_list
            success = True
            break

        generation_attempts += 1

    if not success:
        return None, {
            "error": f"Failed to generate valid presentation content after {max_generation_attempts} attempts"
        }

    logger.info(f"Successfully generated {len(generated_content)} slides (excluding agenda/thank you slides)")
    return generated_content, None


def fix_slide_json(slide_json: Dict, ss: SessionState) -> Dict:
    """Attempt to fix invalid slide JSON using the LLM."""
    try:
        # Initial validation
        logger.info(f"\nProcessing slide: {slide_json.get('slide_n')}")
        logger.debug(f"Original slide data: {json.dumps(slide_json, indent=2)}")

        is_valid_json_slide = validate_slide_json(slide_json=slide_json)
        logger.info(f"Initial validation result: {is_valid_json_slide}")

        if is_valid_json_slide:
            return slide_json

        # Attempt fixes if validation fails
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                logger.info(f"Attempt {attempt + 1} to fix slide json")

                # Generate fixed version using LLM
                fix_attempt_results, usage = generate_text(
                    prompt=fix_slide_json_prompt(slide_json),
                    N_SLIDES=ss.n_slides,
                    model_id=ss.chosen_llm,
                )

                # Update token usage
                ss.n_input_tokens += usage["input_tokens"]
                ss.n_output_tokens += usage["output_tokens"]

                # Process the fixed JSON
                fixed_json = fix_attempt_results[0]["text"]
                logger.info(f"Fixed slide json attempt: {fixed_json}")

                # Try to parse and validate the fixed JSON
                try:
                    parsed_json = ast.literal_eval(fixed_json)
                    is_valid_fix = validate_slide_json(slide_json=parsed_json)

                    if is_valid_fix:
                        logger.info(f"JSON slide fix successful on attempt {attempt + 1}")
                        return parsed_json
                except (ValueError, SyntaxError) as e:
                    logger.error(f"Error parsing fixed JSON on attempt {attempt + 1}: {str(e)}")
                    continue

            except Exception as e:
                logger.error(f"Error during fix attempt {attempt + 1}: {str(e)}")
                if attempt == max_attempts - 1:
                    # On last attempt, try to salvage the original with defaults
                    logger.info("Attempting to salvage original slide with defaults")
                    salvaged_json = ensure_required_fields(slide_json)
                    if validate_slide_json(slide_json=salvaged_json):
                        return salvaged_json

        # If all attempts fail, log and return original
        logger.warning(f"Failed to fix slide after {max_attempts} attempts")
        return slide_json

    except Exception as e:
        logger.error(f"Unexpected error in fix_slide_json: {str(e)}")
        return ensure_required_fields(slide_json)


def ensure_required_fields(slide_json: Dict) -> Dict:
    """Ensure all required fields exist with at least empty strings."""
    try:
        required_fields = ["title", "subtitle", "text", "speaker_notes", "slideFormat"]
        slide_format = slide_json.get("slideFormat", "")

        # Don't add text field for image-only slides
        if slide_format in ["Slide with image only", "Slide with image and text"]:
            required_fields.remove("text")

        for field in required_fields:
            if field not in slide_json or slide_json[field] is None:
                slide_json[field] = ""
                logger.info(f"Added missing field '{field}' to slide")

        # Ensure slide_n exists and is a number
        if "slide_n" not in slide_json:
            slide_json["slide_n"] = 0
            logger.info("Added default slide_n")

        return slide_json
    except Exception as e:
        logger.error(f"Error in ensure_required_fields: {str(e)}")
        return slide_json


def save_and_upload_presentation(prs: Presentation, ss: SessionState) -> Tuple[str, str]:
    """Save presentation locally and upload to S3 if configured."""
    output_filename = f"output_{str(uuid.uuid4())}.pptx"
    ss.output_path = f"{lambda_tmp}/{output_filename}"
    logger.info(f"Saving presentation to ephemeral storage: {ss.output_path}")
    prs.save(ss.output_path)
    logger.info("Presentation saved successfully")

    presigned_url = None
    if "S3_BUCKET" in os.environ:
        bucket = os.environ["S3_BUCKET"]
        s3_key = f"{ss.tenant_id}/output/{output_filename}"
        logger.info(f"Uploading presentation to S3: {bucket}/{s3_key}")
        s3_client.upload_file(ss.output_path, bucket, s3_key)
        logger.info("Presentation uploaded successfully")

        logger.info("Generating presigned URL")
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=300,  # URL valid for 5 minutes
        )
        logger.info(f"Presigned URL: {presigned_url}")

    return output_filename, presigned_url
