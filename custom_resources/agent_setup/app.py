import boto3
import json
import logging
import cfnresponse
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def prepare_agent_and_get_version(bedrock_client, agent_id, agent_name):
    """
    Prepare the agent (create a numbered version from DRAFT) and return the version number.
    """
    logger.info(f"Preparing agent {agent_name} (ID: {agent_id}) to create a numbered version")
    
    try:
        response = bedrock_client.prepare_agent(agentId=agent_id)
        agent_status = response.get('agentStatus')
        agent_version = response.get('agentVersion', 'DRAFT')
        
        logger.info(f"PrepareAgent called for {agent_name}. Status: {agent_status}, Version: {agent_version}")
        return agent_version
        
    except Exception as e:
        logger.error(f"Error preparing agent {agent_name}: {e}")
        raise

def wait_for_agent_version_ready(bedrock_client, agent_id, agent_name, max_retries=30, wait_time=10):
    """
    Wait for a specific agent version to be in PREPARED state.
    Returns True if the version is ready, False otherwise.
    """
    logger.info(f"Waiting for agent {agent_name} to be PREPARED")
    
    for attempt in range(max_retries):
        try:
            response = bedrock_client.get_agent(agentId=agent_id)
            status = response.get('agent', {}).get('agentStatus')
            
            if status == 'PREPARED':
                logger.info(f"Agent {agent_name} is PREPARED and ready")
                return True
            elif status in ['CREATING', 'UPDATING', 'PREPARING']:
                logger.info(f"Agent {agent_name} status: {status}. Waiting {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
            elif status == 'FAILED':
                logger.error(f"Agent {agent_name} is in FAILED state")
                return False
            else:
                logger.warning(f"Agent {agent_name} has unexpected status: {status}")
            
        except bedrock_client.exceptions.ResourceNotFoundException:
            logger.info(f"{agent_name}, waiting... (attempt {attempt + 1}/{max_retries})")
        except Exception as e:
            logger.error(f"Error checking agent {agent_name} status: {e}")
            if attempt == max_retries - 1:
                return False
        
        time.sleep(wait_time)
    
    logger.warning(f"Agent {agent_name} readiness check timed out after {max_retries} attempts")
    return False

def create_or_update_agent_alias(bedrock_client, agent_id, alias_name, agent_name):
    """
    Create an agent alias with routing to specified version.
    If aliases exist, appends version number to name to avoid conflicts.
    Returns tuple of (alias_id, actual_version, alias_arn).
    """
    logger.info(f"Managing alias '{alias_name}' for agent {agent_name} (ID: {agent_id})")
    
    try:
        # List existing aliases to determine naming
        response = bedrock_client.list_agent_aliases(agentId=agent_id, maxResults=100)
        aliases = response.get('agentAliasSummaries', [])
        logger.info(f"Found {len(aliases)} existing aliases for {agent_name}")
        
        # If aliases exist, append version number to avoid conflicts
        number_of_aliases = len(aliases)
        if number_of_aliases > 0:
            alias_name = f"{alias_name}_v{number_of_aliases}"
            logger.info(f"Aliases exist, using name: {alias_name}")
        
        # Create new alias
        logger.info(f"Creating new alias '{alias_name}' ...")
        create_response = bedrock_client.create_agent_alias(
            agentId=agent_id,
            agentAliasName=alias_name
        )
        
        alias_id = create_response['agentAlias']['agentAliasId']
        alias_arn = create_response['agentAlias']['agentAliasArn']
        routing = create_response.get('agentAlias', {}).get('routingConfiguration', [])
        logger.info(f"Successfully created alias '{alias_name}' with ID: {alias_id} and routing: {routing}")
        
        # Extract actual version from routing configuration
        version = routing[0].get('agentVersion', 'DRAFT') if len(routing) > 0 else 'DRAFT'
        
        return (alias_id, version, alias_arn)
            
    except Exception as e:
        logger.error(f"Error managing alias '{alias_name}' for agent {agent_name}: {e}")
        raise

def wait_for_agent_alias_ready(bedrock_client, agent_id, alias_id, agent_name, max_retries=20, wait_time=5):
    logger.info(f"Checking readiness for agent alias {agent_name} (Agent ID: {agent_id}, Alias ID: {alias_id})")
    
    for attempt in range(max_retries):
        try:
            response = bedrock_client.get_agent_alias(agentId=agent_id, agentAliasId=alias_id)
            alias_status = response.get('agentAlias', {}).get('agentAliasStatus')
            
            if alias_status == 'PREPARED':
                logger.info(f"Agent alias {agent_name} is PREPARED and ready")
                return True
            elif alias_status in ['CREATING', 'UPDATING', 'PREPARING', 'DISABLED']:
                logger.info(f"Agent alias {agent_name} status: {alias_status}. Waiting {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
            elif alias_status == 'FAILED':
                logger.error(f"Agent alias {agent_name} is in FAILED state")
                return False
            else:
                logger.warning(f"Agent alias {agent_name} has unexpected status: {alias_status}")
            
        except Exception as e:
            logger.error(f"Error checking agent alias {agent_name} status: {e}")
            if attempt == max_retries - 1:
                return False
        
        time.sleep(wait_time)
    
    logger.warning(f"Agent alias {agent_name} readiness check timed out after {max_retries} attempts")
    return False

def delete_agent_alias(bedrock_client, agent_id, alias_id, agent_name):
    """
    Delete an agent alias if it exists.
    """
    try:
        logger.info(f"Deleting alias {alias_id} for agent {agent_name} (ID: {agent_id})")
        bedrock_client.delete_agent_alias(
            agentId=agent_id,
            agentAliasId=alias_id
        )
        logger.info(f"Successfully deleted alias {alias_id}")
        return True
    except bedrock_client.exceptions.ResourceNotFoundException:
        logger.info(f"Alias {alias_id} not found, skipping deletion")
        return True
    except Exception as e:
        logger.error(f"Error deleting alias {alias_id}: {e}")
        return False

def handler(event, context):
    try:
        logger.info(f"Lambda function ARN: {context.invoked_function_arn}")
        logger.info(f"AWS Region: {boto3.Session().region_name}")
        
        bedrock = boto3.client('bedrock-agent')
        request_type = event['RequestType']
        
        # Get resource properties for single agent
        agent_id = event['ResourceProperties']['AgentId']
        agent_version = event['ResourceProperties']['AgentVersion']
        alias_name = event['ResourceProperties']['AliasName']
        agent_name = event['ResourceProperties'].get('AgentName', 'Agent')
        
        response_data = {}
        physical_resource_id = event.get('PhysicalResourceId', f"agent-alias-{agent_id}")
        
        if request_type == 'Create' or request_type == 'Update':
            logger.info(f"Managing alias for {agent_name} (ID: {agent_id})")
            logger.info(f"Received version from stack: {agent_version}")
            
            # Step 1: Prepare agent
            logger.info(f"Step 1: Preparing {agent_name}")
            prepare_agent_and_get_version(bedrock, agent_id, agent_name)
            wait_for_agent_version_ready(bedrock, agent_id, agent_name)

            # Step 2: Create alias
            logger.info(f"Step 2: Creating/updating alias for {agent_name}")
            
            alias_id, version, alias_arn = create_or_update_agent_alias(
                bedrock, agent_id, alias_name, agent_name
            )
            response_data['AliasId'] = alias_id
            response_data['AliasArn'] = alias_arn
            response_data['AgentVersion'] = agent_version
            
            # Step 3: Wait for alias to be ready
            logger.info(f"Step 3: Waiting for {agent_name} alias to be ready")
            
            if not wait_for_agent_alias_ready(bedrock, agent_id, alias_id, agent_name):
                raise Exception(f"{agent_name} alias is not ready")
            
            logger.info(f"Successfully configured {agent_name} with alias")
        
        elif request_type == 'Delete':
            logger.info(f"Cleaning up {agent_name} aliases...")
            
            # Delete all aliases from agent
            try:
                logger.info(f"Listing aliases for {agent_name} (ID: {agent_id})")
                aliases_response = bedrock.list_agent_aliases(
                    agentId=agent_id,
                    maxResults=100
                )
                aliases = aliases_response.get('agentAliasSummaries', [])
                logger.info(f"Found {len(aliases)} aliases for {agent_name}")
                
                for alias in aliases:
                    alias_id = alias.get('agentAliasId')
                    alias_name_to_delete = alias.get('agentAliasName')
                    try:
                        logger.info(f"Deleting {agent_name} alias: {alias_name_to_delete} (ID: {alias_id})")
                        delete_agent_alias(bedrock, agent_id, alias_id, agent_name)
                        logger.info(f"Successfully deleted {agent_name} alias: {alias_name_to_delete}")
                    except Exception as e:
                        logger.error(f"Failed to delete {agent_name} alias {alias_name_to_delete}: {e}")
                        response_data['AliasDeleteError'] = str(e)
                        
            except Exception as e:
                logger.error(f"Failed to list/delete {agent_name} aliases: {e}")
                response_data['CleanupError'] = str(e)
            
            logger.info(f"Completed cleanup of {agent_name} aliases")
        
        cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data, physical_resource_id)
        
    except Exception as e:
        logger.error(f"Error in agent alias setup: {e}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)}, event.get('PhysicalResourceId', 'failed'))
