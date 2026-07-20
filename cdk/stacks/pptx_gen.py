from aws_cdk import (
    aws_lambda as _lambda,
    aws_ecr,
    aws_ssm as ssm,
    aws_iam,
    aws_ec2,
    Duration,
    Aws,
    Stack,
    ArnFormat,
    aws_bedrock as bedrock
)
from .util import get_inference_profile, get_prefix_list_id
import typing
import os

def create_pptx_lambda(self: Stack, bucket, guardrail_id: str = "", guardrail_version: str = "") -> tuple:

    env = self.node.try_get_context("environment")
    profile = self.node.try_get_context("awscliprofile")
    imageTag = self.node.try_get_context("PPTXGenImageTag")
    bucket = ssm.StringParameter.value_for_string_parameter(
        self, f"/proserv/s3/kb-rag")
    kms_key_arn = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/s3/kb-kms-key-arn")
    bucketDev = self.node.try_get_context("BucketDev")
    # Look up S3 prefix list ID using boto3 at synthesis time
    s3_prefix_list = get_prefix_list_id('s3', profile)
    vpcId = self.node.try_get_context("VpcId")
    vpc = aws_ec2.Vpc.from_lookup(self, "pptx-gen-vpc", vpc_id=vpcId)
    image_generation_model_id = self.node.try_get_context("ImageGenerationModelId")
    container_registry = self.node.try_get_context("ContainerRegistry")

    # For inference-profile based models (e.g., Stability AI), construct the ARN directly
    # For foundation models, use the CDK lookup
    if image_generation_model_id.startswith("stability."):
        image_generation_inference_profile = f"us.{image_generation_model_id}"
        image_generation_model_arn = self.format_arn(
            service="bedrock",
            resource="inference-profile",
            resource_name=image_generation_inference_profile,
            account=Aws.ACCOUNT_ID,
            region=Aws.REGION,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME
        )
        image_generation_env_value = image_generation_inference_profile
    else:
        image_generation_model_arn = bedrock.FoundationModel.from_foundation_model_id(
            scope=self,
            _id='ImageGenerationModelId',
            foundation_model_id=bedrock.FoundationModelIdentifier(image_generation_model_id)).model_arn
        image_generation_env_value = image_generation_model_arn

    agent_inference_profile_id = self.node.try_get_context("AgentInferenceProfileId")
    agent_inference_profile_arn = self.format_arn(
        service="bedrock",
        resource="inference-profile",
        resource_name=agent_inference_profile_id,
        account=Aws.ACCOUNT_ID,
        region=Aws.REGION,
        arn_format=ArnFormat.SLASH_RESOURCE_NAME # Bedrock inference profiles use a slash format
    )

    inference_profile = get_inference_profile(profile, agent_inference_profile_arn)
    model_arns = inference_profile['models']
    model_arns.append(agent_inference_profile_arn)
    model_arns.append(image_generation_model_arn)
    # Also add the foundation model ARN for Stability models
    if image_generation_model_id.startswith("stability."):
        model_arns.append(f"arn:{Aws.PARTITION}:bedrock:*::foundation-model/{image_generation_model_id}")


    # agent_model_id = self.node.try_get_context("AgentModelId")
    # agent_model_arn = bedrock.FoundationModel.from_foundation_model_id(
    #     scope=self,
    #     _id='EmbeddingsModel',
    #     foundation_model_id=bedrock.FoundationModelIdentifier(agent_model_id)).model_arn

    ecr_repository = aws_ecr.Repository.from_repository_attributes(self,
            id              = "ECR",
            repository_arn  ='arn:{0}:ecr:{1}:{2}:repository/{3}'.format(Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID, container_registry),
            repository_name = container_registry
        ) ## aws_ecr.Repository.from_repository_attributes
    ecr_image = typing.cast("aws_lambda.Code", _lambda.EcrImageCode(
            repository = ecr_repository,
            tag_or_digest=imageTag
        ))
    
    sg = aws_ec2.SecurityGroup(self, "pptx-gen-sg", vpc=vpc, allow_all_outbound=False)
    sg.add_egress_rule(
        aws_ec2.Peer.any_ipv4(),
        aws_ec2.Port.tcp(443)
    )
    sg.add_egress_rule(
        aws_ec2.Peer.prefix_list(s3_prefix_list),
        aws_ec2.Port.tcp(443)
    )

    # create s3 bucket
    # s3 = _s3.Bucket(self, "s3bucket")

    function=_lambda.Function(self,
        id  = "pptx_gen",
        description   = "PPTX generator",
        code          = ecr_image,
        ##
        ## Handler and Runtime must be *FROM_IMAGE*
        ## when provisioning Lambda from Container.
        ##
        handler       = _lambda.Handler.FROM_IMAGE,
        runtime       = _lambda.Runtime.FROM_IMAGE,
        environment   = {
            "MODEL_ID": agent_inference_profile_id,
            "IMAGE_MODEL_ID": image_generation_env_value,
            "S3_BUCKET": bucketDev,
            "GUARDRAIL_ID": guardrail_id,
            "GUARDRAIL_VERSION": guardrail_version
        },
        memory_size   = 900,
        timeout       = Duration.seconds(900),
        reserved_concurrent_executions=50,
        tracing       = _lambda.Tracing.ACTIVE,
        vpc           = vpc,
        security_groups=[sg],
        vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
        allow_public_subnet=True
    ) ## aws_lambda.Function



    policy = aws_iam.ManagedPolicy.from_aws_managed_policy_name('AmazonElasticContainerRegistryPublicReadOnly')
    policy1 = aws_iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaBasicExecutionRole')
    policy2 = aws_iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaVPCAccessExecutionRole')
    function.role.add_managed_policy(policy)
    function.role.add_managed_policy(policy1)
    function.role.add_managed_policy(policy2)
    function.role.add_managed_policy(
        aws_iam.ManagedPolicy.from_aws_managed_policy_name('AWSXRayDaemonWriteAccess')
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "pptx-gen-bedrock-invoke",
            statements=[
                aws_iam.PolicyStatement(
                    actions=[
                        "bedrock:InvokeModel",
                        'bedrock:InvokeModelWithResponseStream',
                        'bedrock:GetInferenceProfile'
                    ],
                    resources=model_arns

                ),
                aws_iam.PolicyStatement(
                    actions=[
                        "bedrock:Retrieve"
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:knowledge-base/*"
                    ]
                ),
                aws_iam.PolicyStatement(
                    actions=[
                        "bedrock:RetrieveAndGenerate"
                    ],
                    # RetrieveAndGenerate does not support resource-level permissions
                    # per IAM service authorization reference - requires Resource: "*"
                    resources=["*"]
                ),
                aws_iam.PolicyStatement(
                    actions=[
                        "kms:Decrypt",
                        "kms:GenerateDataKey"
                    ],
                    resources=[
                       kms_key_arn
                    ]
                )
            ])
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "pptx-gen-bedrock-guardrail",
            statements=[
                aws_iam.PolicyStatement(
                    actions=[
                        "bedrock:GetGuardrail",
                        "bedrock:ApplyGuardrail"
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:guardrail/*"
                    ]
                )
            ])
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "pptx-gen-bedrock-prompt",
            statements=[
                aws_iam.PolicyStatement(
                    actions=[
                        "bedrock:GetPrompt",
                        "bedrock:ListPrompts",
                        "bedrock:CreatePrompt",
                        "bedrock:DeletePrompt",
                        "bedrock:UpdatePrompt"
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:prompt/*"
                    ]
                )
            ])
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "pptx-gen-s3-access",
            statements=[
                aws_iam.PolicyStatement(
                    actions=[
                        "s3:PutObject",
                        "s3:GetObject"
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:s3:::{bucketDev}/*"
                    ]
                )
            ])
    )

    function.role.attach_inline_policy(
        aws_iam.Policy(self, "pptx-gen-kb-policy",
            statements=[
                aws_iam.PolicyStatement(
                    actions=[
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:List*",
                        "s3:GetBucket*",
                        "s3:Abort*"
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:s3:::{bucket}",
                        f"arn:{Aws.PARTITION}:s3:::{bucket}/*",
                        f"arn:{Aws.PARTITION}:s3:::{bucket}-intermediate",
                        f"arn:{Aws.PARTITION}:s3:::{bucket}-intermediate/aws/bedrock/knowledge_bases/*"
                    ]
                ),
                aws_iam.PolicyStatement(
                    actions=[
                        "aoss:APIAccessAll"
                    ],
                    resources=[
                        ssm.StringParameter.value_for_string_parameter(
                            self, "/proserv/opensearch/collection_arn"
                        )
                    ]
                ),
            ])
    )

    dotnetLambda = _lambda.Function(self, 'pptx_gen_dotnet',
        handler='AgentProxy::AgentProxy.PowerPointGenerator::FunctionHandler',
        runtime=_lambda.Runtime.DOTNET_8,
        timeout=Duration.seconds(600),
        code=_lambda.Code.from_asset("../asset-output/agent-proxy-function.zip"),
        vpc=vpc,
        role=function.role,
        tracing=_lambda.Tracing.ACTIVE,
        environment   = {
            "MODEL_ID": agent_inference_profile_id,
            "S3_BUCKET": bucketDev,
            "AWS_LAMBDA_HANDLER_LOG_LEVEL":"Trace",
            "GUARDRAIL_ID": guardrail_id,
            "GUARDRAIL_VERSION": guardrail_version
        },
        vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
        allow_public_subnet=True,
        security_groups=[sg])

    return (function, dotnetLambda)

