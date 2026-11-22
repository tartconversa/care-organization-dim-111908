"""命令行入口：inspect / link / publish / history / withdraw / compare / details。

    python -m careorg inspect
    python -m careorg link --perspective responsibility --mode restated \\
        --observation-date 2026-12-01 --as-of 2026-12-05

输出统一为 JSON，UTF-8。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .errors import CareOrgError
from .warehouse import Warehouse


def _tz() -> timezone:
    return timezone.utc


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _parse_instant(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        from .errors import CareOrgError

        raise CareOrgError("instant_naive", "--as-of 必须附带明确时区")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="careorg", description="机构维度装载与多视角查询")
    parser.add_argument("--root", default=".", help="资料根目录（包含 data/）")
    parser.add_argument("--store", default=None, help="发布版本存储目录")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="资料根目录")
    common.add_argument("--store", default=argparse.SUPPRESS, help="发布版本存储目录")

    sub.add_parser("inspect", parents=[common], help="维度接收与校验报告")

    link = sub.add_parser("link", parents=[common], help="按视角与口径关联事实")
    link.add_argument("--perspective", required=True, choices=("legal", "responsibility", "finance"))
    link.add_argument("--mode", default="as_recorded", choices=("as_recorded", "restated"))
    link.add_argument("--observation-date", default=None)
    link.add_argument("--as-of", default=None)
    link.add_argument("--month", default=None)
    link.add_argument("--out", default=None, help="写出明细 JSON 的路径")

    details = sub.add_parser("details", parents=[common], help="明细查询")
    details.add_argument("--perspective", required=True, choices=("legal", "responsibility", "finance"))
    details.add_argument("--mode", default="as_recorded", choices=("as_recorded", "restated"))
    details.add_argument("--observation-date", default=None)
    details.add_argument("--as-of", default=None)
    details.add_argument("--month", default=None)
    details.add_argument("--org", default=None)
    details.add_argument("--source-org", default=None)
    details.add_argument("--site", default=None)
    details.add_argument("--service", default=None)
    details.add_argument("--level", default="unit", choices=("unit", "source", "site", "village"))
    details.add_argument("--no-unmapped", action="store_true")

    publish = sub.add_parser("publish", parents=[common], help="发布口径版本")
    publish.add_argument("--key", required=True)
    publish.add_argument("--label", required=True)
    publish.add_argument("--perspective", required=True, choices=("legal", "responsibility", "finance"))
    publish.add_argument("--mode", default="as_recorded", choices=("as_recorded", "restated"))
    publish.add_argument("--observation-date", default=None)
    publish.add_argument("--as-of", required=True)
    publish.add_argument("--month", default=None)
    publish.add_argument("--org-relationship", default=None,
                         choices=("legal", "responsibility", "finance"))

    hist = sub.add_parser("history", parents=[common], help="版本历史")
    hist.add_argument("--family", default=None)

    withdraw = sub.add_parser("withdraw", parents=[common], help="撤回组织资料")
    withdraw.add_argument("--withdrawal-id", required=True)
    withdraw.add_argument("--notice", action="append", default=[])
    withdraw.add_argument("--handover", action="append", default=[])
    withdraw.add_argument("--org", action="append", default=[])
    withdraw.add_argument("--effective-at", required=True)
    withdraw.add_argument("--reason", required=True)

    compare = sub.add_parser("compare", parents=[common], help="跨视角对照")
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)
    compare.add_argument("--org", action="append", default=[])

    return parser


def _warehouse(args) -> Warehouse:
    store = Path(args.store) if args.store else None
    return Warehouse(args.root, store_dir=store)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            print(json.dumps(_warehouse(args).inspect(), ensure_ascii=False, indent=2))
            return 0
        if args.command in ("link", "details"):
            warehouse = _warehouse(args)
            observation = _parse_date(args.observation_date)
            as_of = _parse_date(args.as_of)
            if args.command == "link":
                result = warehouse.link(args.perspective, args.mode, observation, as_of)
                payload = result.to_dict()
                if args.out:
                    payload = {
                        **payload,
                        "rows": [row.to_dict() for row in result.allocations],
                        "conservation": warehouse.linker.check_conservation(result),
                    }
                    Path(args.out).write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            query = warehouse.details(
                perspective=args.perspective,
                mode=args.mode,
                observation_date=observation,
                as_of=as_of,
                org_id=args.org,
                source_org=args.source_org,
                site_id=args.site,
                service=args.service,
                month=args.month,
                include_unmapped=not args.no_unmapped,
            )
            print(json.dumps(query.summary(level=args.level), ensure_ascii=False, indent=2))
            return 0
        if args.command == "publish":
            warehouse = _warehouse(args)
            observation = _parse_date(args.observation_date)
            as_of_date = _parse_instant(args.as_of).date()
            result = warehouse.link(args.perspective, args.mode, observation, as_of_date)
            publication = warehouse.publish(
                args.key,
                result,
                label=args.label,
                as_of=_parse_instant(args.as_of),
                org_relationship=args.org_relationship,
                month=args.month,
            )
            print(json.dumps(publication.meta(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "history":
            warehouse = _warehouse(args)
            print(json.dumps(warehouse.publisher.history(args.family), ensure_ascii=False, indent=2))
            return 0
        if args.command == "withdraw":
            warehouse = _warehouse(args)
            result = warehouse.publisher.withdraw(
                withdrawal_id=args.withdrawal_id,
                notice_ids=tuple(args.notice),
                handover_ids=tuple(args.handover),
                org_ids=tuple(args.org),
                effective_at=_parse_instant(args.effective_at),
                reason=args.reason,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "compare":
            warehouse = _warehouse(args)
            left = warehouse.publisher.pinned(args.left)
            right = warehouse.publisher.pinned(args.right)
            orgs = set(args.org) if args.org else None
            print(
                json.dumps(
                    warehouse.query.compare(left, right, org_ids=orgs),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    except CareOrgError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, KeyError) as exc:
        print(
            json.dumps({"code": "argument_invalid", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
