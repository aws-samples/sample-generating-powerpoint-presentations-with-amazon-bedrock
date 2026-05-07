"""
Python module for generating Amazon Bedrock Guardrails.

Example Usage
-------------
Basic usage with default configurations
```
from aws_bedrock_guardrails import BedrockGuardrails
guardrails = BedrockGuardrails()
guardrail_id, version = guardrails.create_guardrail("my-guardrail")
```

Create guardrail with custom tags
```
from aws_bedrock_guardrails import BedrockGuardrails
guardrails = BedrockGuardrails()
custom_tags = [
    {"key": "purpose", "value": "summarization-guardrails"},
    {"key": "environment", "value": "production"},
]
guardrail_id, version = guardrails.create_guardrail(
    name="kb-summary-guardrail",
    description="Filter content and mask sensitive information",
    tags=custom_tags
)
```

Custom configuration example
```
from aws_bedrock_guardrails import BedrockGuardrails, FilterConfig, PiiEntityConfig, RegexConfig
custom_guardrails = BedrockGuardrails(
    region_name="us-west-2",
    content_filters=[
        FilterConfig("SEXUAL", "HIGH", "HIGH"),
        FilterConfig("VIOLENCE", "MEDIUM", "HIGH"),
    ],
    pii_entities=[
        PiiEntityConfig("EMAIL", "BLOCK"),
        PiiEntityConfig("PHONE", "ANONYMIZE"),
    ]
)
```
"""

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class FilterConfig:
    """Configuration for content filtering.

    Args:
        type (str): The harmful category that the content filter is applied to.
            Options: "SEXUAL" | "VIOLENCE" | "HATE" | "INSULTS" | "MISCONDUCT" | "PROMPT_ATTACK".
        input_strength (str): The strength of the content filter to apply to
            prompts. As you increase the filter strength, the likelihood of
            filtering harmful content increases and the probability of seeing
            harmful content in your application reduces.
            Options: "NONE" | "LOW" | "MEDIUM" | "HIGH".
        output_strength (str): The strength of the content filter to apply to
            model responses. As you increase the filter strength, the likelihood
            of filtering harmful content increases and the probability of seeing
            harmful content in your application reduces.
            Options: "NONE" | "LOW" | "MEDIUM" | "HIGH"
    """

    type: str
    input_strength: str = "LOW"
    output_strength: str = "LOW"


@dataclass
class PiiEntityConfig:
    """Configuration for PII entity handling.

    Args:
        type (str): Configure guardrail type when the PII entity is detected.
            Options: "ADDRESS" | "AGE" | "AWS_ACCESS_KEY" | "AWS_SECRET_KEY" | "CA_HEALTH_NUMBER" | "CA_SOCIAL_INSURANCE_NUMBER" | "CREDIT_DEBIT_CARD_CVV" | "CREDIT_DEBIT_CARD_EXPIRY" | "CREDIT_DEBIT_CARD_NUMBER" | "DRIVER_ID" | "EMAIL" | "INTERNATIONAL_BANK_ACCOUNT_NUMBER" | "IP_ADDRESS" | "LICENSE_PLATE" | "MAC_ADDRESS" | "NAME" | "PASSWORD" | "PHONE" | "PIN" | "SWIFT_CODE" | "UK_NATIONAL_HEALTH_SERVICE_NUMBER" | "UK_NATIONAL_INSURANCE_NUMBER" | "UK_UNIQUE_TAXPAYER_REFERENCE_NUMBER" | "URL" | "USERNAME" | "US_BANK_ACCOUNT_NUMBER" | "US_BANK_ROUTING_NUMBER" | "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER" | "US_PASSPORT_NUMBER" | "US_SOCIAL_SECURITY_NUMBER" | "VEHICLE_IDENTIFICATION_NUMBER"
        action (str): Configure guardrail action when the PII entity is detected.
            Options: "BLOCK" | "ANONYMIZE"
    """

    type: str
    action: str


@dataclass
class RegexConfig:
    """
    Configuration for regex-based filtering.

    Args:
        name (str): The name of the regular expression to configure for the guardrail.
        description (str): The description of the regular expression to configure for the guardrail.
        pattern (str): The regular expression pattern to configure for the guardrail.
        action (str): The guardrail action to configure when matching regular expression is detected. Options: "BLOCK" | "ANONYMIZE"
    """

    name: str
    description: str
    pattern: str
    action: str


class BedrockGuardrails:
    """A class to manage Amazon Bedrock guardrails for content filtering and PII protection.

    This class provides functionality to create and manage guardrails in Amazon Bedrock,
    including content filtering and sensitive information handling.

    Attributes:
        region_name (str): AWS region name
        client (boto3.client): Boto3 Bedrock client
        content_filters (List[FilterConfig]): List of content filter configurations
        pii_entities (List[PiiEntityConfig]): List of PII entity configurations
        regex_patterns (List[RegexConfig]): List of regex pattern configurations
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        bedrock_client=None,
        content_filters: Optional[List[FilterConfig]] = None,
        pii_entities: Optional[List[PiiEntityConfig]] = None,
        regex_patterns: Optional[List[RegexConfig]] = None,
    ):
        """Initialize the BedrockGuardrails instance.

        Args:
            region_name: AWS region name. If None, uses AWS_REGION environment variable
            client: Boto3 Bedrock client
            content_filters: List of content filter configurations
            pii_entities: List of PII entity configurations
            regex_patterns: List of regex pattern configurations
        """
        self.region_name = region_name or os.environ.get("AWS_REGION")
        if not self.region_name:
            raise ValueError("AWS region must be provided or set in AWS_REGION environment variable")

        self.client = bedrock_client or boto3.Session(region_name=self.region_name).client(
            "bedrock", region_name=self.region_name
        )

        # Set default configurations if none provided
        self.content_filters = content_filters or [
            FilterConfig("SEXUAL"),
            FilterConfig("VIOLENCE"),
            FilterConfig("HATE"),
            FilterConfig("INSULTS"),
            FilterConfig("MISCONDUCT"),
            FilterConfig("PROMPT_ATTACK", output_strength="NONE"),
        ]

        self.pii_entities = pii_entities or [
            PiiEntityConfig("EMAIL", "ANONYMIZE"),
            PiiEntityConfig("PHONE", "ANONYMIZE"),
            PiiEntityConfig("NAME", "ANONYMIZE"),
            PiiEntityConfig("US_SOCIAL_SECURITY_NUMBER", "BLOCK"),
            PiiEntityConfig("US_BANK_ACCOUNT_NUMBER", "BLOCK"),
            PiiEntityConfig("CREDIT_DEBIT_CARD_NUMBER", "BLOCK"),
        ]

        self.regex_patterns = regex_patterns or [
            RegexConfig(
                name="Account Number",
                description="Matches account numbers in the format XXXXXX1234",
                pattern=r"\b\d{6}\d{4}\b",
                action="ANONYMIZE",
            )
        ]

    @property
    def content_policy_config(self) -> Dict:
        """Generate the content policy configuration.

        Returns:
            Dict containing the content policy configuration
        """
        return {
            "filtersConfig": [
                {"type": f.type, "inputStrength": f.input_strength, "outputStrength": f.output_strength}
                for f in self.content_filters
            ]
        }

    @property
    def sensitive_information_policy_config(self) -> Dict:
        """Generate the sensitive information policy configuration.

        Returns:
            Dict containing the sensitive information policy configuration
        """
        return {
            "piiEntitiesConfig": [{"type": e.type, "action": e.action} for e in self.pii_entities],
            "regexesConfig": [
                {"name": r.name, "description": r.description, "pattern": r.pattern, "action": r.action}
                for r in self.regex_patterns
            ],
        }

    def get_existing_guardrail(self, guardrail_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Check if a guardrail with the given name already exists.

        Args:
            guardrail_name: Name of the guardrail to look for

        Returns:
            Tuple containing the guardrail ID and version if found, (None, None) otherwise

        Raises:
            ClientError: If there is an error communicating with the Bedrock service
        """
        logger.info(f"Checking if guardrail {guardrail_name} already exists")

        try:
            response = self.client.list_guardrails(maxResults=123)
            if not response or "guardrails" not in response:
                logger.warning("No guardrails returned")
                return None, None

            for guardrail in response.get("guardrails", []):
                if guardrail.get("name") == guardrail_name:
                    logger.info(f"Found existing guardrail: {guardrail}")
                    return guardrail["id"], guardrail["version"]

            logger.info(f"Guardrail {guardrail_name} does not exist")
            return None, None

        except ClientError as err:
            error_msg = (
                f"Failed to list guardrails: {err.response['Error']['Code']}: " f"{err.response['Error']['Message']}"
            )
            logger.error(error_msg, exc_info=True)
            raise

    def create_guardrail(
        self,
        name: str,
        description: str = "Filter content and mask sensitive information",
        tags: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Create a new guardrail with the specified configuration.

        Args:
            name: Name for the new guardrail
            description: Description of the guardrail's purpose
            tags: Optional list of key-value pair tags

        Returns:
            Tuple containing the guardrail ID and version if created successfully,
            (None, None) otherwise

        Raises:
            ClientError: If there is an error communicating with the Bedrock service
        """
        # Check if guardrail already exists
        existing_id, existing_version = self.get_existing_guardrail(name)
        if existing_id and existing_version:
            logger.info(f"Using existing guardrail {existing_id} version {existing_version}")
            return existing_id, existing_version

        logger.info(f"Creating guardrail {name}")

        default_tags = [
            {"key": "purpose", "value": "content-guardrails"},
            {"key": "environment", "value": "production"},
        ]

        try:
            response = self.client.create_guardrail(
                name=name,
                description=description,
                contentPolicyConfig=self.content_policy_config,
                sensitiveInformationPolicyConfig=self.sensitive_information_policy_config,
                blockedInputMessaging=(
                    "I cannot process your request due to security restrictions on the input content."
                ),
                blockedOutputsMessaging=("I cannot provide the requested information due to security restrictions."),
                tags=tags or default_tags,
            )
            guardrail_id = response.get("id")
            version = response.get("version")
            logger.info(f"Successfully created guardrail {guardrail_id} version {version}")
            return guardrail_id, version

        except ClientError as err:
            error_msg = (
                f"Failed to create guardrail: {err.response['Error']['Code']}: " f"{err.response['Error']['Message']}"
            )
            logger.error(error_msg, exc_info=True)
            raise
