"""
Supervisor agent for document generation and summarization.

This agent runs on Amazon Bedrock AgentCore Runtime using the Strands Agents
framework. It orchestrates two tools:
- generate_pptx: Creates PowerPoint presentations from knowledge base content
- generate_summary: Generates text summaries from knowledge base content
"""

import logging
import os

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

from tools import generate_pptx, generate_summary

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Agent configuration from environment variables
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "")

SYSTEM_PROMPT = """You are a document generation and summarization assistant. You help 
users create PowerPoint presentations and summarize documents from their enterprise 
knowledge base.

Your capabilities:
1. Generate PowerPoint presentations - When users ask for a presentation, report, 
   or slides on a topic, use the generate_pptx tool.
2. Summarize documents - When users ask questions about their documents, want 
   summaries, or need information extracted, use the generate_summary tool.

Guidelines:
- Always use the tools provided to answer queries. Do not guess or fabricate information.
- The user's prompt will contain parameters like kb_id and tenant_id. Extract these 
  from the prompt and pass them to the appropriate tool.
- For presentation requests, pass the topic description to generate_pptx.
- For summary/question requests, pass the user's question to generate_summary.
- If the user's intent is unclear, ask a clarifying question.
- Never ask the user for information you can retrieve through available tools.
- Present results clearly and concisely.
- If a tool returns a download URL, present it prominently to the user.
"""


def create_agent() -> Agent:
    """Create and configure the supervisor agent with tools."""
    model_kwargs = {"model_id": MODEL_ID}

    if GUARDRAIL_ID and GUARDRAIL_VERSION:
        model_kwargs["guardrail_id"] = GUARDRAIL_ID
        model_kwargs["guardrail_version"] = GUARDRAIL_VERSION
        model_kwargs["guardrail_trace"] = "enabled"

    model = BedrockModel(**model_kwargs)

    agent = Agent(
        model=model,
        tools=[generate_pptx, generate_summary],
        system_prompt=SYSTEM_PROMPT,
    )

    return agent


# Create the AgentCore app
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    """Agent entrypoint for AgentCore Runtime."""
    user_message = payload.get("prompt") or payload.get("text", "Hello!")
    # Create a fresh agent per request to avoid concurrency issues
    request_agent = create_agent()
    result = request_agent(user_message)
    # Ensure we return a plain string for JSON serialization
    message = result.message if hasattr(result, 'message') else str(result)
    if not isinstance(message, str):
        message = str(message)
    return {"result": message}


if __name__ == "__main__":
    app.run()
