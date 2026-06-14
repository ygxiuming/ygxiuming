#!/usr/bin/env python3
"""
update_pins.py — 自动拉取 GitHub 仓库并刷新 README.md 中的 Pin 区块。

用法:
    python scripts/update_pins.py                 # 默认 user=ygxiuming, theme=bluewave
    python scripts/update_pins.py --user xxx      # 指定其他用户
    python scripts/update_pins.py --top 6         # 展示 Top 6
    python scripts/update_pins.py --theme radical # 切换卡片主题
    python scripts/update_pins.py --dry-run       # 仅预览, 不写入

筛选规则 (score 由高到低):
    + 与 AI / 图像 / Vue 关键词命中加权
    + stars * 10
    + 最近更新加权 (一年内 +5, 半年内 +10)
    - fork 仓库降权
    - archived / 仓库本身 (用户名同名) 排除
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

PIN_START = "<!-- PINNED-START -->"
PIN_END = "<!-- PINNED-END -->"

KEYWORDS_BOOST = {
    # AI / 图像 / 算法
    "ai": 8, "ml": 6, "cv": 8, "vision": 8, "image": 8, "deep": 6,
    "torch": 6, "pytorch": 6, "tensorflow": 6, "onnx": 5, "opencv": 8,
    "diffusion": 8, "gan": 8, "yolo": 8, "segment": 6, "detect": 6,
    # 主语言
    "python": 4, "cpp": 4, "c++": 4, "vue": 5,
}


def fetch_repos(user: str, token: str | None = None) -> list[dict]:
    """调用 GitHub 公共 REST API。
    匿名 60 次/小时, 带 token 5000 次/小时。可通过 --token 或环境变量
    GITHUB_TOKEN / GH_TOKEN 提供。
    """
    url = f"https://api.github.com/users/{user}/repos?per_page=100&sort=updated"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ygxiuming-pin-cli",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 403:
            sys.exit(
                "[!] GitHub API 限速 (HTTP 403)。\n"
                "    解决方法: 创建一个无任何权限的 Personal Access Token\n"
                "    https://github.com/settings/tokens?type=beta\n"
                "    然后设置环境变量 GITHUB_TOKEN, 或使用 --token <TOKEN>"
            )
        sys.exit(f"[!] GitHub API HTTP {e.code}: {e.reason}")
    except URLError as e:
        sys.exit(f"[!] 网络错误: {e.reason}")


def score_repo(repo: dict, user: str) -> float:
    if repo.get("fork") or repo.get("archived") or repo.get("private"):
        return -1
    if repo.get("name", "").lower() == user.lower():
        return -1  # profile 仓库本身

    score = 0.0
    score += (repo.get("stargazers_count") or 0) * 10
    score += (repo.get("forks_count") or 0) * 3
    score += (repo.get("watchers_count") or 0) * 1

    haystack = " ".join(
        filter(
            None,
            [
                repo.get("name", ""),
                repo.get("description") or "",
                repo.get("language") or "",
                " ".join(repo.get("topics") or []),
            ],
        )
    ).lower()
    for kw, weight in KEYWORDS_BOOST.items():
        if kw in haystack:
            score += weight

    pushed = repo.get("pushed_at")
    if pushed:
        try:
            dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - dt).days
            if days <= 180:
                score += 10
            elif days <= 365:
                score += 5
        except ValueError:
            pass

    if not (repo.get("description") or "").strip():
        score -= 2  # 没描述的项目展示效果差, 轻微降权
    return score


def render_pin_block(repos: list[dict], user: str, theme: str) -> str:
    """生成两列网格的 Pin 卡片 markdown。"""
    if not repos:
        return (
            f"{PIN_START}\n"
            f"<p align=\"center\"><i>暂无可展示的项目, 等你下一个杰作 🚀</i></p>\n"
            f"{PIN_END}"
        )

    lines = [PIN_START, "<p align=\"center\">"]
    for repo in repos:
        name = repo["name"]
        card = (
            f"  <a href=\"https://github.com/{user}/{name}\">"
            f"<img height=\"160\" "
            f"src=\"https://github-readme-stats.vercel.app/api/pin/"
            f"?username={user}&repo={name}&theme={theme}&hide_border=true\" "
            f"alt=\"{name}\" />"
            f"</a>"
        )
        lines.append(card)
    lines.append("</p>")
    lines.append(PIN_END)
    return "\n".join(lines)


def replace_block(content: str, new_block: str) -> str:
    pattern = re.compile(
        re.escape(PIN_START) + r".*?" + re.escape(PIN_END),
        re.DOTALL,
    )
    if pattern.search(content):
        return pattern.sub(new_block, content)
    # 未找到标记则追加到末尾
    sep = "\n\n" if not content.endswith("\n") else "\n"
    return content + sep + new_block + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="自动刷新 README 中的 Pin 区块")
    parser.add_argument("--user", default="ygxiuming", help="GitHub 用户名")
    parser.add_argument("--top", type=int, default=4, help="展示几个项目 (默认 4)")
    parser.add_argument(
        "--theme",
        default="bluewave",
        help="github-readme-stats 主题 (默认 bluewave)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只预览, 不写回 README"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
        help="GitHub PAT (默认读 GITHUB_TOKEN / GH_TOKEN 环境变量), 用于绕过匿名限速",
    )
    args = parser.parse_args()

    print(f"[*] 拉取 {args.user} 的公开仓库 ...")
    if args.token:
        print("    使用 token 认证")
    repos = fetch_repos(args.user, args.token)
    print(f"    共 {len(repos)} 个仓库")

    scored = [(score_repo(r, args.user), r) for r in repos]
    scored = [(s, r) for s, r in scored if s >= 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [r for _, r in scored[: args.top]]

    if not top:
        print("[!] 没有筛选到合适的仓库, 将渲染空占位。")
    else:
        print(f"[+] Top {len(top)}:")
        for s, r in scored[: args.top]:
            print(
                f"    - {r['name']:<30} "
                f"score={s:>5.1f}  "
                f"stars={r.get('stargazers_count', 0):<3} "
                f"lang={r.get('language') or '-'}"
            )

    block = render_pin_block(top, args.user, args.theme)

    if not README.exists():
        sys.exit(f"[!] 未找到 README: {README}")
    original = README.read_text(encoding="utf-8")
    updated = replace_block(original, block)

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(block)
        return 0

    if updated == original:
        print("[=] README 内容未变化。")
    else:
        README.write_text(updated, encoding="utf-8")
        print(f"[OK] 已更新 {README.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
