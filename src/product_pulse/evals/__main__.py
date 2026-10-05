"""Run the eval suite: `python -m product_pulse.evals` (or `pulse-evals`).

Loads the eval fixture into its own project (once), connects an MCP client to the
server in-process and asks every question through the chosen model client. With an
API key in PULSE_EVAL_API_KEY or OPENAI_API_KEY it uses the OpenAI-compatible client,
otherwise the offline planner. Exits 1 when accuracy is below --threshold.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pulse-evals")
    parser.add_argument("--model", choices=["auto", "offline", "openai"], default="auto")
    parser.add_argument("--threshold", type=float, default=0.0)
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args(argv)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "product_pulse.settings")
    os.environ.setdefault("PULSE_LOG_LEVEL", "WARNING")
    import anyio
    import django

    django.setup()
    from product_pulse.clickhouse.client import default_client
    from product_pulse.evals.harness import ModelClient, load_questions
    from product_pulse.evals.offline import OfflinePlanner
    from product_pulse.evals.openai_client import OpenAICompatibleClient, OpenAIConfig
    from product_pulse.evals.runner import ensure_eval_project, run

    config = OpenAIConfig.from_env()
    if args.model == "openai" and config is None:
        parser.error("--model openai needs PULSE_EVAL_API_KEY or OPENAI_API_KEY")
    model: ModelClient
    if config is not None and args.model != "offline":
        model = OpenAICompatibleClient(config)
    else:
        model = OfflinePlanner()

    questions = load_questions()
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q.id in wanted]

    clickhouse = default_client()
    project_id = ensure_eval_project(clickhouse)
    report = anyio.run(run, project_id, clickhouse, model, questions)
    print(report.table())
    if args.json:
        args.json.write_text(json.dumps(report.as_dict(), indent=2))
    sys.exit(0 if report.accuracy >= args.threshold else 1)


if __name__ == "__main__":
    main()
