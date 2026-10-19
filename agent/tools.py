"""
MCP Tool Definitions for Bedrock AgentCore Support Agent.

These functions serve as the MCP (Model Context Protocol) tools that
Bedrock AgentCore routes to when it needs to fetch external data.
Each tool makes real boto3 calls to the corresponding AWS service.

Tools:
- search_knowledge_base: RAG search over OpenSearch Serverless runbooks
- query_cloudwatch_logs: CloudWatch Logs Insights query for tenant errors
- lookup_crm: DynamoDB lookup for tenant CRM profile
- fetch_dynamo_record: DynamoDB get_item for tenant state records
"""

import re
import time
from typing import Dict, Any

import boto3

import config


TENANT_ID_PATTERN = re.compile(r"T-\d{3}")


def scope_insights_query(query: str, tenant_id: str) -> str:
    """Append a tenant filter that the caller cannot remove."""
    if not TENANT_ID_PATTERN.fullmatch(tenant_id or ""):
        raise ValueError("invalid tenant_id")
    return f"{query.strip()} | filter tenant_id = '{tenant_id}'"


ALLOWED_DYNAMODB_TABLES = {
    config.DYNAMODB_CRM_TABLE,
    config.DYNAMODB_STATE_TABLE,
}

def _tenant_scope_error(tool_name: str, message: str, tenant_id: str = "") -> Dict[str, Any]:
    return {
        "tool": tool_name,
        "tenant_id": tenant_id,
        "status": "access_denied",
        "message": message,
    }


# =============================================================================
# Tool: Knowledge Base Search (OpenSearch Serverless)
# =============================================================================

def search_knowledge_base(query: str, tenant_id: str, top_k: int = 3) -> Dict[str, Any]:
    """
    Search the OpenSearch Serverless runbook knowledge base.

    Performs a text-based search over indexed runbooks and error catalogs
    to find relevant troubleshooting steps for the support query.

    Args:
        query: Natural language search query from the agent
        tenant_id: Tenant identifier for access scoping
        top_k: Number of results to return

    Returns:
        Dict with search results and metadata
    """
    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth

    # Get AWS credentials for SigV4 signing
    credentials = boto3.Session(**config.get_aws_session_config()).get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials unavailable for OpenSearch SigV4 signing")

    auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        config.AWS_REGION,
        config.OPENSEARCH_SERVICE,
        session_token=credentials.token,
    )

    # Connect to OpenSearch Serverless with SigV4 authentication
    if not config.OPENSEARCH_ENDPOINT:
        raise ValueError("OPENSEARCH_ENDPOINT is not configured")
    host = config.OPENSEARCH_ENDPOINT.replace("https://", "")
    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )

    # Execute a multi-match query across title and content fields
    search_body = {
        "size": top_k,
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "content", "error_codes"],
                "type": "best_fields",
            }
        },
        "_source": ["title", "content", "category", "severity"],
    }

    response = client.search(
        index=config.OPENSEARCH_INDEX,
        body=search_body,
    )

    hits = response.get("hits", {}).get("hits", [])
    results = []
    for hit in hits:
        source = hit.get("_source", {})
        results.append({
            "title": source.get("title", ""),
            "content": source.get("content", ""),
            "category": source.get("category", ""),
            "score": hit.get("_score", 0),
        })

    return {
        "tool": "search_knowledge_base",
        "tenant_id": tenant_id,
        "query": query,
        "results": results,
        "result_count": len(results),
    }


# =============================================================================
# Tool: CloudWatch Logs Query
# =============================================================================

def query_cloudwatch_logs(
    log_group: str,
    query: str,
    tenant_id: str,
    hours_back: int = 24,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Query CloudWatch Logs Insights for tenant error patterns.

    Uses the CloudWatch Logs Insights query language to search for
    specific error patterns, exceptions, or status codes in tenant logs.

    Args:
        log_group: CloudWatch log group name to query
        query: CloudWatch Insights query string
        tenant_id: Authenticated tenant identifier
        hours_back: How far back to search (in hours)
        limit: Maximum number of log events to return

    Returns:
        Dict with query results and statistics
    """
    if log_group != config.TENANT_LOG_GROUP:
        return _tenant_scope_error(
            "query_cloudwatch_logs",
            f"Log group {log_group} is not approved for tenant log access",
            tenant_id,
        )

    if not tenant_id or not TENANT_ID_PATTERN.fullmatch(tenant_id):
        return _tenant_scope_error(
            "query_cloudwatch_logs",
            "A valid authenticated tenant_id is required",
            tenant_id,
        )

    # The tenant filter is added here, after whatever the model wrote, instead
    # of trusting the model to include it. Insights applies piped filters in
    # order, so a query like "filter tenant_id != 'T-402'" still ends up
    # restricted to the caller's tenant.
    query = scope_insights_query(query, tenant_id)

    aws_config = config.get_aws_session_config()

    # Create CloudWatch Logs client
    logs_client = boto3.client(
        "logs",
        **aws_config,
    )

    # Calculate time range for the query
    end_time = int(time.time())
    start_time = end_time - (hours_back * 3600)

    # Start the CloudWatch Logs Insights query
    # This is an async operation — we start it and then poll for results
    start_response = logs_client.start_query(
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        queryString=query,
        limit=limit,
    )

    query_id = start_response["queryId"]

    # Poll for query completion (CloudWatch Insights queries are async)
    # In production, this would use exponential backoff
    results = None
    for _ in range(10):  # Max 10 polling attempts
        time.sleep(1)  # Wait 1 second between polls

        result_response = logs_client.get_query_results(queryId=query_id)
        status = result_response.get("status", "")

        if status == "Complete":
            results = result_response.get("results", [])
            break
        elif status in ("Failed", "Cancelled"):
            return {
                "tool": "query_cloudwatch_logs",
                "tenant_id": tenant_id,
                "status": "error",
                "message": f"Query {status.lower()}",
            }

    if results is None:
        return {
            "tool": "query_cloudwatch_logs",
            "tenant_id": tenant_id,
            "status": "timeout",
            "message": "Query did not complete within polling window",
        }

    # Format results for agent consumption
    formatted_results = []
    for result in results:
        entry = {}
        for field in result:
            entry[field["field"]] = field["value"]
        formatted_results.append(entry)

    return {
        "tool": "query_cloudwatch_logs",
        "tenant_id": tenant_id,
        "log_group": log_group,
        "hours_searched": hours_back,
        "results": formatted_results,
        "result_count": len(formatted_results),
        "statistics": result_response.get("statistics", {}),
    }


# =============================================================================
# Tool: CRM Lookup (DynamoDB)
# =============================================================================

def lookup_crm(tenant_id: str) -> Dict[str, Any]:
    """
    Look up tenant CRM profile from DynamoDB.

    Retrieves the tenant's subscription tier, support level, contract dates,
    and account manager details. This context helps the agent prioritize
    and route support requests appropriately.

    Args:
        tenant_id: Unique tenant identifier

    Returns:
        Dict with tenant CRM profile data
    """
    aws_config = config.get_aws_session_config()

    # Create DynamoDB resource for high-level table operations
    dynamodb = boto3.resource(
        "dynamodb",
        **aws_config,
    )

    table = dynamodb.Table(config.DYNAMODB_CRM_TABLE)

    # get_item retrieves a single item by its primary key
    response = table.get_item(
        Key={"tenant_id": tenant_id}
    )

    item = response.get("Item")

    if not item:
        return {
            "tool": "lookup_crm",
            "tenant_id": tenant_id,
            "status": "not_found",
            "message": f"No CRM record found for tenant {tenant_id}",
        }

    return {
        "tool": "lookup_crm",
        "tenant_id": tenant_id,
        "plan": item.get("plan", "Unknown"),
        "support_tier": item.get("support_tier", "Standard"),
        "account_manager": item.get("account_manager", "Unassigned"),
        "contract_start": item.get("contract_start", ""),
        "contract_end": item.get("contract_end", ""),
        "monthly_spend": item.get("monthly_spend", 0),
        "status": "found",
    }


# =============================================================================
# Tool: DynamoDB Record Fetch
# =============================================================================

def fetch_dynamo_record(table_name: str, key_dict: dict, authenticated_tenant_id: str = "") -> Dict[str, Any]:
    """
    Fetch a specific record from a DynamoDB table.

    General-purpose DynamoDB lookup tool that the agent uses when it needs
    to retrieve tenant state, configuration, or historical records.

    Args:
        table_name: DynamoDB table name to query
        key_dict: Primary key dictionary (e.g., {"tenant_id": "T-001"})
        authenticated_tenant_id: Tenant from the verified JWT. When set, the
            lookup key must match it, so the model cannot ask for another
            tenant's record by changing the key.

    Returns:
        Dict with the retrieved record or error status
    """
    requested_tenant_id = key_dict.get("tenant_id", "")
    if table_name not in ALLOWED_DYNAMODB_TABLES:
        return _tenant_scope_error(
            "fetch_dynamo_record",
            f"Table {table_name} is not in the tenant-scoped allowlist",
            requested_tenant_id,
        )

    if not requested_tenant_id:
        return _tenant_scope_error(
            "fetch_dynamo_record",
            "DynamoDB lookup key must include tenant_id",
            requested_tenant_id,
        )

    if authenticated_tenant_id and requested_tenant_id != authenticated_tenant_id:
        return _tenant_scope_error(
            "fetch_dynamo_record",
            "Lookup key tenant_id does not match the authenticated tenant",
            requested_tenant_id,
        )

    aws_config = config.get_aws_session_config()

    # Create DynamoDB resource
    dynamodb = boto3.resource(
        "dynamodb",
        **aws_config,
    )

    table = dynamodb.Table(table_name)

    # get_item retrieves the item matching the provided key
    response = table.get_item(Key=key_dict)

    item = response.get("Item")

    if not item:
        return {
            "tool": "fetch_dynamo_record",
            "table": table_name,
            "tenant_id": requested_tenant_id,
            "status": "not_found",
            "message": f"No record found with key {key_dict}",
        }

    # Convert any Decimal types to float for JSON serialization
    def convert_decimals(obj):
        from decimal import Decimal
        if isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: convert_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_decimals(i) for i in obj]
        return obj

    return {
        "tool": "fetch_dynamo_record",
        "table": table_name,
        "tenant_id": requested_tenant_id,
        "record": convert_decimals(item),
        "status": "found",
    }


# =============================================================================
# Tool Schema Definitions (for AgentCore registration)
# =============================================================================

TOOL_SCHEMAS = {
    "search_knowledge_base": {
        "name": "search_knowledge_base",
        "description": (
            "Search the internal runbook and error catalog knowledge base "
            "for troubleshooting steps related to the support query."
        ),
        "parameters": {
            "query": {"type": "string", "description": "Search query", "required": True},
            "tenant_id": {"type": "string", "description": "Tenant identifier", "required": True},
            "top_k": {"type": "integer", "description": "Number of results", "required": False},
        },
    },
    "query_cloudwatch_logs": {
        "name": "query_cloudwatch_logs",
        "description": (
            "Query CloudWatch Logs Insights for recent error patterns and exceptions "
            "in the tenant's application logs."
        ),
        "parameters": {
            "log_group": {"type": "string", "description": "CloudWatch log group name", "required": True},
            "query": {"type": "string", "description": "Insights query string", "required": True},
            "tenant_id": {"type": "string", "description": "Authenticated tenant identifier", "required": True},
            "hours_back": {"type": "integer", "description": "Hours to search back", "required": False},
        },
    },
    "lookup_crm": {
        "name": "lookup_crm",
        "description": "Look up tenant CRM profile including subscription tier and support level.",
        "parameters": {
            "tenant_id": {"type": "string", "description": "Tenant identifier", "required": True},
        },
    },
    "fetch_dynamo_record": {
        "name": "fetch_dynamo_record",
        "description": "Fetch a specific record from a DynamoDB table by primary key.",
        "parameters": {
            "table_name": {"type": "string", "description": "DynamoDB table name", "required": True},
            "key_dict": {"type": "object", "description": "Primary key dictionary", "required": True},
        },
    },
}


def get_tool_definitions() -> Dict[str, Dict]:
    """Return all tool schemas for AgentCore registration."""
    return TOOL_SCHEMAS
