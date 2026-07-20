#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.vpc import VpcStack
from stacks.api import ChatApiStack
from stacks.agentcore_stack import AgentCoreStack
from stacks.opensearch_stack import OpensearchStack


app = cdk.App()
environment=app.node.try_get_context("environment")

# 5.1 - Consistent resource tagging strategy
cdk.Tags.of(app).add("Application", "MultiAgentRAG")
cdk.Tags.of(app).add("ManagedBy", "CDK")

VpcStack(
    app, 
    "VPCEndpointsStack", 
    env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION'))
    )

opensearch=OpensearchStack(
    app, 
    "OpensearchStack",
    env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION')))
agent=AgentCoreStack(
    app, 
    "AgentCoreStack",
    env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION')))
agent.add_dependency(opensearch)
api=ChatApiStack(
    app, 
    "ChatApiStack",
    env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION')))
api.add_dependency(opensearch)
api.add_dependency(agent)

app.synth()
