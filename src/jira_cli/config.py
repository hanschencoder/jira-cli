"""配置加载与保存。

优先级：命令行参数 > 环境变量 > 配置文件。
不支持多 profile；临时换实例用全局 --url / --token 覆盖。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields as dc_fields
from pathlib import Path

import platformdirs
import tomli_w

from .errors import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 依赖 Python 版本
    import tomli as tomllib

APP_NAME = "jira-cli"
CONFIG_FILENAME = "config.toml"
WRITE_LOG_FILENAME = "write-log.jsonl"

#: 配置键 -> 环境变量名
ENV_OVERRIDES = {
    "url": "JIRA_URL",
    "token": "JIRA_TOKEN",
    "auth_type": "JIRA_AUTH_TYPE",
    "default_project": "JIRA_PROJECT",
    "timezone": "JIRA_TZ",
    "download_dir": "JIRA_DOWNLOAD_DIR",
}


def config_dir() -> Path:
    """配置目录。JIRA_CLI_CONFIG_DIR 覆盖平台默认值（测试隔离用）。"""
    override = os.environ.get("JIRA_CLI_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME))


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def cache_dir() -> Path:
    """缓存目录。JIRA_CLI_CACHE_DIR 覆盖平台默认值。"""
    override = os.environ.get("JIRA_CLI_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_cache_dir(APP_NAME))


def default_download_dir() -> Path:
    """附件默认落点的根目录，实际文件放在 <root>/<ISSUE-KEY>/ 下。

    放缓存目录而不是当前工作目录：附件是可重新拉取的派生数据，
    落在固定位置才能跨次复用，也不会撒进用户的代码仓库。
    """
    return cache_dir() / "attachments"


def write_log_path() -> Path:
    return config_dir() / WRITE_LOG_FILENAME


@dataclass
class Config:
    """一次调用最终生效的配置。"""

    url: str = ""
    token: str = ""
    #: bearer（PAT，Server 8.14+ 推荐）或 basic（用户名:密码）
    auth_type: str = "bearer"
    #: 用于 basic 认证；bearer 时忽略
    login: str = ""
    #: server / cloud，由 config init 探测。当前只实现 server
    deployment: str = "server"
    #: wiki / plain，description 字段的渲染器，由 config init 探测
    renderer: str = "wiki"
    #: 默认项目，省去每条命令都敲 --project
    default_project: str = ""
    #: 时间戳输出时换算到的时区。支持 +08:00 / +0800 / Asia/Shanghai
    timezone: str = "+08:00"
    #: 附件默认落点根目录。留空则用 default_download_dir()
    download_dir: str = ""
    #: 跳过 TLS 校验
    insecure: bool = False

    def require_connection(self) -> None:
        """在发起任何请求前调用。缺 url/token 时给出可照抄的修复命令。"""
        missing = [name for name in ("url", "token") if not getattr(self, name)]
        if not missing:
            return
        raise ConfigError(
            f"缺少配置：{', '.join(missing)}",
            "请先配置连接（三选一）：\n"
            "  1. 交互式引导：jira-cli config init\n"
            "  2. 手动写入：   jira-cli config set url https://jira.example.com\n"
            "                  jira-cli config set token <你的-PAT>\n"
            "  3. 环境变量：   export JIRA_URL=... JIRA_TOKEN=...\n\n"
            "PAT 在 Jira 页面右上角头像 →「个人设置」→「Personal Access Tokens」创建。",
        )

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")

    def masked(self) -> dict[str, object]:
        """给 config get 用：token 脱敏。"""
        data = {f.name: getattr(self, f.name) for f in dc_fields(self)}
        token = data.get("token") or ""
        if token:
            data["token"] = f"{token[:4]}…{token[-4:]}" if len(token) > 12 else "…"
        return data


_FIELD_NAMES = {f.name for f in dc_fields(Config)}
_BOOL_FIELDS = {f.name for f in dc_fields(Config) if f.type is bool or f.type == "bool"}


def load_file() -> dict[str, object]:
    """读配置文件。不存在返回空 dict；损坏则报错并指出路径。"""
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:
        raise ConfigError(
            f"配置文件解析失败：{path}",
            f"错误：{exc}\n删除该文件后重新运行 jira-cli config init 即可重建。",
        ) from exc


def save_file(data: dict[str, object]) -> Path:
    """写配置文件，权限 600（含 PAT）。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump({k: v for k, v in data.items() if v not in ("", None)}, fh)
    path.chmod(0o600)
    return path


def load(**overrides: object) -> Config:
    """按 命令行 > 环境变量 > 文件 的优先级合并出最终配置。

    overrides 里值为 None 的键视作「命令行未提供」，不参与覆盖。
    """
    data: dict[str, object] = {}

    for key, value in load_file().items():
        # 配置文件里习惯写 default-project，映射到字段名
        key = key.replace("-", "_")
        if key in _FIELD_NAMES:
            data[key] = value

    for key, env_name in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value:
            data[key] = value

    for key, value in overrides.items():
        if value is not None:
            data[key] = value

    for key in _BOOL_FIELDS:
        if key in data and isinstance(data[key], str):
            data[key] = data[key].strip().lower() in ("1", "true", "yes", "on")

    return Config(**data)  # type: ignore[arg-type]


def normalize_key(key: str) -> str:
    """把 config set 的键名（api-key 风格）规范成字段名。"""
    normalized = key.strip().replace("-", "_").lower()
    if normalized not in _FIELD_NAMES:
        known = ", ".join(sorted(n.replace("_", "-") for n in _FIELD_NAMES))
        raise ConfigError(f"未知配置项：{key}", f"可用配置项：{known}")
    return normalized
