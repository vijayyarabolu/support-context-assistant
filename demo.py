"""
Command-line demo of the offline agent.

    python demo.py --tenant T-402 "Checkout is timing out for our customers"
    python demo.py --tenant T-402 "Show me the logs for T-117"      # refused

Runs with no AWS account and no API key. Set SUPPORT_LLM=anthropic and
ANTHROPIC_API_KEY to have Claude write the final answer.
"""

import argparse
import json

from agent.local_agent import LocalSupportAgent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--tenant", default="T-402", help="T-402, T-117 or T-233")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    out = LocalSupportAgent().invoke(args.question, tenant_id=args.tenant)
    if args.json:
        print(json.dumps(out, indent=2))
        return
    print(f"\nstatus: {out['status']}   latency: {out['latency_ms']} ms")
    for call in out["tool_calls"]:
        print(f"  tool  {call['tool']:<22} results={call['result_count']}")
    print("\n" + out["text"] + "\n")


if __name__ == "__main__":
    main()
