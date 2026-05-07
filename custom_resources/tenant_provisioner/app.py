import json
import logging
import cfnresponse # AWS Lambda helper library for CloudFormation custom resources

from datetime import datetime
import boto3
from botocore.exceptions import ClientError
import uuid
import os
import time
from urllib import parse
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
cloudformation_client = boto3.client('cloudformation')
opensearch_serverless_client = boto3.client('opensearchserverless')
bedrock_client = boto3.client('bedrock')
table_name = os.environ['TENANT_TABLE']
bucket_name = os.environ['S3_BUCKET_NAME']
bucket_kms_key_arn=os.environ['BUCKET_KMS_KEY_ARN']
opensearch_kms_key_arn=os.environ['OPENSEARCH_KMS_KEY_ARN']
collection_name=os.environ['COLLECTION_NAME']
collection_arn=os.environ['COLLECTION_ARN']
collection_endpoint=os.environ['COLLECTION_ENDPOINT']
metadata_field="AMAZON_BEDROCK_METADATA"
text_field="AMAZON_BEDROCK_TEXT_CHUNK"
vector_field="vector"
vector_size=1024

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def create_tenant_record(
    tenant_id:str,
    index_name:str,
    stack_name:str,
    role_arn:str,
    knowledgebase_id:str,
    knowledgebase_arn:str,
    datasource_id:str):
    table = dynamodb.Table(os.environ['TENANT_TABLE'])

    with table.batch_writer() as batch:
    
        batch.put_item(
            Item={
                'tenant_id': tenant_id,
                'stack_name': stack_name,
                'index_name': index_name,
                'role_arn': role_arn,
                'knowledge_base_id': knowledgebase_arn,
                'knowledge_base_arn': knowledgebase_arn,
                'data_source_id': datasource_id,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
                'status': 'active',
                'type': 'DEMO'
            }
        )
    
    logger.debug(f"tenant_id: {tenant_id} is inserted successfully")

def create_s3_prefixes(tenant_id: str):
    """
    Create S3 prefixes for each tenant ID
    """
    
    try:
        # Create main tenant prefix
        tenant_prefix = f"{tenant_id}/"
        
        # Create an empty object to establish the prefix
        s3_client.put_object(
            Bucket=bucket_name,
            Key=tenant_prefix
        )
        logger.info(f"Created S3 prefix: {tenant_prefix}")
        
        # Create additional subdirectories if needed
        subdirs = ['raw/', 'transformed/']
        for subdir in subdirs:
            prefix_path = f"{tenant_prefix}{subdir}"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=prefix_path
            )
            logger.info(f"Created S3 subdir: {prefix_path}")
        
        return {
            'tenant_id': tenant_id,
            'main_prefix': tenant_prefix,
            'subdirectories': [f"{tenant_prefix}{subdir}" for subdir in subdirs]
        }
        
    except ClientError as e:
        logger.error(f"Error creating S3 prefixes: {e}")
        raise

def test_collection_endpoint_connectivity(endpoint: str, max_retries: int = 10, wait_time: int = 15):
    """
    Test if the OpenSearch Serverless endpoint is actually accessible
    This helps avoid 404 errors when the collection is ACTIVE but endpoint not ready
    """
    from urllib import parse
    
    # Extract hostname from endpoint
    parsed_url = parse.urlparse(endpoint)
    hostname = parsed_url.hostname
    
    if not hostname:
        logger.error(f"Could not extract hostname from endpoint: {endpoint}")
        return False
    
    logger.info(f"Testing connectivity to OpenSearch endpoint: {hostname}")
    
    for attempt in range(max_retries):
        try:
            credentials = boto3.Session().get_credentials()
            awsauth = AWS4Auth(credentials.access_key, credentials.secret_key,
                               os.environ['AWS_REGION'], 'aoss', session_token=credentials.token)

            # Build the OpenSearch client
            client = OpenSearch(hosts=[{'host': hostname, 'port': 443}],
                                http_auth=awsauth,
                                use_ssl=True,
                                verify_certs=True,
                                connection_class=RequestsHttpConnection,
                                timeout=30)
            
            # Try a simple cluster info call
            cluster_info = client.info()
            cluster_name = cluster_info.get('cluster_name', 'unknown')
            logger.info(f"Successfully connected to OpenSearch cluster: {cluster_name}")
            return True
            
        except Exception as e:
            error_msg = str(e).lower()
            if "404" in error_msg or "not found" in error_msg:
                logger.warning(f"Endpoint returned 404 - DNS/endpoint not ready yet. Waiting {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
            elif "403" in error_msg or "forbidden" in error_msg:
                logger.warning(f"Access forbidden - policies may not be active yet. Waiting {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
            elif "timeout" in error_msg or "connection" in error_msg:
                logger.warning(f"Connection timeout - endpoint may not be ready. Waiting {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
            else:
                logger.error(f"Unexpected error testing endpoint connectivity: {e}")
            
            if attempt < max_retries - 1:
                time.sleep(wait_time)
    
    logger.warning(f"Endpoint connectivity test failed after {max_retries} attempts. Proceeding anyway...")
    return False

def wait_for_collection_health(collection_name: str, max_retries: int = 30, wait_time: int = 5):
    """
    Wait for OpenSearch Serverless collection to become healthy and active
    """
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            response = opensearch_serverless_client.batch_get_collection(
                names=[collection_name]
            )
            
            collections = response.get('collectionDetails', [])
            if not collections:
                logger.warning(f"Collection '{collection_name}' not found. Waiting {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
                time.sleep(wait_time)
                retry_count += 1
                continue
            
            collection = collections[0]
            status = collection.get('status')
            
            if status == 'ACTIVE':
                collection_endpoint = collection.get('collectionEndpoint')
                if collection_endpoint:
                    logger.info(f"Collection '{collection_name}' is ACTIVE with endpoint: {collection_endpoint}")
                    return collection_endpoint
                else:
                    logger.warning(f"Collection '{collection_name}' is ACTIVE but endpoint not available yet")
            
            elif status in ['CREATING', 'DELETING']:
                logger.info(f"Collection '{collection_name}' status: {status}. Waiting {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
            
            elif status == 'FAILED':
                logger.error(f"Collection '{collection_name}' is in FAILED state")
                raise Exception(f"Collection {collection_name} failed to become healthy")
            
            else:
                logger.warning(f"Collection '{collection_name}' has unexpected status: {status}")
            
        except Exception as e:
            logger.error(f"Error checking collection health: {e}")
            if "AccessDeniedException" in str(e):
                logger.error("Access denied when checking collection status. Proceeding anyway...")
                break
        
        time.sleep(wait_time)
        retry_count += 1
    
    if retry_count >= max_retries:
        logger.warning(f"Collection '{collection_name}' health check timed out after {max_retries} attempts. Proceeding anyway...")
    
    return None

def wait_for_access_policies_active(collection_name: str, max_retries: int = 20, wait_time: int = 5):
    """
    Wait for OpenSearch Serverless access policies to become active
    """
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # List data access policies
            response = opensearch_serverless_client.list_access_policies(
                type='data'
            )
            
            policies = response.get('accessPolicySummaries', [])
            active_policies = []
            
            for policy in policies:
                policy_name = policy.get('name', '')
                # Check if this policy is related to our collection
                if collection_name.lower() in policy_name.lower() or 'tenant' in policy_name.lower():
                    policy_status = policy.get('policyVersion', None)
                    if policy_status:
                        active_policies.append(policy_name)
                        logger.debug(f"Found active access policy: {policy_name}")
            
            if active_policies:
                logger.info(f"Access policies are active: {active_policies}")
                return True
            
            logger.info(f"Waiting for access policies to become active... (attempt {retry_count + 1}/{max_retries})")
            
        except Exception as e:
            logger.error(f"Error checking access policies: {e}")
            if "AccessDeniedException" in str(e):
                logger.warning("Access denied when checking policies. Proceeding anyway...")
                return True
        
        time.sleep(wait_time)
        retry_count += 1
    
    logger.warning(f"Access policy check timed out after {max_retries} attempts. Proceeding anyway...")
    return True

def create_collection_index(hostname: str, index_name: str, event:object):
    """
    Create an index in the given collection with the given param
    """
    try:
        logger.info(f"Creating index '{index_name}' in collection at hostname: {hostname}")
        
        credentials = boto3.Session().get_credentials()
        awsauth = AWS4Auth(credentials.access_key, credentials.secret_key,
                           os.environ['AWS_REGION'], 'aoss', session_token=credentials.token)

        # Build the OpenSearch client
        client = OpenSearch(hosts=[{'host': hostname, 'port': 443}],
                            http_auth=awsauth,
                            use_ssl=True,
                            verify_certs=True,
                            connection_class=RequestsHttpConnection,
                            timeout=300)
                
        # Check if index already exists
        response = client.indices.exists(index=index_name)
        if response:
            logger.info(f'Index {index_name} already exists, skipping creation')
            return
        
        logger.info(f"Creating new index: {index_name}")
        
        # Create index with comprehensive error handling
        index_body = {
            'settings': {
                'index.knn': True,
                'number_of_shards': 1,
                'number_of_replicas': 0
            },
            'mappings': {
                'properties': {
                    metadata_field: {
                        'type': 'text',
                        'index': False
                    },
                    text_field: {
                        'type': 'text'
                    },
                    'id': {
                        'type': 'text',
                        'fields': {
                            'keyword': {
                                'type': 'keyword',
                                'ignore_above': 256
                            }
                        }
                    },
                    'x-amz-bedrock-kb-source-uri': {
                        'type': 'text',
                        'fields': {
                            'keyword': {
                                'type': 'keyword',
                                'ignore_above': 256
                            }
                        }
                    },
                    vector_field: {
                        'type': 'knn_vector',
                        'dimension': vector_size,
                        'method': {
                            'name': 'hnsw',
                            'engine': 'faiss',
                            'parameters': {
                                'ef_construction': 512,
                                'm': 16
                            }
                        }
                    }
                }
            }
        }
        
        response = client.indices.create(index=index_name, body=index_body)
        logger.info(f"Successfully created index '{index_name}': {json.dumps(response, default=str)}")
        
    except Exception as e:
        logger.error(f"Error creating index '{index_name}': {e}")
        logger.error(f"Error type: {type(e).__name__}")
        if hasattr(e, 'info'):
            logger.error(f"Error details: {e.info}")
        raise

def get_inference_profile(inference_profile_arn: str):
    """
    Look up destination regions for a Bedrock inference profile
    
    Args:
        inference_profile_arn: ARN of the inference profile
        
    Returns:
        dict: Information about the inference profile including destination regions
    """
    try:
        # Extract inference profile ID from ARN
        # ARN format: arn:aws:bedrock:region:account:inference-profile/profile-id
        profile_id = inference_profile_arn.split('/')[-1]
        
        logger.info(f"Looking up inference profile: {profile_id}")
        
        # Get inference profile details
        response = bedrock_client.get_inference_profile(
            inferenceProfileIdentifier=profile_id
        )
        
        profile_info = {
            'profile_id': profile_id,
            'profile_arn': inference_profile_arn,
            'profile_name': response.get('inferenceProfileName'),
            'description': response.get('description'),
            'status': response.get('status'),
            'type': response.get('type'),
            'models': [],
            'destination_regions': set()
        }
        
        # Extract model information and destination regions
        models = response.get('models', [])

        for model in models:
            model_arn = model.get('modelArn', '')
            profile_info['models'].append(model_arn)
        
        return profile_info
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Inference profile not found: {inference_profile_arn}")
            return {
                'profile_id': profile_id,
                'profile_arn': inference_profile_arn,
                'error': 'Profile not found',
                'destination_regions': []
            }
        else:
            logger.error(f"Error looking up inference profile {inference_profile_arn}: {e}")
            raise
    
    except Exception as e:
        logger.error(f"Unexpected error looking up inference profile {inference_profile_arn}: {e}")
        raise

def build_stack_parameters(tenant_id: str, index_name: str):
    """
    Build CloudFormation stack parameters for Bedrock Knowledge Base
    Consolidates parameter construction for both CREATE and UPDATE operations
    """
    inference_profile_arn=os.environ.get('PARSING_INFERENCE_PROFILE_ARN', f'arn:aws:bedrock:{os.environ["AWS_REGION"]}::foundation-model/amazon.titan-text-premier-v1:0')
    inference_profile=get_inference_profile(inference_profile_arn)

    return [
        {
            'ParameterKey': 'TenantId',
            'ParameterValue': tenant_id
        },
        {
            'ParameterKey': 'TenantIdShort',
            'ParameterValue': tenant_id[:29]
        },
        {
            'ParameterKey': 'VectorBucket',
            'ParameterValue': bucket_name
        },
        {
            'ParameterKey': 'VectorBucketKmsKeyArn',
            'ParameterValue': bucket_kms_key_arn
        },
        {
            'ParameterKey': 'SupplementalStorageBucket',
            'ParameterValue': bucket_name
        },
        {
            'ParameterKey': 'OpenSearchCollectionArn',
            'ParameterValue': collection_arn           
        },
        {
            'ParameterKey': 'OpenSearchCollectionName',
            'ParameterValue': collection_name          
        },
        {
            'ParameterKey': 'OpenSearchKmsKeyArn',
            'ParameterValue': opensearch_kms_key_arn
        },
        {
            'ParameterKey': 'VectorIndexName',
            'ParameterValue': index_name
        },
        {
            'ParameterKey': 'EmbeddingModelArn',
            'ParameterValue': os.environ.get('EMBEDDING_MODEL_ARN', f'arn:aws:bedrock:{os.environ["AWS_REGION"]}::foundation-model/amazon.titan-embed-text-v2:0')
        },
        {
            'ParameterKey': 'ParsingModelArns',
            'ParameterValue': ",".join(inference_profile['models'])
        },
        {
            'ParameterKey': 'ParsingInferenceProfileArn',
            'ParameterValue': os.environ.get('PARSING_INFERENCE_PROFILE_ARN', f'arn:aws:bedrock:{os.environ["AWS_REGION"]}::foundation-model/amazon.titan-text-premier-v1:0')
        },
        {
            'ParameterKey': 'VectorField',
            'ParameterValue': vector_field
        },
        {
            'ParameterKey': 'TextField',
            'ParameterValue': text_field
        },
        {
            'ParameterKey': 'MetadataField',
            'ParameterValue': metadata_field
        },
        {
            'ParameterKey': 'EnableSupplementalDataStorage',
            'ParameterValue': os.environ.get('ENABLE_SUPPLEMENTAL_DATA_STORAGE', 'true')
        }
    ]

def deploy_bedrock_knowledge_base_stack(
    tenant_id: str, 
    vector_bucket: str, 
    vector_bucket_kms_key_arn: str,
    index_name: str,
    collection_arn: str,
    collection_name: str):
    """
    Deploy Bedrock Knowledge Base using CloudFormation template
    """
    try:
        stack_name = f"bedrock-kb-{tenant_id}"
                
        # Read the CloudFormation template
        template_path = os.path.join(os.path.dirname(__file__), 'bedrock_kb_template.yaml')
        with open(template_path, 'r') as template_file:
            template_body = template_file.read()
        
        # Deploy the CloudFormation stack
        response = cloudformation_client.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=build_stack_parameters(tenant_id, index_name),
            Capabilities=['CAPABILITY_NAMED_IAM']
        )
        
        stack_id = response['StackId']
        logger.info(f"Created CloudFormation stack: {stack_id}")
        
        # Wait for stack creation to complete
        waiter = cloudformation_client.get_waiter('stack_create_complete')
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={
                'Delay': 30,
                'MaxAttempts': 60  # Wait up to 30 minutes
            }
        )
        
        # Get stack outputs
        stack_description = cloudformation_client.describe_stacks(StackName=stack_name)
        outputs = stack_description['Stacks'][0].get('Outputs', [])
        
        result = {'stack_name': stack_name, 'stack_id': stack_id}
        for output in outputs:
            key = output['OutputKey']
            value = output['OutputValue']
            if key == 'KnowledgeBaseId':
                result['knowledge_base_id'] = value
            elif key == 'DataSourceId':
                result['data_source_id'] = value
            elif key == 'KnowledgeBaseArn':
                result['knowledge_base_arn'] = value
            elif key == 'RoleArn':
                result['role_arn'] = value
        
        logger.info(f"Successfully deployed Bedrock Knowledge Base stack: {result}")
        return result
        
    except ClientError as e:
        logger.error(f"Error deploying Bedrock Knowledge Base stack: {e}")
        raise

def delete_opensearch_index(hostname: str, index_name: str):
    """
    Delete an index from OpenSearch Serverless collection
    """
    try:
        credentials = boto3.Session().get_credentials()
        awsauth = AWS4Auth(credentials.access_key, credentials.secret_key,
                           os.environ['AWS_REGION'], 'aoss', session_token=credentials.token)

        # Build the OpenSearch client
        client = OpenSearch(hosts=[{'host': hostname, 'port': 443}],
                            http_auth=awsauth,
                            use_ssl=True,
                            verify_certs=True,
                            connection_class=RequestsHttpConnection,
                            timeout=300)
        
        # Check if index exists before trying to delete
        if client.indices.exists(index=index_name):
            response = client.indices.delete(index=index_name)
            logger.info(f"Deleted OpenSearch index: {index_name}")
            return response
        else:
            logger.info(f"OpenSearch index {index_name} does not exist, skipping deletion")
            return None
            
    except Exception as e:
        logger.error(f"Error deleting OpenSearch index {index_name}: {e}")
        # Don't raise exception as this shouldn't block the overall deletion process
        return None

def delete_s3_tenant_objects(tenant_id: str):
    """
    Delete all S3 objects for a specific tenant
    """
    try:
        tenant_prefix = f"{tenant_id}/"
        
        # List all objects with the tenant prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=tenant_prefix)
        
        objects_to_delete = []
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    objects_to_delete.append({'Key': obj['Key']})
        
        if objects_to_delete:
            # Delete objects in batches (max 1000 per batch)
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i:i+1000]
                response = s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': batch}
                )
                logger.info(f"Deleted {len(batch)} S3 objects for tenant {tenant_id}")
        else:
            logger.info(f"No S3 objects found for tenant {tenant_id}")
            
    except ClientError as e:
        logger.error(f"Error deleting S3 objects for tenant {tenant_id}: {e}")
        # Don't raise exception as this shouldn't block the overall deletion process

def delete_cloudformation_stack(stack_name: str):
    """
    Delete a CloudFormation stack and wait for completion
    """
    try:
        # Check if stack exists
        try:
            cloudformation_client.describe_stacks(StackName=stack_name)
        except ClientError as e:
            if 'does not exist' in str(e):
                logger.info(f"Stack {stack_name} does not exist, skipping deletion")
                return
            raise
        
        # Delete the stack
        cloudformation_client.delete_stack(StackName=stack_name)
        logger.info(f"Initiated deletion of CloudFormation stack: {stack_name}")
        
        # Wait for stack deletion to complete
        waiter = cloudformation_client.get_waiter('stack_delete_complete')
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={
                'Delay': 30,
                'MaxAttempts': 60  # Wait up to 30 minutes
            }
        )
        logger.info(f"Successfully deleted CloudFormation stack: {stack_name}")
        
    except ClientError as e:
        logger.error(f"Error deleting CloudFormation stack {stack_name}: {e}")
        raise

def update_tenant_record(tenant_id: str, updates: dict):
    """
    Update tenant record in DynamoDB table with new information
    """
    try:
        table = dynamodb.Table(table_name)
        
        # Build update expression dynamically
        update_expression_parts = []
        expression_attribute_values = {}
        
        for key, value in updates.items():
            update_expression_parts.append(f"{key} = :{key}")
            expression_attribute_values[f":{key}"] = value
        
        if update_expression_parts:
            update_expression = "SET " + ", ".join(update_expression_parts)
            
            # Add updated timestamp
            update_expression += ", updated_at = :updated_at"
            expression_attribute_values[":updated_at"] = datetime.now().isoformat()
            
            table.update_item(
                Key={'tenant_id': tenant_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_attribute_values
            )
            logger.info(f"Updated tenant record: {tenant_id} with {updates}")
        
    except ClientError as e:
        logger.error(f"Error updating tenant record {tenant_id}: {e}")
        # Don't raise exception as this shouldn't block the overall update process

def delete_tenant_record(tenant_id: str):
    """
    Delete tenant record from DynamoDB table
    """
    try:
        table = dynamodb.Table(table_name)
        table.delete_item(Key={'tenant_id': tenant_id})
        logger.info(f"Deleted tenant record: {tenant_id}")
        
    except ClientError as e:
        logger.error(f"Error deleting tenant record {tenant_id}: {e}")
        # Don't raise exception as this shouldn't block the overall deletion process

def update_tenant_stack(tenant_id: str, stack_name: str, index_name: str):
    """
    Update a specific tenant's CloudFormation stack with latest template
    """
    try:
        logger.info(f"Updating CloudFormation stack for tenant {tenant_id}: {stack_name}")
        
        # Read the latest CloudFormation template
        template_path = os.path.join(os.path.dirname(__file__), 'bedrock_kb_template.yaml')
        with open(template_path, 'r') as template_file:
            template_body = template_file.read()
        
        # Update the CloudFormation stack with latest template and parameters
        response = cloudformation_client.update_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=build_stack_parameters(tenant_id, index_name),
            Capabilities=['CAPABILITY_NAMED_IAM']
        )
        
        stack_id = response['StackId']
        logger.info(f"Initiated update for CloudFormation stack: {stack_id}")
        
        # Wait for stack update to complete
        waiter = cloudformation_client.get_waiter('stack_update_complete')
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={
                'Delay': 30,
                'MaxAttempts': 60  # Wait up to 30 minutes
            }
        )
        
        # Get updated stack outputs
        stack_description = cloudformation_client.describe_stacks(StackName=stack_name)
        outputs = stack_description['Stacks'][0].get('Outputs', [])
        
        # Extract relevant outputs
        stack_outputs = {}
        for output in outputs:
            key = output['OutputKey']
            value = output['OutputValue']
            if key == 'KnowledgeBaseId':
                stack_outputs['knowledge_base_id'] = value
            elif key == 'DataSourceId':
                stack_outputs['data_source_id'] = value
            elif key == 'KnowledgeBaseArn':
                stack_outputs['knowledge_base_arn'] = value
            elif key == 'RoleArn':
                stack_outputs['role_arn'] = value
        
        # Update tenant record with any new information
        if stack_outputs:
            update_tenant_record(tenant_id, stack_outputs)
        
        logger.info(f"Successfully updated CloudFormation stack: {stack_name}")
        return True
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == 'ValidationError' and 'No updates are to be performed' in str(e):
            logger.info(f"No updates needed for stack {stack_name} - template is already current")
            return True
        else:
            logger.error(f"Error updating CloudFormation stack {stack_name}: {e}")
            raise

def update_all_tenants():
    """
    Scan tenant table and update CloudFormation stacks for all tenants
    """
    try:
        table = dynamodb.Table(table_name)
        
        # Scan the table to get all tenant records
        response = table.scan()
        tenants = response.get('Items', [])
        
        # Handle pagination if there are more items
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            tenants.extend(response.get('Items', []))
        
        logger.info(f"Found {len(tenants)} tenants to update")
        
        if not tenants:
            logger.info("No tenants found in the table")
            return {"updated_tenants": 0, "errors": [], "skipped_tenants": 0}
        
        updated_count = 0
        skipped_count = 0
        errors = []
        
        for tenant in tenants:
            tenant_id = tenant.get('tenant_id')
            stack_name = tenant.get('stack_name')
            index_name = tenant.get('index_name')
            
            if not stack_name:
                logger.warning(f"Tenant {tenant_id} has no stack_name, skipping update")
                skipped_count += 1
                continue
            
            logger.info(f"Updating tenant: {tenant_id} (stack: {stack_name})")
            
            try:
                # Check if stack exists before attempting update
                try:
                    cloudformation_client.describe_stacks(StackName=stack_name)
                except ClientError as e:
                    if 'does not exist' in str(e):
                        logger.warning(f"Stack {stack_name} for tenant {tenant_id} does not exist, skipping")
                        skipped_count += 1
                        continue
                    raise
                
                # Update the tenant's CloudFormation stack
                if update_tenant_stack(tenant_id, stack_name, index_name):
                    updated_count += 1
                    logger.info(f"Successfully updated tenant: {tenant_id}")
                else:
                    skipped_count += 1
                    logger.info(f"No updates needed for tenant: {tenant_id}")
                
            except Exception as e:
                error_msg = f"Error updating tenant {tenant_id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        return {
            "updated_tenants": updated_count,
            "total_tenants": len(tenants),
            "skipped_tenants": skipped_count,
            "errors": errors
        }
        
    except Exception as e:
        logger.error(f"Error in update_all_tenants: {e}")
        raise

def delete_all_tenants():
    """
    Scan tenant table and delete all tenant resources
    """
    try:
        table = dynamodb.Table(table_name)
        
        # Scan the table to get all tenant records
        response = table.scan()
        tenants = response.get('Items', [])
        
        # Handle pagination if there are more items
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            tenants.extend(response.get('Items', []))
        
        logger.info(f"Found {len(tenants)} tenants to delete")
        
        if not tenants:
            logger.info("No tenants found in the table")
            return {"deleted_tenants": 0, "errors": []}
        
        deleted_count = 0
        errors = []
        
        # Parse collection endpoint for hostname
        parts = parse.urlparse(collection_endpoint)
        hostname = parts.hostname
        
        for tenant in tenants:
            tenant_id = tenant.get('tenant_id')
            stack_name = tenant.get('stack_name')
            index_name = tenant.get('index_name')
            
            logger.info(f"Deleting tenant: {tenant_id}")
            
            try:
                # Delete CloudFormation stack (this will delete Bedrock KB resources)
                if stack_name:
                    delete_cloudformation_stack(stack_name)
                
                # Delete OpenSearch index
                if index_name and hostname:
                    delete_opensearch_index(hostname, index_name)
                
                # Delete S3 objects
                delete_s3_tenant_objects(tenant_id)
                
                # Delete tenant record from DynamoDB
                delete_tenant_record(tenant_id)
                
                deleted_count += 1
                logger.info(f"Successfully deleted tenant: {tenant_id}")
                
            except Exception as e:
                error_msg = f"Error deleting tenant {tenant_id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        return {
            "deleted_tenants": deleted_count,
            "total_tenants": len(tenants),
            "errors": errors
        }
        
    except Exception as e:
        logger.error(f"Error in delete_all_tenants: {e}")
        raise

def wait_for_indice_creation(host: str, index_name: str, wait_time: int = 5, max_retries: int = 60):
    """
    Wait for index creation in OpenSearch Serverless collection.
    Note: OpenSearch Serverless doesn't support cluster.health() API,
    so we use alternative methods to verify index readiness.
    """
    credentials = boto3.Session().get_credentials()
    awsauth = AWS4Auth(credentials.access_key, credentials.secret_key,
                       os.environ['AWS_REGION'], 'aoss', session_token=credentials.token)

    # Build the OpenSearch client
    client = OpenSearch(hosts=[{'host': host, 'port': 443}],
                        http_auth=awsauth,
                        use_ssl=True,
                        verify_certs=True,
                        connection_class=RequestsHttpConnection,
                        timeout=300)
    
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Check if index exists
            if not client.indices.exists(index=index_name):
                logger.warning(f"Index '{index_name}' does not exist yet. Waiting {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
                time.sleep(wait_time)
                retry_count += 1
                continue
            
            # For OpenSearch Serverless, try to get index stats to verify it's ready
            try:                
                # Try a simple search to verify the index is queryable
                search_response = client.search(
                    index=index_name,
                    body={
                        "query": {"match_all": {}},
                        "size": 0  # We don't need actual results, just want to verify it's searchable
                    }
                )
                logger.info(f"Index '{index_name}' is ready and queryable. Total docs: {search_response.get('hits', {}).get('total', {}).get('value', 0)}")
                return
                
            except Exception as search_error:
                # If search fails, the index might still be initializing
                logger.warning(f"Index '{index_name}' exists but not yet queryable: {search_error}. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
            
        except Exception as e:
            # Handle various exceptions that might occur
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ["404", "not found", "index_not_found_exception", "no such index"]):
                logger.warning(f"Index '{index_name}' not found. This is expected during creation. Waiting {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
            else:
                logger.warning(f"Error checking index '{index_name}': {e}. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
        
        time.sleep(wait_time)
        retry_count += 1
    
    # If we've exhausted all retries, log a warning but don't fail the deployment
    logger.warning(f"Index '{index_name}' verification completed after {max_retries} attempts. Proceeding with deployment.")

def is_cloudformation_event(event, context):
    """
    Check if the Lambda is invoked by CloudFormation custom resource
    Returns True if invoked by CloudFormation, False otherwise
    """
    try:
        # Check for required CloudFormation event properties
        required_cfn_keys = ['RequestType', 'ResponseURL', 'StackId', 'RequestId', 'LogicalResourceId']
        
        # Check if all required CloudFormation keys are present
        for key in required_cfn_keys:
            if key not in event:
                logger.info(f"Missing CloudFormation key: {key} - treating as direct invocation")
                return False
        
        # Check if context has required attributes for CloudFormation
        if not hasattr(context, 'log_group_name') or not hasattr(context, 'log_stream_name'):
            logger.info("Missing context attributes - treating as direct invocation")
            return False
        
        # Additional validation - RequestType should be one of the expected values
        request_type = event.get('RequestType', '')
        if request_type not in ['Create', 'Update', 'Delete']:
            logger.info(f"Invalid RequestType: {request_type} - treating as direct invocation")
            return False
        
        logger.info("Detected CloudFormation custom resource invocation")
        return True
        
    except Exception as e:
        logger.warning(f"Error checking CloudFormation event: {e} - treating as direct invocation")
        return False

def safe_cfn_response(event, context, status, response_data, physicalResourceId=None, reason=None):
    """
    Safely send CloudFormation response only if invoked by CloudFormation
    Returns response_data for direct invocations
    """
    try:
        if is_cloudformation_event(event, context):
            logger.info("Sending CloudFormation response")
            cfnresponse.send(event, context, status, response_data, 
                           physicalResourceId=physicalResourceId, reason=reason)
        else:
            logger.info("Skipping CloudFormation response - direct invocation detected")
            return response_data
    except Exception as e:
        logger.error(f"Error sending CloudFormation response: {e}")
        # For direct invocations, still return the response data
        if not is_cloudformation_event(event, context):
            return response_data
        raise

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    request_type = event.get('RequestType', 'Unknown')
    tenant_id = str(uuid.uuid4())

    try:
        if request_type == 'Create':
            # Logic for creating the custom resource
            # Example: Create an S3 bucket, call an external API, etc.
            logger.info("Handling Create event")
            # You must return a PhysicalResourceId for CloudFormation to track the resource
            index_name = f"{tenant_id}-index"
            endpoint = os.environ['COLLECTION_ENDPOINT']
            # Read the basic Collection information
            parts = parse.urlparse(endpoint)
            hostname = parts.hostname
            
            create_s3_prefixes(tenant_id)
            
            # Wait for collection to be healthy before creating indexes
            logger.info(f"Checking collection health for: {collection_name}")
            collection_endpoint_from_health = wait_for_collection_health(collection_name)
            
            # Wait for access policies to be active
            logger.info("Checking access policies status...")
            wait_for_access_policies_active(collection_name)
            
            # Additional wait to ensure everything is fully ready
            # This is critical for avoiding 404 errors on newly created collections
            logger.info("Waiting additional 60 seconds for full collection readiness and DNS propagation...")
            time.sleep(60)
            
            create_collection_index(hostname, index_name, event)
            wait_for_indice_creation(hostname, index_name, 5)

            # Deploy Bedrock Knowledge Base via CloudFormation
            kb_result = deploy_bedrock_knowledge_base_stack(
                tenant_id, 
                bucket_name, 
                bucket_kms_key_arn,
                index_name,
                collection_arn,
                collection_name
                )

            create_tenant_record(
                tenant_id,
                index_name,
                kb_result['stack_name'],
                kb_result['role_arn'],
                kb_result.get('knowledge_base_id'),
                kb_result.get('knowledge_base_arn'),
                kb_result.get('data_source_id'))

            response_data = {
                "Message": "Custom resource created successfully!",
                "tenant_id": tenant_id,
                "index_name": index_name,
                "endpoint": endpoint,
                "tenant_table": os.environ['TENANT_TABLE'],
                "knowledge_base_arn": kb_result.get('knowledge_base_arn'),
                "data_source_id": kb_result.get('data_source_id'),
                "stack_name": kb_result['stack_name']
            }
            return safe_cfn_response(event, context, cfnresponse.SUCCESS, response_data, physicalResourceId="all-tenants")

        elif request_type == 'Update':
            # Logic for updating all tenant CloudFormation stacks
            logger.info("Handling Update event - updating all tenant stacks")
            
            try:
                update_result = update_all_tenants()
                
                response_data = {
                    "Message": "Tenant update completed",
                    "updated_tenants": update_result["updated_tenants"],
                    "total_tenants": update_result["total_tenants"],
                    "skipped_tenants": update_result["skipped_tenants"],
                    "errors": update_result["errors"]
                }
                
                # Report success even if some updates failed - errors are included for visibility
                return safe_cfn_response(event, context, cfnresponse.SUCCESS, response_data, 
                                       physicalResourceId="all-tenents")
                
            except Exception as update_error:
                logger.error(f"Critical error during tenant update: {update_error}")
                return safe_cfn_response(event, context, cfnresponse.FAILED, 
                                       {"Message": f"Failed to update tenants: {str(update_error)}"}, 
                                       physicalResourceId="all-tenants",
                                       reason=str(update_error))

        elif request_type == 'Delete':
            # Logic for deleting all tenant resources
            logger.info("Handling Delete event - deleting all tenants")
            
            try:
                deletion_result = delete_all_tenants()
                
                response_data = {
                    "Message": "Tenant deletion completed",
                    "deleted_tenants": deletion_result["deleted_tenants"],
                    "total_tenants": deletion_result["total_tenants"],
                    "errors": deletion_result["errors"]
                }
                
                # If there were errors but some tenants were deleted, still report success
                # The errors are included in the response for visibility
                return safe_cfn_response(event, context, cfnresponse.SUCCESS, response_data, 
                                       physicalResourceId=event.get('PhysicalResourceId', 'tenant-provisioner'))
                
            except Exception as delete_error:
                logger.error(f"Critical error during tenant deletion: {delete_error}")
                return safe_cfn_response(event, context, cfnresponse.FAILED, 
                                       {"Message": f"Failed to delete tenants: {str(delete_error)}"}, 
                                       physicalResourceId=event.get('PhysicalResourceId', 'tenant-provisioner'),
                                       reason=str(delete_error))

        else:
            logger.info(f"Nothing to run for RequestType: {request_type}")
            response_data = {"Message": f"Nothing to do for RequestType: {request_type}"}
            return safe_cfn_response(event, context, cfnresponse.SUCCESS, response_data, 
                                   physicalResourceId=tenant_id, reason="Nothing to do for this RequestType")

    except Exception as e:
        logger.error(f"Error processing custom resource event: {e}")
        error_response = {"Message": f"Error processing event: {str(e)}"}
        return safe_cfn_response(event, context, cfnresponse.FAILED, error_response, 
                               physicalResourceId=tenant_id, reason=str(e))