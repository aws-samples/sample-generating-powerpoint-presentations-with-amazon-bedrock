from aws_cdk import (
    aws_lambda as _lambda,
    aws_ecr,
    aws_ssm as ssm,
    aws_iam,
    aws_ec2,
    Duration,
    Aws,
    Stack,
    aws_bedrock as bedrock,
    ArnFormat
)
from .util import get_inference_profile

def create_sum_lambda(self: Stack, bucket, guardrail_id: str = "", guardrail_version: str = "") -> tuple:

    bucket = ssm.StringParameter.value_for_string_parameter(
        self, f"/proserv/s3/kb-rag")
    kms_key_arn = ssm.StringParameter.value_for_string_parameter(
            self, f"/proserv/s3/kb-kms-key-arn")
    bucketDev = self.node.try_get_context("BucketDev")
    vpcId = self.node.try_get_context("VpcId")
    vpc = aws_ec2.Vpc.from_lookup(self, "sum-gen-vpc", vpc_id=vpcId)

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

    # agent_model_id = self.node.try_get_context("AgentModelId")
    # agent_model_arn = bedrock.FoundationModel.from_foundation_model_id(
    #     scope=self,
    #     _id='EmbeddingsModel',
    #     foundation_model_id=bedrock.FoundationModelIdentifier(agent_model_id)).model_arn
        
    sg = aws_ec2.SecurityGroup(self, "sum-gen-sg", vpc=vpc, allow_all_outbound=False)
    sg.add_egress_rule(
        aws_ec2.Peer.any_ipv4(),
        aws_ec2.Port.tcp(443)
    )

    function = _lambda.Function(
        self, "sum-gen",
        code=_lambda.Code.from_asset('../sum-gen'),
        handler="lambda_function.handler",
        timeout=Duration.seconds(300),
        runtime=_lambda.Runtime.PYTHON_3_12,
        environment   = {
            "MODEL_ID": agent_inference_profile_id,
            "S3_BUCKET": bucketDev,
            "GUARDRAIL_ID": guardrail_id,
            "GUARDRAIL_VERSION": guardrail_version
        },
        vpc=vpc,
        vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
        security_groups=[sg],
        allow_public_subnet=True,
        memory_size=512,
        reserved_concurrent_executions=50,
        tracing=_lambda.Tracing.ACTIVE
    )

    policy = aws_iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaBasicExecutionRole')
    policy2 = aws_iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaVPCAccessExecutionRole')
    function.role.add_managed_policy(policy)
    function.role.add_managed_policy(policy2)
    function.role.add_managed_policy(
        aws_iam.ManagedPolicy.from_aws_managed_policy_name('AWSXRayDaemonWriteAccess')
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "sum-gen-bedrock-invoke",
            statements=[
                aws_iam.PolicyStatement(
                    actions=[
                        "bedrock:InvokeModel",
                        'bedrock:GetInferenceProfile',
                        "bedrock:InvokeModelWithResponseStream"
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
                        "bedrock:CreatePrompt",
                        "bedrock:DeletePrompt",
                        "bedrock:ListPrompts",
                        "bedrock:GetPrompt",
                        "bedrock:UpdatePrompt"
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:prompt/*"
                    ]
                )
            ])
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "sum-gen-bedrock-guardrail",
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
            ]
        )
    )
    function.role.attach_inline_policy(
        aws_iam.Policy(self, "sum-gen-kb-policy",
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

    dotnetLambda = _lambda.Function(self, 'sum_gen_dotnet',
        handler='AgentProxy::AgentProxy.SummaryGenerator::FunctionHandler',
        runtime=_lambda.Runtime.DOTNET_8,
        timeout=Duration.seconds(600),
        code=_lambda.Code.from_asset("../asset-output/agent-proxy-function.zip"),
        vpc=vpc,
        role=function.role,
        memory_size=512,
        tracing=_lambda.Tracing.ACTIVE,
        environment   = {
            "MODEL_ID": agent_inference_profile_arn,
            "S3_BUCKET":bucket,
            "AWS_LAMBDA_HANDLER_LOG_LEVEL":"Trace",
            "GUARDRAIL_ID": guardrail_id,
            "GUARDRAIL_VERSION": guardrail_version
        },
        vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
        allow_public_subnet=True,
        security_groups=[sg])
   

    return (function, dotnetLambda)
