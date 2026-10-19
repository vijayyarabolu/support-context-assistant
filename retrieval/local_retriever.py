"""
Offline runbook retrieval: chunking plus BM25 with an exact error-code boost.

This is the demo-mode replacement for OpenSearch Serverless. It keeps the same
idea as the AWS query (keyword relevance over title and content, no fuzzy
matching so short error codes stay precise) and adds an exact-match boost when
a query contains an error code that a runbook lists in its front matter.

Three chunking strategies are available so eval/run_eval.py can compare them
on the same golden set.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "data" / "runbooks"

ERROR_CODE_RE = re.compile(r"\b[A-Z]{2,6}-(?:\d{3,4}|DLQ)\b")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does", "for", "from",
    "get", "getting", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "keep",
    "me", "my", "of", "on", "or", "our", "should", "so", "that", "the", "their", "them", "there",
    "they", "this", "to", "too", "up", "us", "was", "we", "what", "when", "where", "which", "why",
    "will", "with", "you", "your", "all", "any", "not", "no", "after", "again", "being", "some",
}

CHUNKING_STRATEGIES = {
    # name: (chunk_words, overlap_words); None means split on markdown sections
    "fixed-200/50": (200, 50),
    "fixed-60/30": (60, 30),
    "section": None,
}
DEFAULT_STRATEGY = "section"


# Support tickets and runbooks describe the same problem in different words.
# A small, explicit expansion list keeps BM25 honest without an embedding model.
QUERY_EXPANSIONS = {
    "timing out": "timeout timeouts",
    "timed out": "timeout timeouts",
    "slow": "timeout latency",
    "throttled": "rate limit 429",
    "throttling": "rate limit 429",
    "too many requests": "rate limit 429",
    "log in": "login sign",
    "logged out": "token expired 401",
    "sign in": "login sign",
    "duplicates": "duplicate",
    "certificate": "certificate tls",
    "cert": "certificate tls",
    "webhook": "webhook webhooks",
    "unpaid": "unpaid invoices payment",
    "stuck": "stuck dead letter queue",
    "deadlock": "lock wait",
    "locked": "lock wait",
}


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> List[str]:
    return [_stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def expand_query(query: str) -> str:
    lowered = query.lower()
    extra = [terms for phrase, terms in QUERY_EXPANSIONS.items() if phrase in lowered]
    return query + (" " + " ".join(extra) if extra else "")


def extract_error_codes(text: str) -> List[str]:
    seen = []
    for code in ERROR_CODE_RE.findall(text.upper()):
        if code not in seen:
            seen.append(code)
    return seen


@dataclass
class Runbook:
    id: str
    title: str
    error_codes: List[str]
    category: str
    severity: str
    body: str


@dataclass
class Chunk:
    runbook_id: str
    title: str
    text: str
    error_codes: List[str]
    tokens: List[str] = field(default_factory=list)


def load_runbooks(directory: Path = RUNBOOK_DIR) -> List[Runbook]:
    books = []
    for path in sorted(Path(directory).glob("*.md")):
        raw = path.read_text()
        _, front, body = raw.split("---", 2)
        meta = {}
        for line in front.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        codes = [c.strip() for c in meta["error_codes"].strip("[]").split(",") if c.strip()]
        books.append(Runbook(meta["id"], meta["title"], codes, meta["category"], meta["severity"], body.strip()))
    return books


def chunk_runbook(book: Runbook, strategy: str) -> List[Chunk]:
    spec = CHUNKING_STRATEGIES[strategy]
    if spec is None:
        parts = [p.strip() for p in re.split(r"\n(?=## )", book.body) if p.strip()]
        texts = [f"{book.title}\n{p}" for p in parts]
    else:
        size, overlap = spec
        words = book.body.split()
        step = max(1, size - overlap)
        texts = []
        for start in range(0, len(words), step):
            texts.append(f"{book.title}\n" + " ".join(words[start:start + size]))
            if start + size >= len(words):
                break
    return [Chunk(book.id, book.title, t, book.error_codes, tokenize(t)) for t in texts]


class LocalRunbookRetriever:
    def __init__(self, strategy: str = DEFAULT_STRATEGY, k1: float = 1.4, b: float = 0.75,
                 code_boost: float = 6.0, runbook_dir: Path = RUNBOOK_DIR):
        self.strategy = strategy
        self.k1, self.b, self.code_boost = k1, b, code_boost
        self.runbooks: Dict[str, Runbook] = {rb.id: rb for rb in load_runbooks(runbook_dir)}
        self.chunks: List[Chunk] = [c for rb in self.runbooks.values() for c in chunk_runbook(rb, strategy)]
        self._df = Counter(tok for c in self.chunks for tok in set(c.tokens))
        self._avg_len = sum(len(c.tokens) for c in self.chunks) / len(self.chunks)

    def _bm25(self, query_tokens: List[str], chunk: Chunk) -> float:
        tf = Counter(chunk.tokens)
        n = len(self.chunks)
        score = 0.0
        for tok in query_tokens:
            if tok not in tf:
                continue
            idf = math.log(1 + (n - self._df[tok] + 0.5) / (self._df[tok] + 0.5))
            freq = tf[tok]
            denom = freq + self.k1 * (1 - self.b + self.b * len(chunk.tokens) / self._avg_len)
            score += idf * freq * (self.k1 + 1) / denom
        return score

    def search(self, query: str, top_k: int = 3, error_codes: Optional[List[str]] = None) -> List[Dict]:
        """Return the best chunk per runbook, ranked, so top_k counts distinct runbooks."""
        codes = set(error_codes or extract_error_codes(query))
        q_tokens = tokenize(expand_query(query))
        best: Dict[str, Dict] = {}
        for chunk in self.chunks:
            score = self._bm25(q_tokens, chunk)
            if codes & set(chunk.error_codes):
                score += self.code_boost
            if score <= 0:
                continue
            current = best.get(chunk.runbook_id)
            if current is None or score > current["score"]:
                best[chunk.runbook_id] = {"runbook_id": chunk.runbook_id, "title": chunk.title,
                                          "text": chunk.text, "score": round(score, 3)}
        ranked = sorted(best.values(), key=lambda r: r["score"], reverse=True)
        return ranked[:top_k]

    def runbook(self, runbook_id: str) -> Runbook:
        return self.runbooks[runbook_id]
