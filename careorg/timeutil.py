"""日期、月份与区间的解析工具。

有效区间一律左闭右开：[start, end)。空结束值表示“尚无结束记录”，
不据此推断未来；只在指定观察日上判断是否生效。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .errors import SourceError


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str) or not value:
        raise SourceError("date_invalid", f"{field} 必须是 ISO 8601 日期", field=field, value=value)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SourceError("date_invalid", f"{field} 不是有效日期: {value}", field=field) from exc


def parse_optional_date(value: object, field: str) -> date | None:
    if value is None or value == "":
        return None
    return parse_date(value, field)


def parse_instant(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceError("instant_invalid", f"{field} 必须是带时区的 ISO 8601 时间", field=field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SourceError("instant_invalid", f"{field} 不是有效时间: {value}", field=field) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceError("instant_naive", f"{field} 必须附带明确时区: {value}", field=field)
    return parsed


def parse_month(value: str) -> tuple[date, date]:
    """返回月份首日与该月最后一日（观察日按月末固定）。"""
    parts = value.split("-")
    if len(parts) != 2:
        raise SourceError("month_invalid", f"月份格式应为 YYYY-MM: {value}", value=value)
    try:
        year, month = int(parts[0]), int(parts[1])
        start = date(year, month, 1)
    except ValueError as exc:
        raise SourceError("month_invalid", f"月份格式应为 YYYY-MM: {value}", value=value) from exc
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def month_of(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"
