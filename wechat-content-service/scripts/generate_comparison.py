import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the same WeChat article with three text models."
    )
    parser.add_argument(
        "--topic",
        default="过了55岁，晚饭后做哪些小事更有利于睡眠",
    )
    parser.add_argument("--category", default="睡眠养生")
    parser.add_argument("--audience", default="senior-wellness")
    parser.add_argument(
        "--headline-intensity",
        type=int,
        choices=range(1, 6),
        default=4,
    )
    parser.add_argument(
        "--no-humanize",
        action="store_true",
        help="Skip the Humanizer-zh editing pass.",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    response = httpx.post(
        f"{args.api_base.rstrip('/')}/api/articles/compare",
        json={
            "topic": args.topic,
            "category": args.category,
            "audience_slug": args.audience,
            "title_count": 5,
            "image_count": 3,
            "headline_intensity": args.headline_intensity,
            "humanize": not args.no_humanize,
            "providers": ["deepseek", "minimax", "kimi"],
        },
        timeout=900,
    )
    response.raise_for_status()
    result = response.json()

    output_dir = Path("comparison-results")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / (
        f"comparison-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Result saved to {output_path.resolve()}")
    for article in result["articles"]:
        print(
            f"- {article['llm_provider']}: {article['selected_title']} "
            f"({args.api_base.rstrip('/')}/admin/articles/{article['id']})"
        )
    for error in result["errors"]:
        print(f"- {error['provider']} failed: {error['error']}")

    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
