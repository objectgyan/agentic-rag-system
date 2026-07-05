"""CLI: run the eval harness against a live tenant.

    python -m app.services.rag.evaluation datasets/example.json \
        --tenant <TENANT_UUID> --user <USER_UUID> [--top-k 5] [--model gpt-4o-mini] \
        [--no-judge] [--out report.json] [--min-recall 0.6] [--min-faithfulness 0.7]

Requires a running DB and a tenant that already has ingested documents. It opens its own
tenant-scoped session (sets the RLS GUC) rather than going through the HTTP layer, so it can be
run from a shell or in CI against a seeded database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.services.rag.evaluation.dataset import load_golden_dataset
from app.services.rag.evaluation.runner import EvalReport, EvaluationRunner


def _check_thresholds(report: EvalReport, args: argparse.Namespace) -> list[str]:
    """Return a list of threshold-failure messages (empty = all gates pass)."""
    failures: list[str] = []
    if args.min_recall is not None:
        recall_keys = [k for k in report.retrieval_means if k.startswith("recall@")]
        for k in recall_keys:
            if report.retrieval_means[k] < args.min_recall:
                failures.append(f"{k}={report.retrieval_means[k]:.3f} < min {args.min_recall}")
    if args.min_faithfulness is not None:
        val = report.generation_means.get("faithfulness")
        if val is not None and val < args.min_faithfulness:
            failures.append(f"faithfulness={val:.3f} < min {args.min_faithfulness}")
    return failures


async def _run(args: argparse.Namespace) -> int:
    cases = load_golden_dataset(args.dataset)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, args.tenant)
        runner = EvaluationRunner(
            db=session,
            tenant_id=args.tenant,
            user_id=args.user,
            top_k=args.top_k,
            model=args.model,
            use_reranking=not args.no_reranking,
            judge_answers=not args.no_judge,
        )
        report = await runner.run(cases)

    print(report.summary())

    if args.out:
        Path(args.out).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"\nWrote full report to {args.out}")

    failures = _check_thresholds(report, args)
    if failures:
        print("\nTHRESHOLD FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Run the RAG evaluation harness against a live tenant.")
    p.add_argument("dataset", help="Path to a golden dataset JSON file")
    p.add_argument("--tenant", required=True, help="Tenant UUID (must have ingested documents)")
    p.add_argument("--user", required=True, help="User UUID (for usage records)")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--model", default=None, help="LLM model (defaults to config default)")
    p.add_argument("--no-reranking", action="store_true", help="Disable cross-encoder re-ranking")
    p.add_argument("--no-judge", action="store_true", help="Skip the LLM-as-judge generation metrics")
    p.add_argument("--out", default=None, help="Write the full JSON report here")
    p.add_argument("--min-recall", type=float, default=None, help="Fail if recall@k below this")
    p.add_argument("--min-faithfulness", type=float, default=None, help="Fail if faithfulness below this")
    args = p.parse_args()

    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
