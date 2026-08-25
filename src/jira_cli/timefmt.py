"""时间戳格式化。

Jira 返回的是 `2026-08-25T11:31:57.000+0800`：带 T 分隔符、带时区偏移，
且偏移不带冒号（Python 3.10 的 fromisoformat 不认这种写法）。

对外统一输出 `2026-08-25 11:31:57.000`，并换算到配置的时区（默认东八区）。
毫秒按源数据实际有什么显示什么——源里没有小数部分就不输出，不补 `.000`。

时区是**进程级设置**：一次调用只连一个实例、只有一份配置，
让 fields.py 里每个输出构造函数都多带一个 tz 参数不值得。
由 cli/common.py 的 Ctx 在启动时调 set_timezone() 配置一次。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

#: 北京时间。中国自 1991 年起无夏令时，固定偏移即可，不必依赖 tzdata
DEFAULT_TZ = "+08:00"

_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"[T ]"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<frac>\.\d+)?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)

_target_tz: timezone = timezone(timedelta(hours=8))


def parse_timezone(spec: str) -> Optional[timezone]:
    """把 '+08:00' / '+0800' / 'Asia/Shanghai' 解析成 tzinfo。识别不了返回 None。"""
    spec = (spec or "").strip()
    if not spec:
        return None
    matched = _OFFSET_RE.match(spec)
    if matched:
        sign = -1 if matched.group(1) == "-" else 1
        delta = timedelta(hours=int(matched.group(2)), minutes=int(matched.group(3)))
        return timezone(sign * delta)
    try:  # IANA 名称，需要系统 tzdata
        from zoneinfo import ZoneInfo

        return ZoneInfo(spec)  # type: ignore[return-value]
    except Exception:
        return None


def set_timezone(spec: str) -> bool:
    """设置输出时区，返回是否识别成功。

    识别不了时保持原设置并返回 False，由调用方决定怎么提示——静默回退
    会让 `JIRA_TZ` 拼错（Asia/Shangai）表现成「配置生效了但时间不对」。
    """
    tz = parse_timezone(spec)
    if tz is None:
        return not (spec or "").strip()
    global _target_tz
    _target_tz = tz  # type: ignore[assignment]
    return True


def format_ts(value: Any) -> Any:
    """Jira 时间戳 -> `2026-08-25 11:31:57.000`（换算到配置时区）。

    不是时间戳的值原样返回——日期型字段（如 duedate 的 `2026-09-01`）
    和任意文本都不该被这里改写。
    """
    if not isinstance(value, str):
        return value
    matched = _TS_RE.match(value.strip())
    if not matched:
        return value

    raw_frac = matched.group("frac")
    # 源里有小数部分才输出，统一到毫秒精度；没有就不补
    frac = f".{raw_frac[1:].ljust(3, '0')[:3]}" if raw_frac else ""
    raw_tz = matched.group("tz")
    stamp = f"{matched.group('date')} {matched.group('time')}{frac}"

    if not raw_tz:
        # 没有偏移信息就无从换算，按原样呈现
        return stamp

    tz = timezone.utc if raw_tz == "Z" else parse_timezone(raw_tz)
    if tz is None:
        return stamp
    pattern = "%Y-%m-%d %H:%M:%S.%f" if frac else "%Y-%m-%d %H:%M:%S"
    try:
        moment = datetime.strptime(stamp, pattern).replace(tzinfo=tz)
    except ValueError:
        return stamp
    converted = moment.astimezone(_target_tz)
    if frac:
        return converted.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return converted.strftime("%Y-%m-%d %H:%M:%S")
