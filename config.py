"""
Centralized configuration for the User-Context Specific Support Assistant.

All AWS resource names, ARNs, region settings, and environment variables
are managed here. This ensures consistent configuration across all modules
and simplifies deployment to different environments.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()


# =============================================================================
# AWS General Configuration
# =============================================================================
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}

# =============================================================================
# AWS Bedrock AgentCore Configuration
# =============================================================================
# Bedrock AgentCore agent ID — created via AWS Console or CloudFormation
BEDROCK_AGENT_ID = os.getenv("BEDROCK_AGENT_ID")
# Bedrock AgentCore agent alias ID — points to the deployed version
BEDROCK_AGENT_ALIAS_ID = os.getenv("BEDROCK_AGENT_ALIAS_ID")

# =============================================================================
# AWS OpenSearch Serverless Configuration
# =============================================================================
# Fully qualified endpoint URL for the OpenSearch Serverless collection
OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT")
# Index name for support runbooks and error catalogs
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "support-runbooks")
# OpenSearch Serverless service identifier for SigV4 signing
OPENSEARCH_SERVICE = "aoss"

# =============================================================================
# AWS Cognito Configuration
# =============================================================================
# Cognito User Pool ID for JWT validation
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
# Cognito App Client ID for token verification
COGNITO_APP_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID")
# JWKS URL constructed from the User Pool ID for signature verification
COGNITO_JWKS_URL = (
    f"https://cognito-idp.{AWS_REGION}.amazonaws.com/"
    f"{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    if COGNITO_USER_POOL_ID else None
)

# =============================================================================
# AWS CloudWatch Configuration
# =============================================================================
# CloudWatch log group for agent observability and end-to-end tracing
CLOUDWATCH_LOG_GROUP = os.getenv("CLOUDWATCH_LOG_GROUP", "/aws/bedrock/support-agent-traces")
# Log group for querying tenant application logs (MCP tool)
TENANT_LOG_GROUP = os.getenv("TENANT_LOG_GROUP", "/aws/application/tenant-logs")

# =============================================================================
# AWS DynamoDB Configuration
# =============================================================================
# Table for tenant CRM records
DYNAMODB_CRM_TABLE = os.getenv("DYNAMODB_CRM_TABLE", "support-crm-records")
# Table for tenant state records
DYNAMODB_STATE_TABLE = os.getenv("DYNAMODB_STATE_TABLE", "support-tenant-state")

# =============================================================================
# RAG Configuration
# =============================================================================
# Optimized chunking parameters (Variant 3 — winner after 3 iterations)
# See retrieval/rag_pipeline.py for the full evaluation of all 3 variants
RAG_CHUNK_SIZE = 512
RAG_CHUNK_OVERLAP = 50  # Reduced from 128 → 50 for 25% token savings

# =============================================================================
# Application Settings
# =============================================================================
APP_TITLE = "Support Context Assistant"
APP_PAGE_ICON = "🛠️"


def get_aws_session_config():
    """
    Returns a dictionary of AWS session configuration parameters.
    Used by boto3 clients across the application for consistent credentials.
    """
    config_values = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        config_values.update({
            "aws_access_key_id": AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
        })
    return config_values


def validate_config():
    """
    Validates that all required environment variables are set.
    Called at application startup to fail fast if configuration is incomplete.

    Returns:
        dict: Validation results with missing variables listed
    """
    required_vars = {
        "BEDROCK_AGENT_ID": BEDROCK_AGENT_ID,
        "BEDROCK_AGENT_ALIAS_ID": BEDROCK_AGENT_ALIAS_ID,
        "OPENSEARCH_ENDPOINT": OPENSEARCH_ENDPOINT,
        "COGNITO_USER_POOL_ID": COGNITO_USER_POOL_ID,
        "COGNITO_APP_CLIENT_ID": COGNITO_APP_CLIENT_ID,
    }

    missing = [key for key, value in required_vars.items() if not value]

    if DEMO_MODE:
        return {
            "valid": True,
            "missing": missing,
            "message": "DEMO_MODE=true; AWS configuration is optional for local demo"
        }

    return {
        "valid": len(missing) == 0,
        "missing": missing,
        "message": f"Missing required config: {', '.join(missing)}" if missing else "All config valid"
    }
