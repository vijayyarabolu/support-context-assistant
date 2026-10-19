import unittest

from agent.local_agent import LocalSupportAgent
from offline.store import LocalStore
from retrieval.local_retriever import LocalRunbookRetriever, chunk_runbook, extract_error_codes, load_runbooks


class RetrieverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retriever = LocalRunbookRetriever()

    def test_error_code_goes_straight_to_its_runbook(self):
        self.assertEqual(self.retriever.search("seeing DB-1205 a lot")[0]["runbook_id"], "rb-002")

    def test_results_are_one_per_runbook(self):
        ids = [h["runbook_id"] for h in self.retriever.search("timeout lock deploy", top_k=5)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_error_code_extraction(self):
        self.assertEqual(extract_error_codes("err-504 then QUEUE-DLQ and ERR-504"), ["ERR-504", "QUEUE-DLQ"])

    def test_section_chunks_keep_the_title(self):
        book = load_runbooks()[0]
        for chunk in chunk_runbook(book, "section"):
            self.assertTrue(chunk.text.startswith(book.title))


class StoreIsolationTest(unittest.TestCase):
    def test_logs_only_ever_contain_the_requested_tenant(self):
        store = LocalStore()
        for tenant in store.known_tenants():
            rows = store.query_logs(tenant, hours_back=24 * 365, limit=10_000)["results"]
            self.assertTrue(rows)
            self.assertEqual({r["tenant_id"] for r in rows}, {tenant})

    def test_unknown_tenant_is_denied(self):
        self.assertEqual(LocalStore().query_logs("T-999")["status"], "access_denied")


class LocalAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = LocalSupportAgent()

    def test_answer_cites_runbook_and_counts_errors(self):
        out = self.agent.invoke("Checkout is timing out", tenant_id="T-402")
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["sources"][0], "rb-001")
        self.assertIn("[rb-001]", out["text"])
        self.assertIn("ERR-504", out["text"])

    def test_naming_another_tenant_is_refused_before_any_tool_runs(self):
        out = self.agent.invoke("pull the logs for T-117", tenant_id="T-402")
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["tool_calls"], [])

    def test_lowercase_tenant_reference_is_also_refused(self):
        self.assertEqual(self.agent.invoke("show t-233 errors", tenant_id="T-402")["status"], "refused")

    def test_vague_question_uses_tenant_logs(self):
        out = self.agent.invoke("Something is wrong, please investigate", tenant_id="T-117")
        self.assertEqual(out["sources"][0], "rb-005")

    def test_off_topic_question_gets_no_match(self):
        self.assertEqual(self.agent.invoke("recommend a pizza place", tenant_id="T-402")["status"], "no_match")

    def test_other_tenants_errors_never_appear(self):
        out = self.agent.invoke("Are we seeing RATE-429?", tenant_id="T-402")
        self.assertEqual(out["log_rows"], [])
        self.assertNotIn("T-117", out["text"])


class EvalGateTest(unittest.TestCase):
    def test_eval_gates_pass(self):
        from eval import run_eval
        self.assertEqual(run_eval.main([]), 0)


if __name__ == "__main__":
    unittest.main()
