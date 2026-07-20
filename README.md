# **Prerequisites**
1. dotnet sdk 8
   - dotnet tool install -g Amazon.Lambda.Tools (run this to install dotnet tools for lambda packaging)
2. python 3.13
3. aws cli
4. nodejs (use npm to install aws-cdk)
5. cdk cli
6. identify a S3 bucket for deployment
7. Open a quota increase request to raise API Gateway Max Integration timeout from 29 seconds to 10 minutes.

## Security Note
This sample uses Lambda environment variables for configuration values such as model IDs, S3 bucket names, and guardrail IDs. These are resource identifiers, not secrets. For production deployments, consider using AWS Secrets Manager for any truly sensitive configuration values (API keys, database credentials, etc.).

# **Initialize cdk**
In order to use cdk for deployment, the following command must be executed once per account per region.

`cdk boostrap`

This cmd creates a "CDKToolkit" cloudformation stack. You may also check in AWS Cloudformation console to verify the existence of this stack to determine if this cmd has been executed.

If you have not installed AWS CDK, please follow the instructions provided through the following link:
[AWS CDK Install](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html)


# **AWS Profile**

Create a profile for aws cli


# **Build steps**
0. add pipeline-params.ini file at the root of this repo with the following parameters. **Make sure there is an empty line at the end of pipeline-params.ini file.**
```
environment=sandbox
awscliprofile=<replace with AWS Profile name>
ContainerRegistry=lambda-pptx-gen
BucketDev=<replace with a S3 bucket name>
AccessLogBucketName=<replace with a S3 bucket name>
VpcId=<replace with VPC Id>
ParsingInferenceProfileId=us.anthropic.claude-3-5-sonnet-20240620-v1:0
AgentInferenceProfileId=us.anthropic.claude-3-5-sonnet-20240620-v1:0
EmbeddingModelId=amazon.titan-embed-text-v2:0
ImageGenerationModelId=amazon.titan-image-generator-v2:0

```


1. chmod +x ./pipeline-build.sh 
2. /bin/bash pipeline-build.sh


# **Deployment steps**

1. chmod +x ./pipeline-deploy.sh 
2. /bin/bash pipeline-deploy.sh

# **Demo Tenant**

A tenant will be created after the successful deployment.

# **Architecture Overview**

For the complete architecture diagram with AWS icons, see `architecture-diagram.drawio` - open with draw.io (diagrams.net) for the best visual representation of the multi-agent RAG system.

