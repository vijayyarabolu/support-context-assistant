"""
Local stand-ins for CloudWatch Logs and DynamoDB.

The AWS deployment queried CloudWatch Logs Insights and two DynamoDB tables.
Those services are off now (free-tier credits ran out), so demo mode reads the
same shapes of data from JSON fixtures in data/. Every read takes the
authenticated tenant_id and filters on it before anything is returned, which
mirrors how the AWS tools scope their queries.
"""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Fixtures were generated relative to this moment, so "last 24 hours" is stable.
FIXTURE_NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class LocalStore:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = Path(data_dir)
        self._tenants = json.loads((self.data_dir / "tenants.json").read_text())
        with open(self.data_dir / "logs.jsonl") as handle:
            self._logs = [json.loads(line) for line in handle if line.strip()]

    def known_tenants(self) -> List[str]:
        return sorted(self._tenants)

    def query_logs(
        self,
        tenant_id: str,
        error_codes: Optional[List[str]] = None,
        hours_back: int = 24,
        limit: int = 50,
    ) -> Dict[str, Any]:
        if tenant_id not in self._tenants:
            return {"tool": "query_logs", "tenant_id": tenant_id, "status": "access_denied",
                    "message": "Unknown tenant"}

        cutoff = FIXTURE_NOW - timedelta(hours=hours_back)
        wanted = {code.upper() for code in error_codes} if error_codes else None

        rows = [
            row for row in self._logs
            if row["tenant_id"] == tenant_id
            and row["level"] == "ERROR"
            and _parse_ts(row["timestamp"]) >= cutoff
            and (wanted is None or row["error_code"] in wanted)
        ]
        rows.sort(key=lambda r: r["timestamp"], reverse=True)

        by_code = Counter(row["error_code"] for row in rows)
        sources = {}
        for row in rows:
            sources.setdefault(row["error_code"], Counter())[row["source"]] += 1

        summary = [
            {"error_code": code, "count": count, "top_source": sources[code].most_common(1)[0][0],
             "sample_message": next(r["message"] for r in rows if r["error_code"] == code)}
            for code, count in by_code.most_common()
        ]

        return {
            "tool": "query_logs",
            "tenant_id": tenant_id,
            "status": "ok",
            "hours_searched": hours_back,
            "total_errors": len(rows),
            "summary": summary,
            "results": rows[:limit],
        }

    def lookup_crm(self, tenant_id: str) -> Dict[str, Any]:
        record = self._tenants.get(tenant_id)
        if not record:
            return {"tool": "lookup_crm", "tenant_id": tenant_id, "status": "not_found"}
        fields = {k: v for k, v in record.items() if k != "state"}
        return {"tool": "lookup_crm", "tenant_id": tenant_id, "status": "found", **fields}

    def fetch_state(self, tenant_id: str) -> Dict[str, Any]:
        record = self._tenants.get(tenant_id)
        if not record:
            return {"tool": "fetch_state", "tenant_id": tenant_id, "status": "not_found"}
        return {"tool": "fetch_state", "tenant_id": tenant_id, "status": "found", "record": record["state"]}
