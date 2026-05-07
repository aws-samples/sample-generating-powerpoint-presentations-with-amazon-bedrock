from aws_cdk import (
    Duration,
    aws_lambda as _lambda,
    aws_ecr,
    aws_dynamodb,
    aws_kms as kms,
    aws_ssm as ssm,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_logs as aws_logs,
    aws_opensearchserverless as opensearchserverless,
    aws_iam as iam,
    aws_ec2,
    custom_resources as cr,
    Aws,
    Stack,
    aws_bedrock as bedrock,
    CustomResource,
    custom_resources as cr,
    CfnDeletionPolicy,
    CfnOutput,
    Fn,
    Tags,
    ArnFormat
    # aws_sqs as sqs,
)
import string
import secrets
import json
import boto3
import logging
import typing
from constructs import Construct

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class OpensearchStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Load configuration
        with open('./config.json', 'r') as config_file:
            config = json.load(config_file)
        
        env = self.node.try_get_context("environment")
        self.env_name = env
        self.profile = self.node.try_get_context("awscliprofile")
        self.tenant_table_name =f"tenant-table-{env}"
        suffix = f"{Aws.REGION}-{Aws.ACCOUNT_ID}"
        s3_bucket_name = f"{config['s3BucketName']}-{suffix}"
        intermediate_bucket_name = f"{s3_bucket_name}-intermediate"

        Tags.of(self).add("Environment", env)


        kms_key = kms.Key(self, "S3BucketKMS",
            enable_key_rotation=True,
            rotation_period=Duration.days(180)
        )
        os_kms_key = kms.Key(self, "OpenSearchKMS",
            enable_key_rotation=True,
            rotation_period=Duration.days(180)
        )
        tenant_table_kms_key = kms.Key(self, "TenantTableKMS",
            enable_key_rotation=True,
            rotation_period=Duration.days(180)
        )
        self.tenant_table_kms_key = tenant_table_kms_key
        self.os_kms_key = os_kms_key
        self.bucket_key = kms_key
        # Upload the dataset to Amazon S3
        access_logs_bucket = s3.Bucket(
             self, 'AccessLogsBucket',
             bucket_name=f"{s3_bucket_name}-access-logs",
             block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
             encryption=s3.BucketEncryption.S3_MANAGED,
             enforce_ssl=True,
             lifecycle_rules=[
                 s3.LifecycleRule(expiration=Duration.days(365))
             ])

        bucket = s3.Bucket(
             self, 'kbs3bucket',
             bucket_name=s3_bucket_name,
             block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
             encryption=s3.BucketEncryption.KMS,
             encryption_key=kms_key,
             event_bridge_enabled=False,
             versioned=True,
             server_access_logs_bucket=access_logs_bucket,
             server_access_logs_prefix="kb-bucket-logs/")
        self.bucket = bucket
        
        intermediate_bucket = s3.Bucket(
             self, 'kbIntermeds3bucket',
             bucket_name=intermediate_bucket_name,
             block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
             encryption=s3.BucketEncryption.KMS,
             encryption_key=kms_key,
             event_bridge_enabled=False,
             versioned=True,
             server_access_logs_bucket=access_logs_bucket,
             server_access_logs_prefix="intermediate-bucket-logs/")
        self.intermediate_bucket = intermediate_bucket
        
        tenant_table = aws_dynamodb.Table(
            self, "tenant_table",
            table_name=self.tenant_table_name,
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=aws_dynamodb.Attribute(
                name="tenant_id",
                type=aws_dynamodb.AttributeType.STRING
            ),
            encryption=aws_dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=tenant_table_kms_key,
            point_in_time_recovery=True,
            deletion_protection=True
        )
        self.tenant_table = tenant_table

        # Create OpenSearch Serverless Collection
        collection = opensearchserverless.CfnCollection(
            self, "RAGCollection",
            name=f"rag-collection-{env}",
            description="Collection for RAG applications",
            type="VECTORSEARCH",
            standby_replicas="DISABLED"
        )


        # Create global encryption policy for OpenSearch
        encryption_policy = opensearchserverless.CfnSecurityPolicy(
            self, "GlobalEncryptionPolicy",
            name=f"rag-encryption-policy-{env}",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{collection.name}"]
                }],
                "AWSOwnedKey": False,
                "KmsARN": os_kms_key.key_arn
            })
        )
        self.encryption_policy = encryption_policy

        # Network policy: Dashboard access is VPC-only, but collection (data plane)
        # must remain publicly accessible because Bedrock Knowledge Base service
        # accesses OpenSearch from AWS-managed infrastructure, NOT through customer
        # VPC endpoints. This is an architectural requirement of the Bedrock KB service.
        network_policy_document = json.dumps([
            {
                'Rules': [
                    {
                        'Resource': [f'collection/{collection.name}'],
                        'ResourceType': 'dashboard'
                    }
                ],
                'AllowFromPublic': False,
                'SourceVPCEs': [
                    ssm.StringParameter.value_for_string_parameter(
                        self, "/proserv/vpcendpoint/opensearch-vpceid"
                    )
                ]
            },
            {
                'Rules': [
                    {
                        'Resource': [f'collection/{collection.name}'],
                        'ResourceType': 'collection'
                    }
                ],
                # Collection data plane must remain publicly accessible because
                # Bedrock Knowledge Base service accesses OpenSearch from AWS-managed
                # infrastructure, not through customer VPC endpoints.
                'AllowFromPublic': True
            }
        ], separators=(',', ':'))

        # Create global network policy for OpenSearch
        network_policy = opensearchserverless.CfnSecurityPolicy(
            self, "GlobalNetworkPolicy",
            name=f"rag-network-policy-{env}",
            type="network",
            policy=network_policy_document
        )
        self.network_policy = network_policy

        # Add collection dependencies
        collection.node.add_dependency(encryption_policy)
        collection.node.add_dependency(network_policy)
        self.collection = collection

        ssm.StringParameter(self, 'tenant-table-name',
            parameter_name=f"/proserv/dynamodb/tenant-table",
            string_value=tenant_table.table_name
        )

        ssm.StringParameter(self, 's3-bucket-kms-key-arn',
            parameter_name=f"/proserv/s3/kb-kms-key-arn",
            string_value=kms_key.key_arn
        )

        ssm.StringParameter(self, 's3-bucket-for-bk-rag',
            parameter_name=f"/proserv/s3/kb-rag",
            string_value=bucket.bucket_name
        )

        ssm.StringParameter(self, 's3-bucket-for-bk-rag-intermediate',
            parameter_name=f"/proserv/s3/kb-rag-intermediate",
            string_value=intermediate_bucket.bucket_name
        )

        ssm.StringParameter(self, 'opensearch-collection-name',
            parameter_name=f"/proserv/opensearch/collection_name",
            string_value=collection.name
        )

        ssm.StringParameter(self, 'opensearch-collection-arn',
            parameter_name=f"/proserv/opensearch/collection_arn",
            string_value=collection.attr_arn
        )

        ssm.StringParameter(self, 'opensearch-collection-endpoint',
            parameter_name=f"/proserv/opensearch/collection_endpoint",
            string_value=collection.attr_collection_endpoint
        )

        CfnOutput(self, "CollectionArn",            
            value=collection.attr_arn,
            description="Opensearch Collection Arn",
            export_name=f"{env}OpensearchCollectionArn")
        

        # parsing_model_id = self.node.try_get_context("ParsingModelId")
        # self.parsing_model_arn = bedrock.FoundationModel.from_foundation_model_id(
        #     scope=self,
        #     _id='AgentModel',
        #     foundation_model_id=bedrock.FoundationModelIdentifier(parsing_model_id)).model_arn

        parsing_inference_profile_id = self.node.try_get_context("ParsingInferenceProfileId")
        self.parsing_inference_profile_arn = self.format_arn(
            service="bedrock",
            resource="inference-profile",
            resource_name=parsing_inference_profile_id,
            account=Aws.ACCOUNT_ID,
            region=Aws.REGION,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME # Bedrock inference profiles use a slash format
        )

        embedding_model_id = self.node.try_get_context("EmbeddingModelId")
        self.embedding_model_arn = bedrock.FoundationModel.from_foundation_model_id(
            scope=self,
            _id='AgentModel',
            foundation_model_id=bedrock.FoundationModelIdentifier(embedding_model_id)).model_arn

        self.provision_tenant_resources();
        self.provision_demo_tenant();

    def provision_tenant_resources(self):


        cr_tenant_lambda_role = iam.Role(
            self, "CRTenantLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess")
            ]
        )

        # CloudFormation permissions for tenant stack management
        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:{Aws.PARTITION}:cloudformation:{Aws.REGION}:{Aws.ACCOUNT_ID}:stack/bedrock-kb-*/*"
                ],
                actions=[
                    "cloudformation:CreateStack",
                    "cloudformation:UpdateStack",
                    "cloudformation:DeleteStack",
                    "cloudformation:DescribeStacks",
                    "cloudformation:DescribeStackEvents",
                    "cloudformation:GetTemplate"
                ]
            )
        )

        # Bedrock permissions for knowledge base and inference profile management
        # CloudFormation resource handlers for AWS::Bedrock::KnowledgeBase and
        # AWS::Bedrock::DataSource need broad permissions since resources don't exist yet
        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=[
                    "bedrock:CreateKnowledgeBase",
                    "bedrock:DeleteKnowledgeBase",
                    "bedrock:GetKnowledgeBase",
                    "bedrock:ListKnowledgeBases",
                    "bedrock:CreateDataSource",
                    "bedrock:DeleteDataSource",
                    "bedrock:GetDataSource",
                    "bedrock:TagResource",
                    "bedrock:UntagResource",
                    "bedrock:ListTagsForResource",
                    "bedrock:AssociateThirdPartyKnowledgeBase"
                ]
            )
        )

        # iam:PassRole needed for CloudFormation to pass the KB role to Bedrock service
        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:role/BedrockKBRole-*"
                ],
                actions=[
                    "iam:PassRole"
                ],
                conditions={
                    "StringEquals": {
                        "iam:PassedToService": "bedrock.amazonaws.com"
                    }
                }
            )
        )

        # Bedrock inference profile and foundation model lookups
        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:{Aws.PARTITION}:bedrock:*:*:inference-profile/*",
                    f"arn:{Aws.PARTITION}:bedrock:*::foundation-model/*"
                ],
                actions=[
                    "bedrock:GetInferenceProfile",
                    "bedrock:ListInferenceProfiles",
                    "bedrock:GetFoundationModel",
                    "bedrock:ListFoundationModels"
                ]
            )
        )

        # OpenSearch Serverless permissions for collection and access policy management
        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=[
                    "aoss:BatchGetCollection",
                    "aoss:ListAccessPolicies",
                    "aoss:CreateAccessPolicy",
                    "aoss:DeleteAccessPolicy",
                    "aoss:UpdateAccessPolicy",
                    "aoss:GetAccessPolicy"
                ]
            )
        )

        # IAM role management for CloudFormation-created KB roles
        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:role/BedrockKBRole-*"
                ],
                actions=[
                    "iam:PassRole",
                    "iam:CreateRole",
                    "iam:DeleteRole",
                    "iam:GetRole",
                    "iam:AttachRolePolicy",
                    "iam:DetachRolePolicy",
                    "iam:PutRolePolicy",
                    "iam:DeleteRolePolicy",
                    "iam:GetRolePolicy",
                    "iam:TagRole",
                    "iam:UntagRole"
                ]
            )
        )

        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    self.tenant_table.table_arn
                ],
                actions=[
                    'dynamodb:GetItem',
                    'dynamodb:PutItem',
                    'dynamodb:UpdateItem',
                    'dynamodb:DeleteItem',
                    'dynamodb:Scan',
                    'dynamodb:Query',
                    'dynamodb:DescribeTable',
                    'dynamodb:BatchWriteItem'
                ]
            )
        )

        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    self.tenant_table_kms_key.key_arn,
                    self.bucket_key.key_arn,
                    self.os_kms_key.key_arn
                ],
                actions=[
                    'kms:Encrypt',
                    'kms:Decrypt',
                    'kms:ReEncryptFrom',
                    'kms:GenerateDataKey',
                    'kms:DescribeKey'
                ]
            )
        )

        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                        f"{self.bucket.bucket_arn}",
                        f"{self.bucket.bucket_arn}/*"
                ],
                actions=[
                    's3:PutObject',
                    's3:GetObject',
                    's3:DeleteObject',
                    's3:ListBucket'
                ]
            )
        )

        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    self.collection.attr_arn,
                    f"{self.collection.attr_arn}/*"
                ],
                actions=[
                    #"aoss:CreateIndex",
                    #"aoss:DeleteIndex",
                    #"aoss:UpdateIndex",
                    #"aoss:DescribeIndex",
                    #"aoss:ListIndices",
                    #"aoss:*"
                    "aoss:APIAccessAll"
                ]
            )
        )

        cr_tenant_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[
                    f"arn:{Aws.PARTITION}:bedrock:*:*:inference-profile/*",
                    f"arn:{Aws.PARTITION}:bedrock:*::foundation-model/*"
                ],
                actions=[
                    "bedrock:GetInferenceProfile",
                    "bedrock:ListInferenceProfiles",
                    "bedrock:GetFoundationModel",
                    "bedrock:ListFoundationModels"
                ]
            )
        )

        cr_tenant_lambda_layer = _lambda.LayerVersion(
            self, "CRTenantLambdaLayer",
            code=_lambda.Code.from_asset("../asset-output/cr_lambdalayer"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12]
        )

        vpcId = self.node.try_get_context("VpcId")
        vpc = aws_ec2.Vpc.from_lookup(self, "cr-tenant-vpc", vpc_id=vpcId)

        cr_tenant_sg = aws_ec2.SecurityGroup(self, "CRTenantLambdaSG", vpc=vpc, allow_all_outbound=False)
        cr_tenant_sg.add_egress_rule(
            aws_ec2.Peer.any_ipv4(),
            aws_ec2.Port.tcp(443)
        )

        cr_tenant_lambda = _lambda.Function(
            self, "CRTenantLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="app.lambda_handler",
            code=_lambda.Code.from_asset("../custom_resources/tenant_provisioner"),
            role=cr_tenant_lambda_role,
            layers=[cr_tenant_lambda_layer],
            timeout=Duration.minutes(15),
            reserved_concurrent_executions=5,
            tracing=_lambda.Tracing.ACTIVE,
            vpc=vpc,
            vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[cr_tenant_sg],
            environment={
                "S3_BUCKET_NAME": self.bucket.bucket_name,
                "TENANT_TABLE": self.tenant_table.table_name,
                "BUCKET_KMS_KEY_ARN": self.bucket_key.key_arn,
                "OPENSEARCH_KMS_KEY_ARN": self.os_kms_key.key_arn,
                "COLLECTION_NAME": self.collection.name,
                "COLLECTION_ARN": self.collection.attr_arn,
                "COLLECTION_ENDPOINT": self.collection.attr_collection_endpoint,
                "EMBEDDING_MODEL_ARN": self.embedding_model_arn,
                # "PARSING_MODEL_ARN": self.parsing_model_arn,
                "PARSING_INFERENCE_PROFILE_ARN": self.parsing_inference_profile_arn
            }
        )

        self.cr_tenant_lambda = cr_tenant_lambda

        self.cr_tenant_lambda_index_access_policy = opensearchserverless.CfnAccessPolicy(
           self, "CRTenantLambdAccessPolicy",
           type="data",
           name=f"cr-tenant-lambda-ap-{self.env_name}",
           policy=json.dumps([{
              "Rules": [
                 {
                   "ResourceType": "index",
                   "Resource": [f"index/{self.collection.name}/*"],
                   "Permission": ["aoss:*"]
                 },
                 {
                "ResourceType": "collection",
                "Resource": [f"collection/{self.collection.name}"],
                "Permission": ["aoss:*"]
                }
           ],
           "Principal": [
            cr_tenant_lambda_role.role_arn,
            f"arn:aws:iam::{Aws.ACCOUNT_ID}:root"
            ]
           }])
        )

        self.cr_tenant_lambda_index_access_policy.node.add_dependency(self.collection)
        self.cr_tenant_lambda_index_access_policy.node.add_dependency(cr_tenant_lambda_role)
        
        self.cr_tenant_lambda.node.add_dependency(cr_tenant_lambda_layer)
        self.cr_tenant_lambda.node.add_dependency(cr_tenant_lambda_role)
        self.cr_tenant_lambda.node.add_dependency(self.cr_tenant_lambda_index_access_policy)
        self.cr_tenant_lambda.node.add_dependency(self.collection)

    def provision_demo_tenant(self):
        random_suffix = "".join(
            secrets.choice(string.ascii_letters + string.digits)
            for _ in range(4)
        )
        cr_tenant_provider = cr.Provider(
            scope=self,
            id='CRTenantProvider',
            on_event_handler=self.cr_tenant_lambda) 

        cr_tenant_creator = CustomResource(
            scope=self,
            id=f"CRTenantResourcesCreate",
            service_token=cr_tenant_provider.service_token,
            resource_type="Custom::TenantResources",
            properties={
                'SEED_TO_CAUSE_UPDATE': random_suffix
                # 'vector_size': 1024,  # Depends on embeddings model
                # 'metadata_field': 'AMAZON_BEDROCK_METADATA',
                # 'text_field': 'AMAZON_BEDROCK_TEXT_CHUNK',
                # 'vector_field': 'vector'
            }
        )

        # Add dependencies
        cr_tenant_creator.node.add_dependency(self.collection)
        cr_tenant_creator.node.add_dependency(self.cr_tenant_lambda_index_access_policy)

        # Set update replace policy to RETAIN to preserve tenant data
        cfn_tenant_creator = cr_tenant_creator.node.default_child
        cfn_tenant_creator.cfn_options.update_replace_policy = CfnDeletionPolicy.RETAIN
