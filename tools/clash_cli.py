#!/usr/bin/env python3
"""
Clash REST API helper

功能：
  - 列出策略组与节点
  - 对节点执行延迟测试
  - 在策略组中切换节点
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


DEFAULT_HOST = os.getenv("CLASH_API_HOST", "127.0.0.1:9090")
DEFAULT_SECRET = os.getenv("CLASH_API_SECRET")
DEFAULT_CONFIG_PATH = Path(os.getenv("CLASH_CLI_CONFIG", "~/.config/clash_cli.json")).expanduser()


class ClashClient:
    def __init__(self, host: str, secret: Optional[str] = None, timeout: int = 10):
        if host.startswith("http://") or host.startswith("https://"):
            self.base = host.rstrip("/")
        else:
            self.base = f"http://{host}".rstrip("/")
        self.secret = secret
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        query: Optional[Dict[str, str]] = None,
    ):
        url = urllib.parse.urljoin(self.base + "/", path.lstrip("/"))
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"

        payload = None
        if data is not None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, method=method.upper(), headers=headers, data=payload)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return {}
            return json.loads(body)

    def proxies(self) -> Dict[str, Dict]:
        return self._request("GET", "/proxies").get("proxies", {})

    def proxy(self, name: str) -> Dict:
        encoded = urllib.parse.quote(name, safe="")
        return self._request("GET", f"/proxies/{encoded}")

    def switch(self, group: str, node: str) -> Dict:
        encoded = urllib.parse.quote(group, safe="")
        return self._request("PUT", f"/proxies/{encoded}", data={"name": node})

    def delay(self, name: str, test_url: str, timeout_ms: int) -> Dict:
        encoded = urllib.parse.quote(name, safe="")
        query = {"timeout": str(timeout_ms), "url": test_url}
        return self._request("GET", f"/proxies/{encoded}/delay", query=query)


def is_selector(proxy_info: Dict) -> bool:
    return proxy_info.get("type") in {"Selector", "URLTest", "Fallback", "LoadBalance"}


def list_proxies(client: ClashClient, show_groups: bool, show_nodes: bool) -> None:
    proxies = client.proxies()
    if show_groups:
        print("=== Policy Groups ===")
        for name, info in proxies.items():
            if is_selector(info):
                now = info.get("now")
                members = ", ".join(info.get("all", []))
                print(f"{name} [{info.get('type', '?')}]: now={now}; members={members}")
    if show_nodes:
        print("=== Endpoint Nodes ===")
        for name, info in proxies.items():
            if not is_selector(info):
                print(f"{name} [{info.get('type', '?')}], udp={info.get('udp')}")


def nodes_from_group(client: ClashClient, group: str) -> Iterable[str]:
    info = client.proxy(group)
    if not is_selector(info):
        raise ValueError(f"'{group}' 不是策略组（Selector/URLTest 等）。")
    return info.get("all", [])


def test_delays(client: ClashClient, targets: Iterable[str], url: str, timeout: int) -> None:
    for name in targets:
        try:
            result = client.delay(name, url, timeout)
            delay = result.get("delay")
            if delay is None or delay < 0:
                print(f"{name}: timeout/no response")
            else:
                print(f"{name}: {delay} ms")
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: request failed ({exc})", file=sys.stderr)


def load_config(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"读取配置文件失败：{exc}", file=sys.stderr)
        return {}


def save_config(path: Path, data: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"已保存配置到 {path}")


def parse_args() -> Tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(description="Clash REST API helper")
    parser.add_argument("--host", help=f"API host（默认：配置文件或 {DEFAULT_HOST}）")
    parser.add_argument("--secret", help="API secret（默认读取配置文件或 env CLASH_API_SECRET）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help=f"配置文件路径（默认：{DEFAULT_CONFIG_PATH}）")

    sub = parser.add_subparsers(dest="command")

    list_cmd = sub.add_parser("list", help="列出策略组或节点")
    list_cmd.add_argument("--groups", action="store_true", help="仅显示策略组")
    list_cmd.add_argument("--nodes", action="store_true", help="仅显示节点")

    ping_cmd = sub.add_parser("ping", help="测试节点延迟")
    ping_cmd.add_argument("--group", help="策略组名，测试该组内所有节点")
    ping_cmd.add_argument("--node", action="append", help="节点名称，可重复；指定 group 时忽略")
    ping_cmd.add_argument("--url", default="https://www.gstatic.com/generate_204", help="测速 URL")
    ping_cmd.add_argument("--timeout", type=int, default=5000, help="超时时间（毫秒）")

    switch_cmd = sub.add_parser("switch", help="切换策略组到指定节点")
    switch_cmd.add_argument("group", help="策略组名称")
    switch_cmd.add_argument("node", help="节点名称")
    switch_cmd.add_argument("--validate", action="store_true", help="切换前校验节点是否属于该策略组")

    config_cmd = sub.add_parser("config", help="查看或修改默认 host/secret")
    config_cmd.add_argument("--host", help="设置默认 host")
    config_cmd.add_argument("--secret", help="设置默认 secret")
    config_cmd.add_argument("--show", action="store_true", help="显示当前保存的配置")

    args = parser.parse_args()
    return parser, args


def main() -> None:
    parser, args = parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    config_path = Path(args.config).expanduser()
    stored = load_config(config_path)

    if args.command == "config":
        updated = dict(stored)
        changed = False
        if args.host is not None:
            updated["host"] = args.host
            changed = True
        if args.secret is not None:
            updated["secret"] = args.secret
            changed = True
        if changed:
            save_config(config_path, updated)
        if args.show or not changed:
            host_val = updated.get("host", DEFAULT_HOST)
            secret_val = updated.get("secret", DEFAULT_SECRET)
            print(f"host: {host_val}")
            print(f"secret: {secret_val or '(empty)'}")
        return

    # CLI 参数优先，其次配置文件，最后环境变量/默认值
    host = args.host if args.host is not None else stored.get("host", DEFAULT_HOST)
    secret = args.secret if args.secret is not None else stored.get("secret", DEFAULT_SECRET)
    client = ClashClient(host, secret)

    if args.command == "list":
        show_groups = args.groups or not args.nodes
        show_nodes = args.nodes or not args.groups
        list_proxies(client, show_groups, show_nodes)
        return

    if args.command == "ping":
        if args.group:
            nodes = list(nodes_from_group(client, args.group))
            print(f"Testing group '{args.group}' ({len(nodes)} nodes)")
        else:
            if not args.node:
                print("请通过 --group 或 --node 指定需要测速的节点。", file=sys.stderr)
                sys.exit(1)
            nodes = args.node
        test_delays(client, nodes, args.url, args.timeout)
        return

    if args.command == "switch":
        if args.validate:
            members = set(nodes_from_group(client, args.group))
            if args.node not in members:
                print(f"节点 '{args.node}' 不在策略组 '{args.group}' 中。成员：{', '.join(members)}")
                sys.exit(1)
        result = client.switch(args.group, args.node)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    raise RuntimeError("未知命令")


if __name__ == "__main__":
    main()
