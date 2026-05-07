
import boto3
from botocore.exceptions import ClientError
import json
import logging
import os
from aws_cdk import (
    Aws
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def get_inference_profile(awsprofile: str, inference_profile_arn: str):
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
        session = boto3.Session(profile_name=awsprofile) if awsprofile else boto3.Session()
        logger.info(f"Looking up inference profile: {profile_id}")
        bedrock_client = session.client('bedrock')
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
                'models': [],
                'destination_regions': []
            }
        else:
            logger.error(f"Error looking up inference profile {inference_profile_arn}: {e}")
            raise
    
    except Exception as e:
        logger.error(f"Unexpected error looking up inference profile {inference_profile_arn}: {e}")
        raise

def get_prefix_list_id(service_name: str, awsprofile: str = None) -> str:
    """Look up AWS managed prefix list ID for a service"""
    session = boto3.Session(profile_name=awsprofile) if awsprofile else boto3.Session()
    ec2 = session.client('ec2')
    
    
    response = ec2.describe_prefix_lists(
        Filters=[
            {
                'Name': 'prefix-list-name',
                'Values': [f'com.amazonaws.{session.region_name}.{service_name}']
            }
        ]
    )
    
    if response['PrefixLists']:
        return response['PrefixLists'][0]['PrefixListId']
    else:
        raise ValueError(f"No prefix list found for service {service_name} in region {region}")
    