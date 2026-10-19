import base64
import json
import sys
import time
import types
import unittest


class InvalidTokenError(Exception):
    pass


class ExpiredSignatureError(InvalidTokenError):
    pass


class InvalidAudienceError(InvalidTokenError):
    pass


class DecodeError(InvalidTokenError):
    pass


def _b64_json(data):
    raw = json.dumps(data).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("utf-8")


def make_token(payload, header=None):
    header = header or {"alg": "HS256"}
    return f"{_b64_json(header)}.{_b64_json(payload)}.signature"


def _decode_segment(segment):
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def fake_get_unverified_header(token):
    try:
        return _decode_segment(token.split(".")[0])
    except Exception as exc:
        raise DecodeError(str(exc))


def fake_decode(token, key=None, algorithms=None, audience=None, issuer=None, options=None):
    try:
        payload = _decode_segment(token.split(".")[1])
    except Exception as exc:
        raise DecodeError(str(exc))

    verify_exp = (options or {}).get("verify_exp", True)
    if verify_exp and payload.get("exp") and payload["exp"] < int(time.time()):
        raise ExpiredSignatureError("Signature has expired")

    return payload


fake_jwt = types.ModuleType("jwt")
fake_jwt.decode = fake_decode
fake_jwt.get_unverified_header = fake_get_unverified_header
fake_jwt.ExpiredSignatureError = ExpiredSignatureError
fake_jwt.InvalidAudienceError = InvalidAudienceError
fake_jwt.InvalidTokenError = InvalidTokenError
fake_jwt.DecodeError = DecodeError

fake_algorithms = types.ModuleType("jwt.algorithms")
fake_algorithms.RSAAlgorithm = types.SimpleNamespace(from_jwk=lambda key: f"public:{key}")
fake_jwt.algorithms = fake_algorithms

sys.modules.setdefault("jwt", fake_jwt)
sys.modules.setdefault("jwt.algorithms", fake_algorithms)
sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *args, **kwargs: None))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault("botocore", types.ModuleType("botocore"))
sys.modules.setdefault("botocore.config", types.SimpleNamespace(Config=lambda **kwargs: kwargs))
sys.modules.setdefault("botocore.exceptions", types.SimpleNamespace(ClientError=Exception))
sys.modules.setdefault("opensearchpy", types.SimpleNamespace(OpenSearch=object, RequestsHttpConnection=object))
sys.modules.setdefault("requests_aws4auth", types.SimpleNamespace(AWS4Auth=object))

import config
from agent import tools
from agent.bedrock_agent import SupportAgent
from auth.cognito_auth import CognitoAuthMiddleware
from observability.cloudwatch_logger import AgentTraceLogger
from retrieval.rag_pipeline import RunbookRetriever


class FakeBedrockClient:
    def __init__(self):
        self.calls = []

    def invoke_agent(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "completion": [
                {"chunk": {"bytes": b"tenant-safe answer"}}
            ]
        }


class FakeCloudWatchClient:
    def __init__(self):
        self.put_calls = []

    def put_log_events(self, **kwargs):
        self.put_calls.append(kwargs)
        return {"nextSequenceToken": "deprecated-token"}


class FakeOpenSearchClient:
    def __init__(self):
        self.search_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {"hits": {"hits": []}}


class CognitoAuthTest(unittest.TestCase):
    def setUp(self):
        self.original_demo_mode = config.DEMO_MODE

    def tearDown(self):
        config.DEMO_MODE = self.original_demo_mode

    def test_unverified_demo_token_is_explicitly_gated(self):
        token = make_token({"custom:tenant_id": "T-402", "username": "ops"})
        auth = CognitoAuthMiddleware()
        auth._jwks_keys = []

        config.DEMO_MODE = True
        demo_result = auth.validate_token(token)
        self.assertTrue(demo_result["is_valid"])
        self.assertEqual(demo_result["auth_mode"], "demo_unverified")

        config.DEMO_MODE = False
        prod_result = auth.validate_token(token)
        self.assertFalse(prod_result["is_valid"])

    def test_expired_demo_token_is_not_authenticated(self):
        token = make_token({"custom:tenant_id": "T-402", "exp": int(time.time()) - 60})
        auth = CognitoAuthMiddleware()
        auth._jwks_keys = []
        config.DEMO_MODE = True

        result = auth.validate_token(token)

        self.assertFalse(result["is_valid"])
        self.assertEqual(result["error"], "Demo token has expired")


class BedrockTenantContextTest(unittest.TestCase):
    def test_streaming_injects_same_tenant_context_as_standard_invoke(self):
        agent = SupportAgent.__new__(SupportAgent)
        agent.client = FakeBedrockClient()
        agent.agent_id = "agent"
        agent.agent_alias_id = "alias"

        self.assertEqual(
            list(agent.invoke_streaming("check errors", session_id="s1", tenant_id="T-402")),
            ["tenant-safe answer"]
        )

        call = agent.client.calls[0]
        self.assertEqual(call["inputText"], "[Tenant Context: T-402] check errors")


class CloudWatchLoggerTest(unittest.TestCase):
    def test_put_log_event_never_sends_sequence_token(self):
        logger = AgentTraceLogger.__new__(AgentTraceLogger)
        logger.logs_client = FakeCloudWatchClient()
        logger.log_group = "/aws/test"
        logger.log_stream_name = "stream"

        logger._put_log_event({"event_type": "test", "session_id": "s1"})

        self.assertNotIn("sequenceToken", logger.logs_client.put_calls[0])


class RagQueryTest(unittest.TestCase):
    def test_runbook_search_query_omits_fuzziness_for_precision(self):
        fake_client = FakeOpenSearchClient()
        retriever = RunbookRetriever.__new__(RunbookRetriever)
        retriever.index = "support-runbooks"
        retriever._get_client = lambda: fake_client

        retriever.search("ERR-504", top_k=3)

        multi_match = fake_client.search_calls[0]["body"]["query"]["multi_match"]
        self.assertEqual(multi_match["type"], "best_fields")
        self.assertNotIn("fuzziness", multi_match)


class TenantToolIsolationTest(unittest.TestCase):
    def test_cloudwatch_tool_rejects_unscoped_queries(self):
        result = tools.query_cloudwatch_logs(
            log_group=config.TENANT_LOG_GROUP,
            query="fields @timestamp, @message | filter level = 'ERROR'",
            tenant_id="T-402",
        )

        self.assertEqual(result["status"], "access_denied")

    def test_dynamo_tool_rejects_unapproved_table(self):
        result = tools.fetch_dynamo_record(
            table_name="global-admin-table",
            key_dict={"tenant_id": "T-402"},
        )

        self.assertEqual(result["status"], "access_denied")


if __name__ == "__main__":
    unittest.main()
