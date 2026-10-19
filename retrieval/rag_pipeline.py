"""
RAG Pipeline over AWS OpenSearch Serverless for Support Runbooks.

Retrieves troubleshooting runbooks and error catalogs using dense vector
search with SigV4 authentication against OpenSearch Serverless.

Chunk size and overlap were tuned while the AWS index was live (it settled on
512-token chunks with 50 tokens of overlap). That index is gone now, so the
chunking comparison that can be rerun today lives in eval/run_eval.py and runs
against the offline retriever in retrieval/local_retriever.py.
"""

import json
from typing import List, Dict, Any, Optional

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

import config


class RunbookRetriever:
    """
    Manages retrieval of support runbooks from AWS OpenSearch Serverless.

    Uses SigV4 authentication for secure access to the serverless collection.
    Supports both dense vector search and keyword search modes.
    """

    def __init__(self):
        """
        Initialize the OpenSearch Serverless connection with SigV4 auth.

        OpenSearch Serverless requires AWS Signature Version 4 for all
        HTTP requests, unlike managed OpenSearch which supports basic auth.
        """
        aws_config = config.get_aws_session_config()

        # Get credentials for SigV4 signing
        session = boto3.Session(**aws_config)
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials unavailable for OpenSearch SigV4 signing")

        # AWS4Auth handles SigV4 request signing for OpenSearch Serverless
        # Service name is 'aoss' (Amazon OpenSearch Serverless), not 'es'
        self.auth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            config.AWS_REGION,
            config.OPENSEARCH_SERVICE,  # 'aoss' for serverless
            session_token=credentials.token,
        )

        self.index = config.OPENSEARCH_INDEX

    def _get_client(self) -> OpenSearch:
        """
        Create an OpenSearch client connected to the serverless endpoint.

        Returns:
            Configured OpenSearch client with SigV4 auth
        """
        if not config.OPENSEARCH_ENDPOINT:
            raise ValueError("OPENSEARCH_ENDPOINT is not configured")
        host = config.OPENSEARCH_ENDPOINT.replace("https://", "")

        # Connect via HTTPS with SigV4 authentication
        return OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=self.auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Execute a search query over the runbooks index.

        Uses OpenSearch's multi-match query to search across title, content,
        and error_code fields. The Variant 3 chunking configuration ensures
        each result maps to one complete runbook section.

        Args:
            query: User's support query or error description
            top_k: Number of results to return

        Returns:
            List of matching runbook chunks with titles and content
        """
        print(f"[RAG] Searching runbooks for: {query}")

        client = self._get_client()

        # Multi-match query searches across multiple fields with boosting.
        # Fuzziness is intentionally omitted: support runbook retrieval often
        # includes short error codes where fuzzy expansion hurts precision.
        search_body = {
            "size": top_k,
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["title^2", "content", "error_codes", "category"],
                    "type": "best_fields",
                }
            },
            "_source": ["title", "content", "category", "severity", "resolution_steps"],
        }

        # Execute the search against OpenSearch Serverless
        response = client.search(
            index=self.index,
            body=search_body,
        )

        hits = response.get("hits", {}).get("hits", [])
        results = []

        for hit in hits:
            source = hit.get("_source", {})
            results.append({
                "title": source.get("title", "Unknown Runbook"),
                "content": source.get("content", ""),
                "category": source.get("category", "General"),
                "severity": source.get("severity", "Medium"),
                "resolution_steps": source.get("resolution_steps", ""),
                "score": hit.get("_score", 0),
            })

        print(f"[RAG] Found {len(results)} relevant runbooks")
        return results

    def search_by_error_code(self, error_code: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Search runbooks by specific error code.

        Optimized for exact error code matching which is common in
        support ticket triage workflows.

        Args:
            error_code: Error code to search for (e.g., 'ERR-504', 'TIMEOUT')
            top_k: Number of results to return

        Returns:
            List of matching runbook chunks
        """
        print(f"[RAG] Searching by error code: {error_code}")

        client = self._get_client()

        # Use a term query for exact error code matching combined
        # with a should clause for fuzzy content matching
        search_body = {
            "size": top_k,
            "query": {
                "bool": {
                    "should": [
                        {"term": {"error_codes": error_code}},
                        {"match": {"content": error_code}},
                        {"match": {"title": error_code}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        }

        response = client.search(
            index=self.index,
            body=search_body,
        )

        hits = response.get("hits", {}).get("hits", [])
        return [
            {
                "title": hit["_source"].get("title", ""),
                "content": hit["_source"].get("content", ""),
                "score": hit.get("_score", 0),
            }
            for hit in hits
        ]

    def get_context_string(self, query: str, top_k: int = 3) -> str:
        """
        Format retrieved runbooks into a single context string for the agent.

        Args:
            query: Search query
            top_k: Number of results to include

        Returns:
            Formatted context string with numbered sources
        """
        results = self.search(query, top_k)

        if not results:
            return "No relevant runbooks found for this query."

        context_parts = []
        for i, result in enumerate(results):
            context_parts.append(
                f"[Runbook {i + 1}: {result['title']}]\n"
                f"Category: {result['category']} | Severity: {result['severity']}\n"
                f"{result['content']}\n"
            )

        return "\n".join(context_parts)
