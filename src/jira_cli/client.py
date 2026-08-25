"""Jira REST API 封装。

用 requests 直接封装，不依赖 jira 官方库——鉴权、分页、错误展开都要自己
控制，尤其错误信息必须转成「怎么修」的提示（见 errors.py 的设计原则）。

Backend 是 Cloud 预留的插拔点之一（另一个是 markup.Codec）。差异不只是
换端点版本，还有真正的语义差异，见 ServerBackend.user_identity 的注释。
"""

from __future__ import annotations

import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

import requests

from .config import Config
from .errors import ApiError, AuthError, format_api_errors

#: 单次 search 请求的最大条数。Jira 服务端通常也有自己的上限，取小者生效。
PAGE_SIZE = 100
TIMEOUT = 60


class Backend(ABC):
    """不同 Jira 部署形态的适配层。

    当前只实现 ServerBackend。加 Cloud 时新增 CloudBackend 即可，
    调用方（cli / fields / meta_cache）不需要改动。
    """

    #: 部署标识，与 config.deployment 对应
    deployment: str

    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.verify = not config.insecure
        self.session.headers.update({"Accept": "application/json"})
        self._apply_auth()

    # -- 子类必须提供 ------------------------------------------------------
    @property
    @abstractmethod
    def api_base(self) -> str:
        """REST API 前缀，如 /rest/api/2。"""

    @abstractmethod
    def user_identity(self, user: dict) -> str:
        """从用户对象里取出「用于赋值给 assignee/reporter 的标识」。"""

    # -- 鉴权 --------------------------------------------------------------
    def _apply_auth(self) -> None:
        cfg = self.config
        if cfg.auth_type == "basic":
            self.session.auth = (cfg.login, cfg.token)
        else:
            self.session.headers["Authorization"] = f"Bearer {cfg.token}"

    # -- 底层请求 ----------------------------------------------------------
    def url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.config.base_url}{self.api_base}/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            resp = self.session.request(method, self.url(path), **kwargs)
        except requests.exceptions.SSLError as exc:
            raise ApiError(
                f"TLS 校验失败：{exc}",
                "若该实例用自签证书，可加 --insecure/-k 跳过校验。",
            ) from exc
        except requests.RequestException as exc:
            raise ApiError(f"请求失败：{exc}") from exc
        return self._handle(resp)

    def _handle(self, resp: requests.Response) -> Any:
        # 先解析、**再判状态**。反过来写的话「空 body」这一条会先命中，
        # 于是无正文的 403/502 被当成成功返回 None，错误就此消失
        try:
            payload = resp.json() if resp.content else None
        except ValueError:
            payload = None

        if resp.ok:
            return payload

        detail = format_api_errors(payload) or (resp.text or "")[:500] or "响应无正文"

        if resp.status_code in (401, 403):
            raise AuthError(
                f"鉴权失败（HTTP {resp.status_code}）：{detail}",
                "请检查：\n"
                "  1. PAT 是否已过期或被撤销（Jira 页面 →「个人设置」→「Personal Access Tokens」）\n"
                "  2. 当前账号对该资源是否有权限\n"
                "  3. jira-cli config get 看 url / token 是否正确",
            )
        raise ApiError(f"HTTP {resp.status_code}：{detail}", status=resp.status_code)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)


class ServerBackend(Backend):
    """Jira Server / Data Center，REST API v2。"""

    deployment = "server"

    @property
    def api_base(self) -> str:
        return "/rest/api/2"

    def user_identity(self, user: dict) -> str:
        """Server 用登录名标识用户。

        这是与 Cloud 的**语义差异**而非换端点：Cloud 必须用 accountId，
        Server 用 name。写 CloudBackend 时这里返回 user["accountId"]。
        """
        return user.get("name") or user.get("key") or ""

    # -- 元信息 ------------------------------------------------------------
    def server_info(self) -> dict:
        return self.get("/serverInfo") or {}

    def myself(self) -> dict:
        return self.get("/myself") or {}

    def projects(self) -> list[dict]:
        return self.get("/project") or []

    def issue_types(self) -> list[dict]:
        return self.get("/issuetype") or []

    def statuses(self) -> list[dict]:
        return self.get("/status") or []

    def priorities(self) -> list[dict]:
        return self.get("/priority") or []

    def fields(self) -> list[dict]:
        return self.get("/field") or []

    def search_users(self, query: str, limit: int = 20) -> list[dict]:
        return self.get(
            "/user/search",
            params={"username": query, "maxResults": limit},
        ) or []

    def create_meta(self, project: str, issue_type: str | None = None) -> dict:
        params: dict[str, Any] = {
            "projectKeys": project,
            "expand": "projects.issuetypes.fields",
        }
        if issue_type:
            params["issuetypeNames"] = issue_type
        return self.get("/issue/createmeta", params=params) or {}

    # -- 查询 --------------------------------------------------------------
    def search(
        self,
        jql: str,
        limit: int = 50,
        fields: Sequence[str] | None = None,
        expand: Sequence[str] | None = None,
    ) -> dict:
        """按 JQL 搜索，自动翻页直到取满 limit。

        v2 用 startAt + maxResults 翻页；Cloud v3 只有 maxResults。
        """
        collected: list[dict] = []
        total = 0
        start = 0
        while len(collected) < limit:
            params: dict[str, Any] = {
                "jql": jql,
                "startAt": start,
                "maxResults": min(PAGE_SIZE, limit - len(collected)),
            }
            if fields:
                params["fields"] = ",".join(fields)
            if expand:
                params["expand"] = ",".join(expand)
            page = self.get("/search", params=params) or {}
            total = page.get("total", 0)
            issues = page.get("issues") or []
            collected.extend(issues)
            start += len(issues)
            if not issues or start >= total:
                break
        return {"total": total, "issues": collected[:limit]}

    def get_issue(
        self,
        key: str,
        fields: Sequence[str] | None = None,
        expand: Sequence[str] | None = None,
    ) -> dict:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = ",".join(expand)
        return self.get(f"/issue/{key}", params=params) or {}

    # -- 写 ----------------------------------------------------------------
    def create_issue(self, payload: dict) -> dict:
        return self.post("/issue", json=payload) or {}

    def update_issue(self, key: str, payload: dict) -> None:
        self.put(f"/issue/{key}", json=payload)

    def transitions(self, key: str) -> list[dict]:
        data = self.get(
            f"/issue/{key}/transitions",
            params={"expand": "transitions.fields"},
        ) or {}
        return data.get("transitions") or []

    def do_transition(self, key: str, payload: dict) -> None:
        self.post(f"/issue/{key}/transitions", json=payload)

    def add_comment(self, key: str, body: str) -> dict:
        return self.post(f"/issue/{key}/comment", json={"body": body}) or {}

    def comments(self, key: str) -> list[dict]:
        data = self.get(f"/issue/{key}/comment") or {}
        return data.get("comments") or []

    # -- 附件 --------------------------------------------------------------
    def upload_attachments(self, key: str, paths: Sequence[Path]) -> list[dict]:
        """上传附件。

        Jira 要求带 X-Atlassian-Token: no-check 头（XSRF 防护豁免），
        且表单字段名必须是 file。
        """
        files = []
        handles = []
        try:
            for path in paths:
                fh = path.open("rb")
                handles.append(fh)
                ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(("file", (path.name, fh, ctype)))
            return self.post(
                f"/issue/{key}/attachments",
                files=files,
                headers={"X-Atlassian-Token": "no-check"},
            ) or []
        finally:
            for fh in handles:
                fh.close()

    def download_attachment(self, content_url: str, dest: Path) -> int:
        """下载附件到本地。

        附件 content URL 必须带认证头才能取，所以不能把裸链接丢给调用方
        自己下——这也是本工具必须落盘的原因。

        先写 .part 再改名：中途断网或 Ctrl-C 不会在落点留下一个大小不对的
        半截文件——它看起来是「下好了」，读的人拿到的却是截断内容。
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with self.session.get(content_url, stream=True, timeout=TIMEOUT) as resp:
                if not resp.ok:
                    raise ApiError(
                        f"附件下载失败 HTTP {resp.status_code}：{content_url}",
                        status=resp.status_code,
                    )
                size = 0
                with part.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        fh.write(chunk)
                        size += len(chunk)
        except requests.RequestException as exc:
            # 这条路径不经过 request()，不包一层就会直接吐 requests 的 traceback
            part.unlink(missing_ok=True)
            raise ApiError(f"附件下载失败：{exc}") from exc
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(dest)
        return size


def build_backend(config: Config) -> Backend:
    """按配置造 Backend。当前只有 server 一种实现。"""
    config.require_connection()
    return ServerBackend(config)


def detect_deployment(url: str, insecure: bool = False) -> dict:
    """探测部署形态。config init 用，此时还没有可用的 Backend。"""
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/api/2/serverInfo",
            timeout=TIMEOUT,
            verify=not insecure,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise ApiError(
            f"无法连接到 {url}：{exc}",
            "请检查 URL 是否正确、网络是否可达（内网实例注意代理设置）。",
        ) from exc
