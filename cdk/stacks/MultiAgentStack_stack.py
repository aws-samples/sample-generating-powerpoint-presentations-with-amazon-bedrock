from typing_extensions import runtime
import os
import time
from aws_cdk import (
    Duration,
    CustomResource,
    Stack,
    Aws,
    aws_s3 as s3,
    aws_logs as aws_logs,
    aws_s3_deployment as s3_deploy,
    aws_dynamodb as dynamodb,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_s3_notifications as s3n,
    aws_ssm as ssm,
    aws_opensearchserverless as opensearchserverless,
    aws_iam as iam,
    aws_ec2,
    CfnOutput,
    aws_bedrock as bedrock,
    custom_resources as cr,
    CfnWaitCondition,
    CfnWaitConditionHandle,
    Tags,
    CfnDeletionPolicy,  # ← Added
    ArnFormat
)
import json
import string
import secrets
import boto3
import logging
from .pptx_gen import create_pptx_lambda
from .sum_gen import create_sum_lambda
from .util import get_inference_profile

logger = logging.getLogger()
logger.setLevel(logging.INFO)

from constructs import Construct

class MultiAgentStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Get account and region using Stack properties
        account_id = Stack.of(self).account
        region = Stack.of(self).region
        suffix = f"{region}-{account_id}"
        env = self.node.try_get_context("environment")
        AccessLogBucketName = self.node.try_get_context("AccessLogBucket")
        Tags.of(self).add("Environment", env)

        self.kms_key = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/s3/kb-kms-key-arn")
        self.bucket_name = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/s3/kb-rag")
        self.intermediate_bucket_name = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/s3/kb-rag-intermediate")
        self.collection_name = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/opensearch/collection_name")
        self.collection_arn = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/opensearch/collection_arn")
        self.collection_endpoint = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/opensearch/collection_endpoint")

        # Load configuration
        with open('./config.json', 'r') as config_file:
            config = json.load(config_file)

        # Define parameters

        docgen_agent_name = config['pptxAgentName']
        docgen_agent_alias_name = config['pptxAgentAliasName']

        summary_agent_name = config['summaryAgentName']
        summary_agent_alias_name = config['summaryAgentAliasName']

        supervisor_agent_name = config['superAgentName']
        supervisor_agent_alias_name = config['superAgentAliasName']

        knowledge_base_name = config['knowledgeBaseName']
        knowledge_base_description = config['knowledgeBaseDescription']

        docgen_collaborator_instruction = config['docgenCollaboratorInstruction']
        sumgen_collaborator_instruction = config['sumgenCollaboratorInstruction']
       
        # Create Bedrock Guardrails via CDK (finding 4.6)
        pptx_guardrail = bedrock.CfnGuardrail(
            self, "PptxGuardrail",
            name="kb-pptx-guardrail",
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

        # Publish a versioned snapshot of the PPTX guardrail
        pptx_guardrail_version = bedrock.CfnGuardrailVersion(
            self, "PptxGuardrailVersion",
            guardrail_identifier=pptx_guardrail.attr_guardrail_id,
            description="Initial version"
        )
        pptx_guardrail_version.add_dependency(pptx_guardrail)

        summary_guardrail = bedrock.CfnGuardrail(
            self, "SummaryGuardrail",
            name="kb-summary-guardrail",
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

        # Publish a versioned snapshot of the summary guardrail
        summary_guardrail_version = bedrock.CfnGuardrailVersion(
            self, "SummaryGuardrailVersion",
            guardrail_identifier=summary_guardrail.attr_guardrail_id,
            description="Initial version"
        )
        summary_guardrail_version.add_dependency(summary_guardrail)

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

        # agent_model_id = self.node.try_get_context("AgentModelId")
        # agent_model_arn = bedrock.FoundationModel.from_foundation_model_id(
        #     scope=self,
        #     _id='EmbeddingsModel',
        #     foundation_model_id=bedrock.FoundationModelIdentifier(agent_model_id)).model_arn
        agent_inference_profile_id = self.node.try_get_context("AgentInferenceProfileId")
        agent_inference_profile_arn = self.format_arn(
            service="bedrock",
            resource="inference-profile",
            resource_name=agent_inference_profile_id,
            account=Aws.ACCOUNT_ID,
            region=Aws.REGION,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME # Bedrock inference profiles use a slash format
        )
        profile = self.node.try_get_context("awscliprofile")
        inference_profile = get_inference_profile(profile, agent_inference_profile_arn)
        model_arns = inference_profile['models']
        model_arns.append(agent_inference_profile_arn)

        # Bedrock embedding model Amazon Titan Text v2
        embedding_model_id = self.node.try_get_context("EmbeddingModelId")
        embedding_model_arn = bedrock.FoundationModel.from_foundation_model_id(
            scope=self,
            _id='EmbeddingsModel',
            foundation_model_id=bedrock.FoundationModelIdentifier(embedding_model_id)).model_arn

        # Docgen Agent
        docgen_agent_description = config['pptxAgentDescription']
        docgen_agent_instruction = config['pptxAgentInstruction']
        docgen_agent_action_group_description = config['pptxAgentActionGroupDescription']
        docgen_agent_action_group_name = config['pptxAgentActionGroupName']

        # Summary Agent
        summary_agent_description = config['summaryAgentDescription']
        summary_agent_instruction = config['summaryAgentInstruction']
        summary_agent_action_group_description = config['summaryAgentActionGroupDescription']
        summary_agent_action_group_name = config['summaryAgentActionGroupName']

        # Supervisor Agent
        supervisor_agent_description = config['superAgentDescription']
        supervisor_agent_instruction = config['superAgentInstruction']

        # Role that will be used by the Bedrock Agents
        agent_role = iam.Role(
            scope=self,
            id='AgentRole',
            role_name='AmazonBedrockExecutionRoleForAgents-multi-agent',
            assumed_by=iam.ServicePrincipal('bedrock.amazonaws.com'))

        # Create the Summary Agent
        summary_agent = bedrock.CfnAgent(
            self, "summaryAgent",
            agent_name=summary_agent_name,
            agent_resource_role_arn=agent_role.role_arn,
            # auto_prepare=True,
            description=summary_agent_description,
            foundation_model=agent_inference_profile_id,
            instruction=summary_agent_instruction,
            idle_session_ttl_in_seconds=3600,
            action_groups=[bedrock.CfnAgent.AgentActionGroupProperty(
                action_group_name=summary_agent_action_group_name,
                description=summary_agent_action_group_description,

                # the properties below are optional
                action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                    lambda_=sum_gen_func.function_arn #sum_gen_dotnet_func.function_arn
                ),

                function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                    functions=[bedrock.CfnAgent.FunctionProperty(
                        name='generate_text_kb',
                        # the properties below are optional
                        description='Generates a summary of the document in the KB',
                        parameters={
                            "tenant_id": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="tenant id",
                                required=False
                            ),
                            "kb_id": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="KB id",
                                required=True
                            ),
                            "prompt": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="user prompt",
                                required=True
                            )
                            
                        }
                    )]
                )
        )])


        # Summary agent alias will be created by the custom resource

        # Create the Docgen Agent
        docgen_agent = bedrock.CfnAgent(
            self, "docgenAgent",
            agent_name=docgen_agent_name,
            agent_resource_role_arn=agent_role.role_arn,
            # auto_prepare=True,
            description=docgen_agent_description,
            foundation_model=agent_inference_profile_id,
            instruction=docgen_agent_instruction,
            idle_session_ttl_in_seconds=3600,
            action_groups=[bedrock.CfnAgent.AgentActionGroupProperty(
                action_group_name=docgen_agent_action_group_name,
                description=docgen_agent_action_group_description,

                # the properties below are optional
                action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                    lambda_=pptx_gen_func.function_arn #pptx_gen_dotnet_func.function_arn
                ),

                function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                    functions=[bedrock.CfnAgent.FunctionProperty(
                        name='generate_pptx_kb',
                        # the properties below are optional
                        description='Generates a Powerpoint, WordX, PDF report of the document in the KB',
                        parameters={
                            "tenant_id": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="tenant id",
                                required=True
                            ),
                            "kb_id": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="KB id",
                                required=True
                            ),
                            "topic": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="user prompt",
                                required=True
                            )
                            
                        }
                    )]
                )
        )])

        # Create IAM role for the agent management Lambda
        agent_management_lambda_role = iam.Role(
            self, "AgentManagementLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess")
            ],
            inline_policies={
                "BedrockAgentManagementPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:PrepareAgent",
                                "bedrock:GetAgent",
                                "bedrock:ListAgentAliases",
                                "bedrock:CreateAgentAlias",
                                "bedrock:UpdateAgentAlias",
                                "bedrock:DeleteAgentAlias",
                                "bedrock:GetAgentAlias"
                            ],
                            resources=[
                                f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:agent/*",
                                f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:agent-alias/*"
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "iam:PassRole"
                            ],
                            resources=[
                                agent_role.role_arn
                            ],
                            conditions={
                                "StringEquals": {
                                    "iam:PassedToService": "bedrock.amazonaws.com"
                                }
                            }
                        )
                    ]
                )
            }
        )

        # Consolidated Lambda function to manage all agents
        agent_setup_script = None
        with open('../custom_resources/agent_setup/app.py', 'r') as py_file:
            agent_setup_script = py_file.read()

        agent_setup_lambda = lambda_.Function(
            self, "AgentSetupLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            role=agent_management_lambda_role,
            timeout=Duration.minutes(15),
            tracing=lambda_.Tracing.ACTIVE,
            code=lambda_.Code.from_inline(agent_setup_script)
        )
        random_suffix = "".join(
            secrets.choice(string.ascii_letters + string.digits)
            for _ in range(4)
        )

        # Custom resource to manage docgen agent alias
        docgen_agent_alias_resource = CustomResource(
            self, "DocgenAgentAliasResource",
            service_token=agent_setup_lambda.function_arn,
            properties={
                "AgentId": docgen_agent.attr_agent_id,
                "AgentVersion": docgen_agent.attr_agent_version,
                "AliasName": docgen_agent_alias_name,
                "AgentName": "DocumentGeneration",
                "UpdateToken": random_suffix
            }
        )
        docgen_agent_alias_resource.node.add_dependency(docgen_agent)
        
        # Set update replace policy to RETAIN
        docgen_alias_child = docgen_agent_alias_resource.node.default_child
        docgen_alias_child.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN

        # Custom resource to manage summary agent alias
        summary_agent_alias_resource = CustomResource(
            self, "SummaryAgentAliasResource",
            service_token=agent_setup_lambda.function_arn,
            properties={
                "AgentId": summary_agent.attr_agent_id,
                "AgentVersion": summary_agent.attr_agent_version,
                "AliasName": summary_agent_alias_name,
                "AgentName": "Summary",
                "UpdateToken": random_suffix
            }
        )
        summary_agent_alias_resource.node.add_dependency(summary_agent)
        
        # Set update replace policy to RETAIN
        summary_alias_child = summary_agent_alias_resource.node.default_child
        summary_alias_child.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN

        # Create the supervisor agent (depends on collaborator aliases)
        docen_agent_collaborator = bedrock.CfnAgent.AgentCollaboratorProperty(
            agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                alias_arn=docgen_agent_alias_resource.get_att_string("AliasArn")
            ),
            collaboration_instruction=docgen_collaborator_instruction,
            collaborator_name="DocumentGenerationAgent",
            relay_conversation_history="TO_COLLABORATOR"
        )
        summary_agent_collaborator = bedrock.CfnAgent.AgentCollaboratorProperty(
            agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                alias_arn=summary_agent_alias_resource.get_att_string("AliasArn")
            ),
            collaboration_instruction=sumgen_collaborator_instruction,
            collaborator_name="SummaryAgent",
            relay_conversation_history="TO_COLLABORATOR"
        )
        supervisor_agent = bedrock.CfnAgent(
            self, "SupervisorAgent",
            agent_name=supervisor_agent_name,
            agent_resource_role_arn=agent_role.role_arn,
            description=supervisor_agent_description,
            instruction=supervisor_agent_instruction,
            foundation_model=agent_inference_profile_id,
            idle_session_ttl_in_seconds=3600,
            agent_collaboration="SUPERVISOR_ROUTER",
            agent_collaborators=[
                docen_agent_collaborator,
                summary_agent_collaborator
            ]
        )
        # Supervisor agent depends on collaborator agents' aliases being ready
        supervisor_agent.node.add_dependency(docgen_agent_alias_resource)
        supervisor_agent.node.add_dependency(summary_agent_alias_resource)
        
        # Custom resource to manage supervisor agent alias
        supervisor_agent_alias_resource = CustomResource(
            self, "SupervisorAgentAliasResource",
            service_token=agent_setup_lambda.function_arn,
            properties={
                "AgentId": supervisor_agent.attr_agent_id,
                "AgentVersion": supervisor_agent.attr_agent_version,
                "AliasName": supervisor_agent_alias_name,
                "AgentName": "Supervisor",
                "UpdateToken": random_suffix
            }
        )
        supervisor_agent_alias_resource.node.add_dependency(supervisor_agent)
        
        # Set update replace policy to RETAIN
        supervisor_alias_child = supervisor_agent_alias_resource.node.default_child
        supervisor_alias_child.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN

        # Add Lambda Resource Policy

        # principal = "bedrock.amazonaws.com"

        docgen_agent_invoke_permission = lambda_.CfnPermission(self, "docgen_agent_invoke", 
            action="lambda:InvokeFunction",
            function_name=pptx_gen_func.function_arn,
            principal="bedrock.amazonaws.com",
            source_arn=docgen_agent.attr_agent_arn        
        )

        docgen_agent_invoke_dotnet_permission = lambda_.CfnPermission(self, "docgen_agent_invoke_dotnet",
            action="lambda:InvokeFunction",
            function_name=pptx_gen_dotnet_func.function_arn,
            principal="bedrock.amazonaws.com",
            source_arn=docgen_agent.attr_agent_arn
        )

        sumgen_agent_invoke_permission = lambda_.CfnPermission(self, "sum_agent_invoke",
            action="lambda:InvokeFunction",
            function_name=sum_gen_func.function_name,
            principal="bedrock.amazonaws.com",
            source_arn=summary_agent.attr_agent_arn
        )
        sumgen_agent_invoke_dotnet_permission = lambda_.CfnPermission(self, "sum_agent_invoke_dotnet",
            action="lambda:InvokeFunction",
            function_name=sum_gen_dotnet_func.function_name,
            principal="bedrock.amazonaws.com",
            source_arn=summary_agent.attr_agent_arn
        )
        
        # Create outputs
        CfnOutput(self, "SummaryAgentId",
            value=summary_agent.attr_agent_id,
            description="Summary Agent ID"
        )

        CfnOutput(self, "DocGenAgentId",
            value=docgen_agent.attr_agent_id,
            description="Document Generation Agent ID"
        )

        CfnOutput(self, "SupervisorAgentId",
            value=supervisor_agent.attr_agent_id,
            description="Supervisor Agent ID"
        )

        # Add agent alias IDs as outputs from custom resources
        CfnOutput(self, "SummaryAgentAliasId",
            value=summary_agent_alias_resource.get_att_string("AliasId"),
            description="Summary Agent Alias ID"
        )

        CfnOutput(self, "DocGenAgentAliasId",
            value=docgen_agent_alias_resource.get_att_string("AliasId"),
            description="Document Generation Agent Alias ID"
        )

        CfnOutput(self, "SupervisorAgentAliasId",
            value=supervisor_agent_alias_resource.get_att_string("AliasId"),
            description="Supervisor Agent Alias ID"
        )

        ssm.StringParameter(self, 'SuperAgentId',
            parameter_name=f"/proserv/bedrock/super_agent_id",
            string_value=supervisor_agent.attr_agent_id
        )

        ssm.StringParameter(self, 'SuperAgentAliasId',
            parameter_name=f"/proserv/bedrock/super_agent_alias_id",
            string_value=supervisor_agent_alias_resource.get_att_string("AliasId")
        )

        agent_role.add_to_policy(iam.PolicyStatement(
            sid='InvokeBedrockLambda',
            effect=iam.Effect.ALLOW,
            resources=model_arns,
            actions=[
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream',
                'bedrock:GetInferenceProfile'
                ]))
        agent_role.add_to_policy(iam.PolicyStatement(
            sid='RetrieveKBStatement',
            effect=iam.Effect.ALLOW,
            resources=[
                'arn:aws:bedrock:*:*:knowledge-base/*'],
            actions=['bedrock:Retrieve', 'bedrock:RetrieveAndGenerate']))
        
        agent_role.add_to_policy(iam.PolicyStatement(
            sid='LambdaInvokeStatement',
            effect=iam.Effect.ALLOW,
            resources=[
                pptx_gen_func.function_arn,
                pptx_gen_dotnet_func.function_arn, 
                sum_gen_func.function_arn,
                sum_gen_dotnet_func.function_arn
            ],
            actions=['lambda:InvokeFunction']))
        
        agent_role.add_to_policy(iam.PolicyStatement(
            sid='AmazonBedrockAgentsMultiAgentsPoliciesProd',
            effect=iam.Effect.ALLOW,
            resources=[
                f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:agent-alias/*/*"
            ],
            actions=[
                "bedrock:GetAgentAlias",
                "bedrock:InvokeAgent"]))
        
        agent_role.attach_inline_policy(
            iam.Policy(self, 'Policy',
                statements = [
                    iam.PolicyStatement(
                        effect = iam.Effect.ALLOW,
                        actions = [
                            "bedrock:GetGuardrail",
                            "bedrock:ApplyGuardrail"
                        ],
                        resources = [
                            f"arn:{Aws.PARTITION}:bedrock:*:*:guardrail/*"
                        ]
                    ),
                ]
            )
        )
        