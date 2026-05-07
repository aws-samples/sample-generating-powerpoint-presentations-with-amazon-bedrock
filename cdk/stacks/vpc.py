from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ssm as ssm,
    aws_iam as iam,
    Aws,
    Tags
    # aws_sqs as sqs,
)
from constructs import Construct

class VpcStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env = self.node.try_get_context("environment")
        Tags.of(self).add("Environment", env)
        vpcId = self.node.try_get_context("VpcId")
        vpc = ec2.Vpc.from_lookup(self, "VPC", vpc_id=vpcId)

        sg = ec2.SecurityGroup(self, "BedrockVpcEndpoint", vpc=vpc, allow_all_outbound=False)
        sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(443)
        )      
        vpce_bedrock = ec2.InterfaceVpcEndpoint(self, "Bedrock vpc endpoint", 
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointService(f"com.amazonaws.{Aws.REGION}.bedrock", 443),
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[sg],
            private_dns_enabled=True
        )   
        vpce_agent = ec2.InterfaceVpcEndpoint(self, "Bedrock agent vpc endpoint", 
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointService(f"com.amazonaws.{Aws.REGION}.bedrock-agent", 443),
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[sg],
            private_dns_enabled=True
        )
        vpce_runtime = ec2.InterfaceVpcEndpoint(self, "Bedrock agent runtime vpc endpoint", 
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointService(f"com.amazonaws.{Aws.REGION}.bedrock-agent-runtime", 443),
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[sg],
            private_dns_enabled=True
        )
        vpce_bedrock_runtime = ec2.InterfaceVpcEndpoint(self, "Bedrock runtime vpc endpoint", 
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointService(f"com.amazonaws.{Aws.REGION}.bedrock-runtime", 443),
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[sg],
            private_dns_enabled=True
        )

        vpce_opensearch = ec2.InterfaceVpcEndpoint(self, "OpenSearch Serverless vpc endpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointService(f"com.amazonaws.{Aws.REGION}.aoss", 443),
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                availability_zones=["us-east-1b", "us-east-1c", "us-east-1d"]
            ),
            security_groups=[sg],
            private_dns_enabled=True
        )

        # Bedrock VPC endpoint policy - scoped to account principals and bedrock actions
        bedrock_vpce_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            principals=[iam.AnyPrincipal()],
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:InvokeAgent",
                "bedrock:Retrieve",
                "bedrock:RetrieveAndGenerate",
                "bedrock:GetInferenceProfile",
                "bedrock:ListInferenceProfiles",
                "bedrock:GetFoundationModel",
                "bedrock:ListFoundationModels",
                "bedrock:ApplyGuardrail",
                "bedrock:GetGuardrail",
                "bedrock:ListGuardrails",
                "bedrock:CreateGuardrail",
                "bedrock:TagResource",
                "bedrock:GetPrompt",
                "bedrock:ListPrompts",
                "bedrock:CreatePrompt",
                "bedrock:DeletePrompt",
                "bedrock:UpdatePrompt"
            ],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "aws:PrincipalAccount": Aws.ACCOUNT_ID
                }
            }
        )

        # Bedrock agent VPC endpoint policy - scoped to agent management actions
        bedrock_agent_vpce_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            principals=[iam.AnyPrincipal()],
            actions=[
                "bedrock:GetAgent",
                "bedrock:PrepareAgent",
                "bedrock:ListAgentAliases",
                "bedrock:ListAgentVersions",
                "bedrock:CreateAgentAlias",
                "bedrock:UpdateAgentAlias",
                "bedrock:DeleteAgentAlias",
                "bedrock:GetAgentAlias",
                "bedrock:InvokeAgent",
                "bedrock:CreateKnowledgeBase",
                "bedrock:DeleteKnowledgeBase",
                "bedrock:GetKnowledgeBase",
                "bedrock:ListKnowledgeBases",
                "bedrock:CreateDataSource",
                "bedrock:DeleteDataSource",
                "bedrock:GetDataSource",
                "bedrock:Retrieve",
                "bedrock:RetrieveAndGenerate",
                "bedrock:CreateSession",
                "bedrock:EndSession",
                "bedrock:DeleteSession"
            ],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "aws:PrincipalAccount": Aws.ACCOUNT_ID
                }
            }
        )

        vpce_bedrock.add_to_policy(bedrock_vpce_policy)
        vpce_agent.add_to_policy(bedrock_agent_vpce_policy)
        vpce_runtime.add_to_policy(bedrock_agent_vpce_policy)
        vpce_bedrock_runtime.add_to_policy(bedrock_vpce_policy)

        vpce_opensearch.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=[
                    "aoss:APIAccessAll",
                    "aoss:BatchGetCollection",
                    "aoss:ListCollections",
                    "aoss:ListAccessPolicies",
                    "aoss:CreateAccessPolicy",
                    "aoss:DeleteAccessPolicy",
                    "aoss:UpdateAccessPolicy",
                    "aoss:GetAccessPolicy",
                    "aoss:DescribeCollection"
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "aws:PrincipalAccount": Aws.ACCOUNT_ID
                    }
                }
            )
        )

        vpce_dynamodb = vpc.add_gateway_endpoint("DynamodbVpcEndpoint", 
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )

        vpce_dynamodb.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchWriteItem",
                    "dynamodb:DescribeTable"
                ],
                resources=[
                    f"arn:{Aws.PARTITION}:dynamodb:{Aws.REGION}:{Aws.ACCOUNT_ID}:table/*"
                ],
                conditions={
                    "StringLike": {
                        "aws:PrincipalArn": f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:*"
                    }
                }
            )
        )

        vpce_s3 = vpc.add_gateway_endpoint("S3VpcEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        vpce_s3.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                    "s3:GetBucketVersioning",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                    "s3:ListBucketMultipartUploads"
                ],
                resources=[
                    f"arn:{Aws.PARTITION}:s3:::*"
                ],
                conditions={
                    "StringLike": {
                        "aws:PrincipalArn": f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:*"
                    }
                }
            )
        )

        ssm.StringParameter(self, 'vpc-id-b',
            parameter_name=f"/proserv/vpcendpoint/bedrock-vpceid",
            string_value=vpce_bedrock.vpc_endpoint_id
        )

        ssm.StringParameter(self, 'vpc-id-br',
            parameter_name=f"/proserv/vpcendpoint/bedrock-runtime-vpceid",
            string_value=vpce_bedrock_runtime.vpc_endpoint_id
        )

        ssm.StringParameter(self, 'vpc-id-ba',
            parameter_name=f"/proserv/vpcendpoint/bedrock-agent-vpceid",
            string_value=vpce_agent.vpc_endpoint_id
        )

        ssm.StringParameter(self, 'vpc-id-bar',
            parameter_name=f"/proserv/vpcendpoint/bedrock-agent-runtime-vpceid",
            string_value=vpce_runtime.vpc_endpoint_id
        )

        ssm.StringParameter(self, 'vpc-sg',
            parameter_name=f"/proserv/vpcendpoint/bedrock-sgid",
            string_value=sg.security_group_id
        )

        ssm.StringParameter(self, 'vpc-id-dynamodb',
            parameter_name=f"/proserv/vpcendpoint/dynamodb-vpceid",
            string_value=vpce_dynamodb.vpc_endpoint_id
        )

        ssm.StringParameter(self, 'vpc-id-s3',
            parameter_name=f"/proserv/vpcendpoint/s3-vpceid",
            string_value=vpce_s3.vpc_endpoint_id
        )

        ssm.StringParameter(self, 'vpc-id-opensearch',
            parameter_name=f"/proserv/vpcendpoint/opensearch-vpceid",
            string_value=vpce_opensearch.vpc_endpoint_id
        )