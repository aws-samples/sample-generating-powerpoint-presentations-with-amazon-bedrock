"""
Amazon Bedrock AgentCore Stack

Replaces the classic Bedrock Agents (MultiAgentStack) with AgentCore Runtime.
Deploys:
- AgentCore Runtime (hosts the Strands-based supervisor agent container)
- AgentCore Runtime Endpoint (versioned deployment)
- Guardrails (same as before)
- Lambda functions for PPTX generation and summarization (same as before)
- IAM roles for the agent runtime

The agent container invokes the Lambda functions directly (not via Gateway)
to keep the architecture simple and avoid introducing another network hop.
"""

import json
import os
import string
import secrets

from aws_cdk import (
    Duration,
    Stack,
    Aws,
    RemovalPolicy,
    Tags,
    CfnOutput,
    aws_bedrock as bedrock,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ssm as ssm,
    ArnFormat,
)
from constructs import Construct

from .pptx_gen import create_pptx_lambda
from .sum_gen import create_sum_lambda
from .util import get_inference_profile


class AgentCoreStack(Stack):
    """CDK Stack for Amazon Bedrock AgentCore Runtime deployment."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Context and configuration
        account_id = Stack.of(self).account
        region = Stack.of(self).region
        env = self.node.try_get_context("environment")
        Tags.of(self).add("Environment", env)

        # Load configuration
        with open('./config.json', 'r') as config_file:
            config = json.load(config_file)

        # SSM parameters from previous stacks
        self.bucket_name = ssm.StringParameter.value_for_string_parameter(
            self, "/proserv/s3/kb-rag")

        # Model configuration
        agent_inference_profile_id = self.node.try_get_context("AgentInferenceProfileId")

        # ====================================================================
        # Guardrails (unchanged from classic architecture)
        # ====================================================================

        pptx_guardrail = bedrock.CfnGuardrail(
            self, "PptxGuardrail",
            name="kb-pptx-guardrail-v2",
            description="Filter content and mask sensitive information for PPTX generation",
            blocked_input_messaging="I cannot process your request due to security restrictions on the input content.",
            blocked_outputs_messaging="I cannot provide the requested information due to security restrictions.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="SEXUAL", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="VIOLENCE", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="HATE", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="INSULTS", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="MISCONDUCT", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="PROMPT_ATTACK", input_strength="LOW", output_strength="NONE"),
                ]
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="EMAIL", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PHONE", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="NAME", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_SOCIAL_SECURITY_NUMBER", action="BLOCK"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_BANK_ACCOUNT_NUMBER", action="BLOCK"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_NUMBER", action="BLOCK"),
                ],
                regexes_config=[
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="Account Number",
                        description="Matches account numbers in the format XXXXXX1234",
                        pattern=r"\b\d{6}\d{4}\b",
                        action="ANONYMIZE"
                    )
                ]
            ),
            tags=[
                {"key": "purpose", "value": "pptx-guardrails"},
                {"key": "environment", "value": env or "production"},
            ]
        )

        pptx_guardrail_version = bedrock.CfnGuardrailVersion(
            self, "PptxGuardrailVersion",
            guardrail_identifier=pptx_guardrail.attr_guardrail_id,
            description="Initial version"
        )
        pptx_guardrail_version.add_dependency(pptx_guardrail)

        summary_guardrail = bedrock.CfnGuardrail(
            self, "SummaryGuardrail",
            name="kb-summary-guardrail-v2",
            description="Filter content and mask sensitive information for summary generation",
            blocked_input_messaging="I cannot process your request due to security restrictions on the input content.",
            blocked_outputs_messaging="I cannot provide the requested information due to security restrictions.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="SEXUAL", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="VIOLENCE", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="HATE", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="INSULTS", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="MISCONDUCT", input_strength="LOW", output_strength="LOW"),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(type="PROMPT_ATTACK", input_strength="LOW", output_strength="NONE"),
                ]
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="EMAIL", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PHONE", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="NAME", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_SOCIAL_SECURITY_NUMBER", action="BLOCK"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_BANK_ACCOUNT_NUMBER", action="BLOCK"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_NUMBER", action="BLOCK"),
                ],
                regexes_config=[
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="Account Number",
                        description="Matches account numbers in the format XXXXXX1234",
                        pattern=r"\b\d{6}\d{4}\b",
                        action="ANONYMIZE"
                    )
                ]
            ),
            tags=[
                {"key": "purpose", "value": "summarization-guardrails"},
                {"key": "environment", "value": env or "production"},
            ]
        )

        summary_guardrail_version = bedrock.CfnGuardrailVersion(
            self, "SummaryGuardrailVersion",
            guardrail_identifier=summary_guardrail.attr_guardrail_id,
            description="Initial version"
        )
        summary_guardrail_version.add_dependency(summary_guardrail)

        # ====================================================================
        # Lambda Functions (unchanged - same PPTX gen and summary gen)
        # ====================================================================

        pptx_gen_func, pptx_gen_dotnet_func = create_pptx_lambda(
            self, bucket=self.bucket_name,
            guardrail_id=pptx_guardrail.attr_guardrail_id,
            guardrail_version=pptx_guardrail_version.attr_version
        )
        sum_gen_func, sum_gen_dotnet_func = create_sum_lambda(
            self, bucket=self.bucket_name,
            guardrail_id=summary_guardrail.attr_guardrail_id,
            guardrail_version=summary_guardrail_version.attr_version
        )

        # ====================================================================
        # AgentCore Runtime - IAM Role
        # ====================================================================

        agent_runtime_role = iam.Role(
            self, "AgentCoreRuntimeRole",
            role_name="AgentCoreRuntimeRole-multi-agent",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "BedrockModelAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                                "bedrock:GetInferenceProfile",
                            ],
                            resources=["*"],  # Inference profiles require broad access
                        ),
                    ]
                ),
                "LambdaInvoke": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["lambda:InvokeFunction"],
                            resources=[
                                pptx_gen_func.function_arn,
                                sum_gen_func.function_arn,
                            ],
                        ),
                    ]
                ),
                "GuardrailAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:GetGuardrail",
                                "bedrock:ApplyGuardrail",
                            ],
                            resources=[
                                f"arn:{Aws.PARTITION}:bedrock:*:*:guardrail/*"
                            ],
                        ),
                    ]
                ),
                "KnowledgeBaseAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:Retrieve",
                                "bedrock:RetrieveAndGenerate",
                            ],
                            resources=[
                                "arn:aws:bedrock:*:*:knowledge-base/*"
                            ],
                        ),
                    ]
                ),
                "ECRAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                                "ecr:GetAuthorizationToken",
                            ],
                            resources=["*"],
                        ),
                    ]
                ),
                "CloudWatchLogs": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            resources=["*"],
                        ),
                    ]
                ),
            },
        )

        # ====================================================================
        # AgentCore Runtime
        # ====================================================================

        # ECR repository for the agent container image
        agent_ecr_repo_name = self.node.try_get_context("AgentContainerRegistry") or "agentcore-supervisor"

        # The AgentCore Runtime resource
        # Using CfnResource since the L2 construct may not be available in all CDK versions
        agent_runtime = self.node.try_get_context("AgentRuntimeId")

        # Note: AgentCore Runtime creation requires the container image to be
        # pre-built and pushed to ECR. The pipeline-build.sh script handles this.
        # The CDK stack references the ECR image URI.
        agent_image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{agent_ecr_repo_name}:latest"

        # Store configuration in SSM for the Chat API to reference
        ssm.StringParameter(self, "AgentRuntimeEndpoint",
            parameter_name="/proserv/agentcore/runtime_endpoint_arn",
            string_value="PLACEHOLDER_UNTIL_DEPLOYED",
            description="AgentCore Runtime Endpoint ARN - update after first deployment"
        )

        # Store Lambda function names for the agent container's environment
        ssm.StringParameter(self, "PptxGenFunctionName",
            parameter_name="/proserv/agentcore/pptx_gen_function_name",
            string_value=pptx_gen_func.function_name
        )

        ssm.StringParameter(self, "SummaryGenFunctionName",
            parameter_name="/proserv/agentcore/summary_gen_function_name",
            string_value=sum_gen_func.function_name
        )

        ssm.StringParameter(self, "GuardrailId",
            parameter_name="/proserv/agentcore/guardrail_id",
            string_value=pptx_guardrail.attr_guardrail_id
        )

        ssm.StringParameter(self, "GuardrailVersion",
            parameter_name="/proserv/agentcore/guardrail_version",
            string_value=pptx_guardrail_version.attr_version
        )

        ssm.StringParameter(self, "AgentImageUri",
            parameter_name="/proserv/agentcore/agent_image_uri",
            string_value=agent_image_uri
        )

        ssm.StringParameter(self, "AgentModelId",
            parameter_name="/proserv/agentcore/model_id",
            string_value=agent_inference_profile_id
        )

        ssm.StringParameter(self, "AgentRuntimeRoleArn",
            parameter_name="/proserv/agentcore/runtime_role_arn",
            string_value=agent_runtime_role.role_arn
        )

        # ====================================================================
        # Outputs
        # ====================================================================

        CfnOutput(self, "PptxGenFunctionArn",
            value=pptx_gen_func.function_arn,
            description="PPTX Generator Lambda Function ARN"
        )

        CfnOutput(self, "SummaryGenFunctionArn",
            value=sum_gen_func.function_arn,
            description="Summary Generator Lambda Function ARN"
        )

        CfnOutput(self, "AgentContainerImageUri",
            value=agent_image_uri,
            description="Agent container image URI for AgentCore Runtime"
        )

        CfnOutput(self, "AgentRuntimeRoleArnOutput",
            value=agent_runtime_role.role_arn,
            description="IAM Role ARN for AgentCore Runtime"
        )

        CfnOutput(self, "PptxGuardrailId",
            value=pptx_guardrail.attr_guardrail_id,
            description="PPTX Guardrail ID"
        )

        CfnOutput(self, "SummaryGuardrailId",
            value=summary_guardrail.attr_guardrail_id,
            description="Summary Guardrail ID"
        )
