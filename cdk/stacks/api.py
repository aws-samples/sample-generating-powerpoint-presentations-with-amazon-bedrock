from aws_cdk import (
    Stack,
    Duration,
    aws_dynamodb,
    aws_cognito,
    aws_kms as kms,
    aws_lambda as _lambda,
    aws_apigateway as _apigw,
    aws_logs,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_ssm as ssm,
    aws_ec2,
    aws_iam,
    aws_wafv2 as wafv2,
    Aws,
    Fn,
    Tags
    # aws_sqs as sqs,
)
from constructs import Construct
from .util import get_prefix_list_id
import os

class ChatApiStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env_name = self.node.try_get_context("environment")
        profile = self.node.try_get_context("awscliprofile")
        
        # Get supervisor agent ID and alias ID from SSM parameters
        agentId = ssm.StringParameter.value_for_string_parameter(
            self, "/proserv/bedrock/super_agent_id"
        )
        agentAliasId = ssm.StringParameter.value_for_string_parameter(
            self, "/proserv/bedrock/super_agent_alias_id"
        )
        Tags.of(self).add("Environment", env_name)

        # Look up prefix list IDs using boto3 at synthesis time
        dynamodb_prefix_list = get_prefix_list_id('dynamodb', profile)
        s3_prefix_list = get_prefix_list_id('s3', profile)

        vpcId = self.node.try_get_context("VpcId")
        vpc = aws_ec2.Vpc.from_lookup(self, "VPC", vpc_id=vpcId)

        kms_key = kms.Key(self, "TenantTableKMS",
            enable_key_rotation=True,
            rotation_period=Duration.days(180)
        )
        chat_table = aws_dynamodb.Table(
            self, f"chat-sessions-{env_name}",
            table_name=f"chat-sessions-{env_name}",
            partition_key=aws_dynamodb.Attribute(
                name="sessionId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=aws_dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            point_in_time_recovery=True,
            deletion_protection=True
        )

        user_pool = aws_cognito.UserPool(self, "userpool",
            standard_attributes=aws_cognito.StandardAttributes(
                fullname=aws_cognito.StandardAttribute(
                    required=True,
                    mutable=False
                )
            ),
            sign_in_policy=aws_cognito.SignInPolicy(
                allowed_first_auth_factors=aws_cognito.AllowedFirstAuthFactors(password=True)
            ),
            self_sign_up_enabled=False,
            sign_in_aliases=aws_cognito.SignInAliases(
                username=True
            ),
            auto_verify=aws_cognito.AutoVerifiedAttrs(email=True, phone=True),
            feature_plan=aws_cognito.FeaturePlan.PLUS,
            advanced_security_mode=aws_cognito.AdvancedSecurityMode.ENFORCED
        )

        user_pool.add_client("democlient",
            auth_flows=aws_cognito.AuthFlow(
                user_password=True,
                user_srp=True
            )
        )

        sg = aws_ec2.SecurityGroup(self, "ChatApiSG", vpc=vpc, allow_all_outbound=False)
        sg.add_egress_rule(
            aws_ec2.Peer.any_ipv4(),
            aws_ec2.Port.tcp(443)
        )
        sg.add_egress_rule(
            aws_ec2.Peer.prefix_list(dynamodb_prefix_list),
            aws_ec2.Port.tcp(443)
        )
        sg.add_egress_rule(
            aws_ec2.Peer.prefix_list(s3_prefix_list),
            aws_ec2.Port.tcp(443)
        )

        # The code that defines your stack goes here
        api_lambda = _lambda.Function(self, 'chat-api-func',
                                       handler='ApiBootstrap',
                                       runtime=_lambda.Runtime.DOTNET_8,
                                        timeout=Duration.seconds(29),
                                       code=_lambda.Code.from_asset("../asset-output/chat-api-function.zip"),
                                       reserved_concurrent_executions=100,
                                       tracing=_lambda.Tracing.ACTIVE,
                                    vpc=vpc,
          vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
          allow_public_subnet=True,
          security_groups=[sg])
        api_lambda.add_environment("CHAT_TABLE_NAME", chat_table.table_name)
        api_lambda.add_environment("PARTITION", Aws.PARTITION)
        api_lambda.add_environment("REGION", Aws.REGION)
        api_lambda.add_environment("ACCOUNTID", Aws.ACCOUNT_ID)
        api_lambda.add_environment("AGENT_ID", agentId)
        api_lambda.add_environment("AGENT_ALIAS_ID", agentAliasId)

        policy = aws_iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaBasicExecutionRole')
        api_lambda.role.add_managed_policy(policy)
        api_lambda.role.add_managed_policy(
            aws_iam.ManagedPolicy.from_aws_managed_policy_name('AWSXRayDaemonWriteAccess')
        )
        api_lambda.role.attach_inline_policy(
            aws_iam.Policy(self, "bedrock-invoke",
                statements=[
                    aws_iam.PolicyStatement(
                        actions=[
                            "bedrock:GetAgent",
                            "bedrock:GetAgentVersion",
                            "bedrock:CreateSession",
                            "bedrock:EndSession",
                            "bedrock:DeleteSession",
                            "bedrock:InvokeAgent",
                            "bedrock:InvokeInlineAgent",
                            "bedrock:RenderPrompt",
                            "bedrock:InvokeFlow",
                            "bedrock:OptimizePrompt",
                            "bedrock:Retrieve"
                        ],
                        resources=[
                            f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:agent-alias/{agentId}/{agentAliasId}"
                        ]
                    )
                ])
        )
        api_lambda.role.attach_inline_policy(
            aws_iam.Policy(self, "bedrock-session",
                statements=[
                    aws_iam.PolicyStatement(
                        actions=[
                            "bedrock:CreateSession",
                            "bedrock:EndSession",
                            "bedrock:DeleteSession"
                        ],
                        resources=[
                            f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:session/*"
                        ]
                    )
                ])
        )
        api_lambda.role.attach_inline_policy(
            aws_iam.Policy(self, "bedrock-listkb",
                statements=[
                    aws_iam.PolicyStatement(
                        actions=[
                            "bedrock:ListKnowledgeBases"
                        ],
                        resources=[
                            "*"
                        ]
                    )
                ])
        )

        api_lambda.role.attach_inline_policy(
            aws_iam.Policy(self, "dynamodb-kms-policy",
                statements=[
                    aws_iam.PolicyStatement(
                        effect=aws_iam.Effect.ALLOW,
                        resources=[
                            kms_key.key_arn
                        ],
                        actions=[
                            'kms:Encrypt',
                            'kms:Decrypt',
                            'kms:ReEncrypt',
                            'kms:GenerateDataKey',
                            'kms:DescribeKey'
                        ]
                    )
                ]
            )
        )

        api_lambda.role.attach_inline_policy(
            aws_iam.Policy(self, "sts-assume-tenant-role",
                statements=[
                    aws_iam.PolicyStatement(
                        effect=aws_iam.Effect.ALLOW,
                        resources=[
                            f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:role/tenant-role-*"
                        ],
                        actions=[
                            'sts:AssumeRole'
                        ]
                    )
                ]
            )
        )
        
        chat_table.grant_full_access(api_lambda)
        auth = _apigw.CognitoUserPoolsAuthorizer(self, "authorizer",
            cognito_user_pools=[user_pool]
        )
        # Create API Gateway
        access_log_group = aws_logs.LogGroup(
            self, "ApiAccessLogs",
            retention=aws_logs.RetentionDays.ONE_YEAR
        )

        restapi = _apigw.LambdaRestApi(
            self,
            "chat-api",
            handler=api_lambda,
            integration_options=_apigw.LambdaIntegrationOptions(
                allow_test_invoke=True,
                timeout=Duration.seconds(29)#set to 29 for the time being
            ),
            default_method_options={
                "authorization_type": _apigw.AuthorizationType.COGNITO,
                "authorizer": auth
            },
            deploy_options={
                "stage_name": env_name,
                "access_log_destination": _apigw.LogGroupLogDestination(access_log_group),
                "access_log_format": _apigw.AccessLogFormat.json_with_standard_fields(
                    caller=True,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True
                )
            }
        )

        # CloudWatch alarms for security monitoring
        security_topic = sns.Topic(self, "SecurityAlarmsTopic",
            display_name="Security Alarms"
        )

        cloudwatch.Alarm(self, "Api4xxAlarm",
            metric=restapi.metric_client_error(period=Duration.minutes(5)),
            threshold=100,
            evaluation_periods=3,
            alarm_description="High rate of 4xx errors - potential attack",
            actions_enabled=True
        ).add_alarm_action(cw_actions.SnsAction(security_topic))

        cloudwatch.Alarm(self, "Api5xxAlarm",
            metric=restapi.metric_server_error(period=Duration.minutes(5)),
            threshold=10,
            evaluation_periods=2,
            alarm_description="API server errors detected",
            actions_enabled=True
        ).add_alarm_action(cw_actions.SnsAction(security_topic))

        cloudwatch.Alarm(self, "LambdaErrorsAlarm",
            metric=api_lambda.metric_errors(period=Duration.minutes(5)),
            threshold=5,
            evaluation_periods=2,
            alarm_description="Chat API Lambda errors detected",
            actions_enabled=True
        ).add_alarm_action(cw_actions.SnsAction(security_topic))

        cloudwatch.Alarm(self, "DynamoDBThrottleAlarm",
            metric=chat_table.metric_throttled_requests_for_operations(
                operations=[aws_dynamodb.Operation.PUT_ITEM, aws_dynamodb.Operation.GET_ITEM, aws_dynamodb.Operation.QUERY],
                period=Duration.minutes(5)
            ),
            threshold=5,
            evaluation_periods=2,
            alarm_description="DynamoDB throttling detected",
            actions_enabled=True
        ).add_alarm_action(cw_actions.SnsAction(security_topic))

        # 5.5 - WAF integration for API Gateway protection
        web_acl = wafv2.CfnWebACL(self, "ApiWafAcl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            scope="REGIONAL",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="ChatApiWaf",
                sampled_requests_enabled=True
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                            excluded_rules=[
                                wafv2.CfnWebACL.ExcludedRuleProperty(name="SizeRestrictions_BODY")
                            ]
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="CommonRuleSet",
                        sampled_requests_enabled=True
                    )
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesKnownBadInputsRuleSet"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="KnownBadInputs",
                        sampled_requests_enabled=True
                    )
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitRule",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000,
                            aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimit",
                        sampled_requests_enabled=True
                    )
                )
            ]
        )

        wafv2.CfnWebACLAssociation(self, "ApiWafAssociation",
            resource_arn=restapi.deployment_stage.stage_arn,
            web_acl_arn=web_acl.attr_arn
        )
