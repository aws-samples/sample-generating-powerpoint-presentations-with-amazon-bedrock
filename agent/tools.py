"""
Custom tools for the document generation and summarization supervisor agent.

These tools invoke the existing Lambda functions (PPTX generation and summary
generation) via direct invocation rather than through Bedrock Agents Classic
action groups.
"""

import json
import logging
import os
from typing import Any

import boto3
from botocore.config import Config
from strands import tool

logger = logging.getLogger(__name__)

# Lambda function ARNs from environment variables
PPTX_GEN_FUNCTION_NAME = os.environ.get("PPTX_GEN_FUNCTION_NAME", "")
SUMMARY_GEN_FUNCTION_NAME = os.environ.get("SUMMARY_GEN_FUNCTION_NAME", "")

# Initialize Lambda client at module level
# AWS_REGION is set by AgentCore Runtime in the container environment
_region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
lambda_client = boto3.client(
    "lambda",
    region_name=_region,
    config=Config(read_timeout=900, connect_timeout=10)
)


def _invoke_lambda(function_name: str, payload: dict) -> dict:
    """Invoke a Lambda function and return the parsed response."""
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    response_payload = json.loads(response["Payload"].read().decode("utf-8"))

    if response.get("FunctionError"):
        logger.error(f"Lambda invocation error: {response_payload}")
        raise RuntimeError(f"Lambda function error: {response_payload}")

    return response_payload


@tool
def generate_pptx(tenant_id: str, kb_id: str, topic: str) -> str:
    """Generate a PowerPoint presentation from the knowledge base.

    Creates a multi-slide PowerPoint presentation based on the specified topic,
    using content retrieved from the enterprise knowledge base. Returns a
    presigned URL for downloading the generated presentation.

    Args:
        tenant_id: The tenant identifier for multi-tenant isolation.
        kb_id: The knowledge base ID to retrieve content from.
        topic: The topic or prompt describing what the presentation should cover.
    """
    logger.info(f"Generating PPTX for tenant={tenant_id}, kb={kb_id}, topic={topic}")

    # Build the payload in the format the PPTX Lambda expects
    # The Lambda's flatten_input_event expects a "parameters" list
    payload = {
        "parameters": [
            {"name": "tenant_id", "value": tenant_id},
            {"name": "kb_id", "value": kb_id},
            {"name": "topic", "value": topic},
            {"name": "background", "value": "true"},
            {"name": "images", "value": "Low resolution"},
        ],
        "actionGroup": "docgen-agent-actions",
        "function": "generate_pptx_kb",
        "sessionAttributes": {},
        "promptSessionAttributes": {},
    }

    result = _invoke_lambda(PPTX_GEN_FUNCTION_NAME, payload)

    # Extract the response body from the Bedrock Action Group format
    # The Lambda returns: {"messageVersion": "1.0", "response": {"functionResponse": {"responseBody": {"TEXT": {"body": ...}}}}}
    try:
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        parsed = json.loads(body)
        if "url" in parsed:
            return f"Presentation generated successfully. Download URL: {parsed['url']}"
        return f"Presentation generated: {body}"
    except (KeyError, TypeError, json.JSONDecodeError):
        # If the response format is different, return raw
        return f"Presentation generation result: {json.dumps(result)}"


@tool
def generate_summary(kb_id: str, prompt: str, tenant_id: str = "") -> str:
    """Generate a summary from the knowledge base.

    Retrieves relevant content from the enterprise knowledge base and generates
    a concise summary based on the provided prompt. Uses Retrieval Augmented
    Generation (RAG) to ground the response in source documents.

    Args:
        kb_id: The knowledge base ID to retrieve content from.
        prompt: The question or topic to summarize from the knowledge base.
        tenant_id: Optional tenant identifier for multi-tenant isolation.
    """
    logger.info(f"Generating summary for kb={kb_id}, prompt={prompt}")

    # Build the payload in the format the Summary Lambda expects
    payload = {
        "parameters": [
            {"name": "kb_id", "value": kb_id},
            {"name": "prompt", "value": prompt},
            {"name": "tenant_id", "value": tenant_id},
        ],
        "actionGroup": "summary-agent-actions",
        "function": "generate_text_kb",
        "sessionAttributes": {},
        "promptSessionAttributes": {},
    }

    result = _invoke_lambda(SUMMARY_GEN_FUNCTION_NAME, payload)

    # Extract the response body from the Bedrock Action Group format
    try:
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        parsed = json.loads(body)
        # Prefer the full summary text if available
        summary_text = parsed.get("summary_text", "")
        if summary_text:
            # Add sources from chunks if available
            chunks = parsed.get("chunks", [])
            sources = []
            for chunk in chunks:
                for idx, source in chunk.get("sources", {}).items():
                    if source not in sources:
                        sources.append(source)
            result_text = summary_text
            if sources:
                result_text += "\n\nSources:\n" + "\n".join(f"- {s}" for s in sources)
            return result_text
        # Fall back to combining chunks
        chunks = parsed.get("chunks", [])
        if chunks:
            texts = [chunk.get("text", "") for chunk in chunks]
            sources = []
            for chunk in chunks:
                for idx, source in chunk.get("sources", {}).items():
                    if source not in sources:
                        sources.append(source)
            summary = "\n\n".join(texts)
            if sources:
                summary += "\n\nSources:\n" + "\n".join(f"- {s}" for s in sources)
            return summary
        return f"Summary result: {body}"
    except (KeyError, TypeError, json.JSONDecodeError):
        return f"Summary result: {json.dumps(result)}"
