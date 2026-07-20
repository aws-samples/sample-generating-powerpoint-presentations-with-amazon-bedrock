"""
Lambda handler function to generate summary from Bedrock knowledge base.

This module implements a Lambda function that integrates with Amazon Bedrock
to generate text summaries based on knowledge base content.
"""

import json
import logging
import os
import re
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize boto3 session and clients
region = os.environ["AWS_REGION"]
session = boto3.Session(region_name=region)
bedrock_agent_client = session.client(service_name="bedrock-agent-runtime", region_name=region)

# Load guardrail configuration from environment variables (created via CDK)
guardrail_id = os.environ.get("GUARDRAIL_ID")
guardrail_version = os.environ.get("GUARDRAIL_VERSION")
if not guardrail_id or not guardrail_version:
    logger.warning("GUARDRAIL_ID or GUARDRAIL_VERSION not set. Guardrails will not be applied.")
    guardrail_id, guardrail_version = None, None
else:
    logger.info(f"Using guardrail from env: {guardrail_id}, version: {guardrail_version}")


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


def validate_input(prompt: str, kb_id: str, model_id: str) -> None:
    """
    Validate input parameters for the Lambda function.

    Args:
        prompt (str): The text prompt to validate
        kb_id (str): The knowledge base ID to validate
        model_id (str): The model ID to validate

    Raises:
        ValueError: If any of the input parameters are invalid
    """
    if not prompt or not isinstance(prompt, str):
        logger.error("Prompt must be a non-empty string", exc_info=True)
        raise ValueError("Prompt must be a non-empty string")

    if not kb_id or not isinstance(kb_id, str):
        logger.error("Knowledge base ID must be a non-empty string", exc_info=True)
        raise ValueError("Knowledge base ID must be a non-empty string")

    if not re.match(r'^[A-Za-z0-9]{1,20}$', kb_id):
        logger.error(f"Invalid kb_id format: {kb_id}", exc_info=True)
        raise ValueError("Knowledge base ID must be alphanumeric, max 20 characters")

    if not model_id or not isinstance(model_id, str):
        logger.error("Model ID must be a non-empty string", exc_info=True)
        raise ValueError("Model ID must be a non-empty string")


def generate_text_kb(
    prompt: str, kb_id: str, model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"
) -> Dict[str, Any]:
    """
    Retrieve information from knowledge base and generate a response from Bedrock LLM.

    This function queries an Amazon Bedrock knowledge base using the provided prompt
    and generates a response using the specified model.

    Args:
        prompt (str): Text prompt for the query
        kb_id (str): Knowledge base ID to query against
        model_id (str): The Bedrock model ID to use for generating the response.
            Defaults to "anthropic.claude-3-sonnet-20240229-v1:0"

    Returns:
        Dict ((str, Any)): A dictionary containing the generated summary and
            citation chunks with their corresponding source references

    Raises:
        ClientError: If there's an error communicating with the Bedrock service
        ValueError: If the input parameters are invalid
        Exception: For other unexpected errors
    """
    logger.info("Generating summary using knowledge base and bedrock model")

    try:
        validate_input(prompt, kb_id, model_id)
        if guardrail_id is None:
            logger.warning("Guardrails are not configured. Proceeding without content filtering.")
            generation_config = {"inferenceConfig": {"textInferenceConfig": {}}}
        else:
            generation_config = {
                "guardrailConfiguration": {"guardrailId": guardrail_id, "guardrailVersion": guardrail_version},
                "inferenceConfig": {"textInferenceConfig": {}},
            }
        response = bedrock_agent_client.retrieve_and_generate(
            input={"text": prompt},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "generationConfiguration": generation_config,
                    "knowledgeBaseId": kb_id,
                    "modelArn": model_id,
                    "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 5}},
                },
            },
        )
        if response.get("guardrailAction") == "INTERVENED":
            logger.warning("Guardrail action: INTERVENED. Response may be incomplete or filtered.")
        logger.info(
            f"Generated Response. Length: {len(response['output']['text'])}, Citations: {len(response['citations'])}"
        )
        return response

    except ClientError as err:
        error_msg = f"Bedrock service error: {err.response['Error']['Code']}: {err.response['Error']['Message']}"
        logger.error(error_msg, exc_info=True, stack_info=True)
        raise
    except ValueError as err:
        error_msg = f"Input validation error: {str(err)}"
        logger.error(error_msg, exc_info=True, stack_info=True)
        raise
    except Exception as err:
        error_msg = f"Unexpected error in generate_text_kb: {str(err)}"
        logger.error(error_msg, exc_info=True, stack_info=True)
        raise


def format_bedrock_response(lambda_event: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a dictionary for the Bedrock response. Amazon Bedrock expects
    a response from your Lambda function that matches the following format.
    The response consists of parameters returned from the API operation. The agent can
    use the response from the Lambda function for further orchestration or to help
    it return a response to the customer.

    Args:
        event (Dict[str, Any]): The original event dictionary
        body_response (Dict[str, Any]): The bedrock response dictionary

    Returns:
        Dict[str, Any]: A dictionary containing the response status code, headers, and body
    """

    def extract_sources(chunk):
        """
        Extract sources from retrieved references in a chunk with error handling.

        Args:
            chunk (dict): The chunk containing retrieved references

        Returns:
            dict: Dictionary mapping index to source URI
        """
        try:
            return {
                i: ref["metadata"]["x-amz-bedrock-kb-source-uri"]
                for i, ref in enumerate(chunk.get("retrievedReferences", []))
            }

        except (KeyError, AttributeError) as e:
            logger.error(f"Error extracting sources: {str(e)}")
            return {}

    def create_chunk_data(chunk):
        """
        Create a structured dictionary from a chunk's data with error handling.

        Args:
            chunk (dict): The chunk containing response and reference data

        Returns:
            dict: Formatted chunk data with text and sources
        """
        try:
            return {
                "text": chunk["generatedResponsePart"]["textResponsePart"]["text"],
                "sources": extract_sources(chunk),
            }

        except (KeyError, AttributeError) as e:
            logger.error(f"Error creating chunk data: {str(e)}")
            return {"text": "", "sources": {}}

    def format_response_body(response):
        """
        Format the response body returned by the model.

        Args:
            response (dict): The complete response containing output and citations

        Returns:
            dict: Structured payload with summary text and chunks
        """
        logger.info("Formatting response body")

        try:
            body = {
                "summary_text": response.get("output", {}).get("text", ""),
                "chunks": [create_chunk_data(chunk) for chunk in response.get("citations", [])],
            }
            if response.get("guardrailAction") == "INTERVENED":
                bedrock_response = {"responseState": "REPROMPT", "responseBody": {"TEXT": {"body": json.dumps(body)}}}
            else:
                bedrock_response = {"responseBody": {"TEXT": {"body": json.dumps(body)}}}
            logger.info(f"Formatted response body: {bedrock_response}")
            return bedrock_response

        except Exception as e:
            logger.error(f"Error creating response payload: {str(e)}")
            body = {"chunks": []}
            bedrock_response = {"responseState": "FAILURE", "responseBody": {"TEXT": {"body": json.dumps(body)}}}
            logger.info(f"Formatted response body: {bedrock_response}")
            return bedrock_response

    logger.info("Formatting response for Bedrock")

    try:
        function_response = {
            "actionGroup": lambda_event.get("actionGroup", ""),
            "function": lambda_event.get("function", ""),
            "functionResponse": format_response_body(response),
        }
        session_attributes = lambda_event.get("sessionAttributes", {})
        prompt_session_attributes = lambda_event.get("promptSessionAttributes", {})
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


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler to generate text summaries using Amazon Bedrock.

    This function processes incoming events, validates inputs, and generates
    text summaries using the Bedrock knowledge base.

    Args:
        event (Dict[str, Any]): Lambda event data containing the following keys:
            - prompt (str): The text prompt for generation
            - kb_id (str): The knowledge base ID
            - model_id (str, optional): The model ID to use
        context (Any): Lambda context object

    Returns:
        Dict[str, Any]: A dictionary containing either:
            - The generated summary and citations
            - An error response with appropriate status code and message
    """
    logger.info(f"Processing new event")

    try:
        # Extract and validate the parameters from the event
        flattened_event = flatten_input_event(event)
        prompt = flattened_event.get("prompt")
        kb_id = flattened_event.get("kb_id")
        model_id = os.environ["MODEL_ID"]
        # Generate response body using LLM
        bedrock_response = generate_text_kb(prompt=prompt, kb_id=kb_id, model_id=model_id)
        # Format the response for Bedrock Agent
        payload = format_bedrock_response(lambda_event=event, response=bedrock_response)
        logger.info("Successfully processed request")
        return payload

    except ValueError as e:
        error_msg = f"Invalid input(s): {str(e)}"
        logger.error(error_msg, exc_info=True, stack_info=True)
        return {"error": error_msg}
    except ClientError as e:
        # Log the full error for debugging but return a sanitized message
        error_msg = f"AWS service error: {e.response['Error']['Code']}: {e.response['Error']['Message']}"
        logger.error(error_msg, exc_info=True, stack_info=True)
        return {"error": "Error accessing AWS services. Please try again later."}
    except Exception as e:
        # Log the full error for debugging but return a sanitized message
        logger.error(f"Unexpected error: {str(e)}", exc_info=True, stack_info=True)
        return {"error": "An unexpected error occurred. Please try again later."}
