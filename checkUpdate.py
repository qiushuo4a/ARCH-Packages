#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch0

功能:
  1. 递归扫描目录(默认当前目录)下所有 PKGBUILD 文件
  2. 解析每个 PKGBUILD 中的 pkgname / pkgver / url 字段
  3. 若 url 指向 GitHub 仓库,则通过 GitHub API 查询最新 Release 版本,
     与 PKGBUILD 中声明的 pkgver 对比
  4. 非 GitHub 的 url 可通过「用户自定义版本获取接口」注册检测函数,
     同样参与版本对比(见下方 自定义接口区)
  5. 生成检测报告,分三类列出:
       - 版本相同(已是最新)
       - 版本不同(需要更新)
       - 非 GitHub,未检查(未注册自定义接口)
     另外 API 失败的会单独归入「检查失败」

输出:
  报告默认只打印到 stdout(不带 ANSI 颜色时适合重定向保存);
  进度提示走 stderr,不会混入重定向的文件。
  需要保存 Markdown 报告时用 -o 显式指定。

用法:
  python3 checkUpdate.py                  # 扫描当前目录,报告打印到终端
  python3 checkUpdate.py > report.txt     # 管道/重定向保存纯文本报告
  python3 checkUpdate.py -o update.md     # 额外保存 Markdown 报告文件
  python3 checkUpdate.py -d /path/repo    # 扫描指定目录

环境变量:
  GITHUB_TOKEN / GH_TOKEN   可选,提供后提升 GitHub API 速率限制(60/h -> 5000/h)

退出码:
  0 = 全部为最新(或无 GitHub 包);1 = 存在需更新或检查失败的包;2 = 脚本自身错误

仅依赖 Python 标准库。
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import sys
import time
import importlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


#脚本路径path
sys.path.insert(0, ".scripts/version_script")

#导入脚本模块
laplace_persona = importlib.import_module("laplace_persona")


# ---------------------------------------------------------------- 常量

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 20          # 单次请求超时(秒)
MAX_WORKERS = 8               # 并发请求数
RETRY_COUNT = 2               # 网络错误重试次数

# github.com/<owner>/<repo>,兼容结尾 / 与 .git
GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)

# PKGBUILD 变量行: pkgname=xxx / pkgver=xxx / url=xxx,兼容单双引号与无引号
VAR_RE_CACHE = {
    name: re.compile(rf"^\s*{name}\s*=\s*(.+?)\s*$")
    for name in ("pkgname", "pkgver", "url")
}

# 状态常量
ST_SAME = "same"          # 版本相同
ST_DIFF = "diff"          # 版本不同
ST_NON_GH = "nongh"       # 非 GitHub 且无自定义接口,未检查
ST_ERROR = "error"        # 检查失败(网络/仓库不存在/自定义接口出错等)

# ---------------------------------------------------------------- ANSI 颜色

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, code: str) -> str:
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t): return c(t, "32")
def yellow(t): return c(t, "33")
def red(t): return c(t, "31")
def dim(t): return c(t, "2")
def bold(t): return c(t, "1")


def info(msg: str) -> None:
    """进度提示,输出到 stderr,避免污染重定向的报告内容。"""
    print(dim(msg), file=sys.stderr)


# ---------------------------------------------------------------- 数据结构

@dataclass
class PkgResult:
    path: str                 # PKGBUILD 相对路径
    pkgname: str = ""         # 包名
    pkgver: str = ""          # PKGBUILD 声明的版本
    url: str = ""             # url 字段
    repo: str = ""            # owner/repo(GitHub 时)
    fetcher: object = None    # 匹配到的自定义版本获取函数(非 GitHub 时)
    status: str = ""          # ST_*
    latest: str = ""          # 最新版本(已归一化)
    latest_url: str = ""       # Release / 发布页链接
    note: str = ""             # 备注(来源、方向、错误信息等)
    raw_tag: str = ""          # 原始版本标签(未去掉 v 前缀)


def join_note(old: str, new: str) -> str:
    return f"{old}; {new}" if old else new


# ---------------------------------------------------------------- PKGBUILD 解析

def strip_quotes(value: str) -> str:
    """去掉变量值两侧的引号与括号。"""
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value.strip()


def parse_pkgbuild(path: Path) -> dict:
    """解析 PKGBUILD,提取 pkgname / pkgver / url。"""
    result = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"_error": f"读取失败: {e}"}

    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for name, regex in VAR_RE_CACHE.items():
            if name in result:
                continue
            m = regex.match(line)
            if m:
                result[name] = strip_quotes(m.group(1))
    return result


def scan_pkgbuilds(root: Path) -> list:
    """递归查找所有 PKGBUILD(跳过 .git 目录)。"""
    return [
        pb for pb in sorted(root.rglob("PKGBUILD"))
        if not any(part == ".git" for part in pb.parts)
    ]


# ---------------------------------------------------------------- 版本处理

def normalize_tag(tag: str) -> str:
    """标准化版本标签:去掉 refs/tags/ 前缀与开头的 v/V。"""
    tag = str(tag).strip()
    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]
    if len(tag) > 1 and tag[0] in "vV" and (tag[1].isdigit() or tag[1] == "."):
        tag = tag[1:]
    return tag.strip()


def _ver_tokens(v: str):
    """把版本拆成 数字/字母/符号 令牌序列用于语义比较。"""
    return re.findall(r"\d+|[A-Za-z]+|[^A-Za-z0-9]+", v)


def compare_versions(a: str, b: str) -> int:
    """语义化版本粗略比较:返回 -1 / 0 / 1。仅用于判断方向,不用于相等判定。"""
    ta, tb = _ver_tokens(a), _ver_tokens(b)

    def key(t):
        if t.isdigit():
            return (0, int(t), "")
        if t.isalpha():
            return (1, 0, t)
        return (2, 0, t)

    for x, y in zip(ta, tb):
        kx, ky = key(x), key(y)
        if kx != ky:
            return -1 if kx < ky else 1
    if len(ta) == len(tb):
        return 0
    return -1 if len(ta) < len(tb) else 1


# ---------------------------------------------------------------- 自定义版本获取接口

_CUSTOM_FETCHERS = []   # [(pattern, fn)],按注册顺序匹配


def version_fetcher(pattern: str):
    """装饰器:为匹配 pattern 的 PKGBUILD url 注册自定义版本获取函数。

    详细用法见下方「用户自定义版本获取区」的说明。
    """
    def decorator(fn):
        _CUSTOM_FETCHERS.append((pattern, fn))
        return fn
    return decorator


def _norm_url(u: str) -> str:
    """归一化 URL 用于匹配:去空白、去末尾 /,scheme 与 host 转小写。"""
    u = str(u).strip().rstrip("/")
    m = re.match(r"^(https?://)([^/]+)(.*)$", u, re.IGNORECASE)
    if m:
        return (m.group(1) + m.group(2)).lower() + m.group(3)
    return u


def match_custom_fetcher(url: str):
    """返回第一个匹配 url 的自定义获取函数;无匹配返回 None。

    匹配规则:归一化后完全相等,或 url 以 pattern 开头且紧随边界字符 '/'。
    例:注册 "https://laplace.live" 可匹配 "https://laplace.live/persona"。
    """
    u = _norm_url(url)
    for pattern, fn in _CUSTOM_FETCHERS:
        p = _norm_url(pattern)
        if u == p or (u.startswith(p) and u[len(p):][:1] == "/"):
            return fn
    return None


def _http_get_text(url: str, timeout: int = REQUEST_TIMEOUT, headers=None) -> str:
    """HTTP GET 辅助函数(跟随重定向,返回响应文本),供自定义接口使用。"""
    hdrs = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) checkUpdate.py",
        "Accept": "*/*",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


# ═══════════════════════════════════════════════════════════════════════
# ███  用户自定义版本获取区(非 GitHub 的 url 在此填写检测函数)  ███
# ═══════════════════════════════════════════════════════════════════════
#
# url 不是 GitHub 仓库的软件包默认归入「非 GitHub · 未检查」。
# 在此用 @version_fetcher("<url 前缀>") 注册一个获取最新版本的函数,
# 该包即可像 GitHub 包一样参与版本对比:
#
#   - 函数签名: fn(url: str),url 为该 PKGBUILD 的 url 字段值
#   - 返回值:   最新版本号字符串,写法与 pkgver 一致(如 "2.23.1";
#               开头带 v 也可以,程序会自动归一化后比较)
#               也可返回 (版本号, 上游发布页 URL) 二元组
#   - 失败时:   直接 raise(如 ValueError("...")),会显示在报告的
#               「检查失败」区;抛 NotImplementedError 则视为未实现,
#               该包回到「非 GitHub · 未检查」
#   - 匹配规则: 忽略大小写与末尾 / 后做前缀匹配,如注册
#               "https://laplace.live" 即可匹配 "https://laplace.live/persona"
#   - 可用辅助: _http_get_text(url) 获取页面/接口文本
#
# ---- 示例(取消注释并补全逻辑即可生效) ----
#
# @version_fetcher("https://www.bitcomet.com/")
# def fetch_bitcomet(url):
#     """BitComet 官网为 JS 渲染,需找到真实版本数据源后提取。"""
#     html = _http_get_text("https://www.bitcomet.com/linux")
#     m = re.search(r"BitComet-([\d.]+)-x86_64\.deb", html)
#     if not m:
#         raise ValueError("页面中未找到 BitComet 版本号")
#     return m.group(1)
#
# @version_fetcher("https://laplace.live/persona")
# def fetch_laplace_persona(url):
#     """downloads.vrp.moe 有 Cloudflare 防护,可改用官方更新接口获取版本。"""
#     data = json.loads(_http_get_text("https://example.com/latest.json"))
#     return data["version"], "https://laplace.live/persona"
#
# ═══════════════════════════════════════════════════════════════════════

@version_fetcher("https://laplace.live/persona")
def fetch_laplace_persona(url):
    return laplace_persona.check_version_interface()



# ---------------------------------------------------------------- GitHub API

def gh_request(api_path: str, token: str):
    """GET 一个 GitHub API 路径,返回 (status, data, headers)。"""
    url = GITHUB_API + api_path
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "checkUpdate.py",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_err = None
    for _attempt in range(RETRY_COUNT):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:
                data = {}
            return e.code, data, dict(e.headers)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
    return None, {"message": f"网络错误: {last_err}"}, {}


def fetch_latest_release(owner: str, repo: str, token: str) -> dict:
    """查询仓库最新 Release;若无 Release 则回退到最新 tag。"""
    status, data, _ = gh_request(f"/repos/{owner}/{repo}/releases/latest", token)
    if status == 200:
        return {
            "tag": data.get("tag_name", ""),
            "url": data.get("html_url", f"https://github.com/{owner}/{repo}/releases"),
            "source": "release",
        }
    if status == 404:
        status2, data2, _ = gh_request(f"/repos/{owner}/{repo}/tags?per_page=1", token)
        if status2 == 200 and data2:
            return {
                "tag": data2[0].get("name", ""),
                "url": f"https://github.com/{owner}/{repo}/tags",
                "source": "tag",
            }
        return {"error": f"仓库无 Release 且查询 tag 失败(HTTP {status2})"}
    if status in (403, 429):
        return {"error": f"API 速率受限(HTTP {status}),可设置 GITHUB_TOKEN 环境变量"}
    if status == 401:
        return {"error": "Token 无效(HTTP 401)"}
    msg = data.get("message") if isinstance(data, dict) else ""
    return {"error": f"GitHub API 错误(HTTP {status}): {msg or '未知'}"}


# ---------------------------------------------------------------- 检测逻辑

def apply_comparison(pkg: PkgResult, raw_version: str) -> None:
    """将上游最新版本与 pkgver 对比,写入 status / note。"""
    latest = normalize_tag(raw_version)
    pkg.raw_tag = str(raw_version)
    pkg.latest = latest

    if latest == pkg.pkgver:
        pkg.status = ST_SAME
    elif latest and pkg.pkgver and compare_versions(latest, pkg.pkgver) == 0:
        # 语义相同但写法不同(如 1.06.1 vs 1.6.1),视为相同但提示
        pkg.status = ST_SAME
        pkg.note = join_note(pkg.note, f"标签写法与 pkgver 略有差异({raw_version})")
    else:
        pkg.status = ST_DIFF
        if compare_versions(latest, pkg.pkgver) > 0:
            pkg.note = join_note(pkg.note, "上游有新版本")
        else:
            pkg.note = join_note(pkg.note, "本地版本高于上游")


def check_github_package(pkg: PkgResult, token: str) -> PkgResult:
    """对比单个 GitHub 包的本地版本与上游最新版本。"""
    info = fetch_latest_release(*pkg.repo.split("/"), token=token)
    if "error" in info:
        pkg.status = ST_ERROR
        pkg.note = info["error"]
        return pkg

    if info.get("source") == "tag":
        pkg.note = join_note(pkg.note, "上游无正式 Release,使用最新 tag 对比")
    pkg.latest_url = info.get("url", "")
    apply_comparison(pkg, info.get("tag", ""))
    return pkg


def check_custom_package(pkg: PkgResult, fn) -> PkgResult:
    """调用用户自定义接口获取版本并对比。"""
    pkg.note = f"自定义接口 {getattr(fn, '__name__', fn)}"
    try:
        ret = fn(pkg.url)
    except NotImplementedError:
        # 占位函数尚未实现:回到「非 GitHub · 未检查」
        pkg.status = ST_NON_GH
        pkg.note = join_note(pkg.note, "接口已注册但未实现")
        return pkg
    except Exception as e:
        pkg.status = ST_ERROR
        pkg.note = join_note(pkg.note, f"获取失败: {e}")
        return pkg

    # 兼容返回 (版本号, 发布页URL) 二元组
    if isinstance(ret, (tuple, list)) and len(ret) == 2:
        raw_ver, pkg.latest_url = str(ret[0]), str(ret[1])
    else:
        raw_ver, pkg.latest_url = str(ret), pkg.url

    if not raw_ver.strip():
        pkg.status = ST_ERROR
        pkg.note = join_note(pkg.note, "接口返回了空版本号")
        return pkg
    apply_comparison(pkg, raw_ver)
    return pkg


def check_one(pkg: PkgResult, token: str) -> PkgResult:
    """分发单个包的检测(GitHub / 自定义接口 / 无需检测)。"""
    if pkg.repo:
        return check_github_package(pkg, token)
    if pkg.fetcher is not None:
        return check_custom_package(pkg, pkg.fetcher)
    return pkg  # 非 GitHub 且未注册接口,保持 ST_NON_GH


def build_results(pkgbuild_paths: list, root: Path, token: str) -> list:
    """解析全部 PKGBUILD,并并发检测 GitHub 包与注册了自定义接口的包。"""
    results = []

    for pb in pkgbuild_paths:
        item = PkgResult(path=str(pb.relative_to(root)))
        parsed = parse_pkgbuild(pb)

        if "_error" in parsed:
            item.status = ST_ERROR
            item.note = parsed["_error"]
            results.append(item)
            continue

        item.pkgname = parsed.get("pkgname", "")
        item.pkgver = parsed.get("pkgver", "")
        item.url = parsed.get("url", "")

        if not item.pkgver or not item.url:
            item.status = ST_ERROR
            item.note = "PKGBUILD 缺少 pkgver 或 url 字段"
            results.append(item)
            continue

        m = GITHUB_URL_RE.match(item.url)
        if m:
            item.repo = f"{m.group(1)}/{m.group(2)}"
        else:
            item.fetcher = match_custom_fetcher(item.url)
            if item.fetcher is None:
                item.status = ST_NON_GH
        results.append(item)

    # 并发检测(GitHub API + 自定义接口)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_one, p, token): p
                   for p in results if p.status not in (ST_NON_GH, ST_ERROR)}
        for fut in concurrent.futures.as_completed(futures):
            pkg = futures[fut]
            try:
                fut.result()
            except Exception as e:  # 兜底,避免异常被静默吞掉
                pkg.status = ST_ERROR
                pkg.note = f"检测异常: {e}"

    results.sort(key=lambda r: r.path)
    return results


# ---------------------------------------------------------------- 报告渲染

def _group(results: list) -> dict:
    return {
        ST_SAME: [r for r in results if r.status == ST_SAME],
        ST_DIFF: [r for r in results if r.status == ST_DIFF],
        ST_NON_GH: [r for r in results if r.status == ST_NON_GH],
        ST_ERROR: [r for r in results if r.status == ST_ERROR],
    }


def render_console(results: list, elapsed: float) -> None:
    """把报告打印到 stdout(非终端时自动无色,可直接重定向保存)。"""
    groups = _group(results)

    print(bold("========== PKGBUILD 版本检测报告 =========="))
    print(dim(f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
              f" · 共 {len(results)} 个软件包 · 耗时 {elapsed:.1f}s"))

    # 版本不同(最重要的放最前)
    print(bold(red(f"\n[版本不同] {len(groups[ST_DIFF])} 个 —— 需要更新")))
    for r in groups[ST_DIFF]:
        print(f"  {red('✗')} {r.pkgname:<32} {yellow(r.pkgver)} -> {green(r.latest)}"
              f"  {dim(r.note)}")
        print(f"    {dim(r.path)}  {dim(r.latest_url)}")
    if not groups[ST_DIFF]:
        print(green("  (无,所有已检测包均为最新)"))

    # 版本相同
    print(bold(green(f"\n[版本相同] {len(groups[ST_SAME])} 个 —— 已是最新")))
    for r in groups[ST_SAME]:
        note = f"  {dim(r.note)}" if r.note else ""
        print(f"  {green('✓')} {r.pkgname:<32} {r.pkgver}{note}")
        print(f"    {dim(r.path)}")
    if not groups[ST_SAME]:
        print(dim("  (无)"))

    # 非 GitHub
    print(bold(yellow(f"\n[非 GitHub · 未检查] {len(groups[ST_NON_GH])} 个")))
    for r in groups[ST_NON_GH]:
        note = f"  {dim(r.note)}" if r.note else ""
        print(f"  {yellow('-')} {r.pkgname:<32} {r.pkgver}  {dim(r.url)}{note}")
        print(f"    {dim(r.path)}")
    if not groups[ST_NON_GH]:
        print(dim("  (无)"))

    # 检查失败
    if groups[ST_ERROR]:
        print(bold(red(f"\n[检查失败] {len(groups[ST_ERROR])} 个")))
        for r in groups[ST_ERROR]:
            print(f"  {red('!')} {r.pkgname or r.path:<32} {red(r.note)}")
            print(f"    {dim(r.path)}")

    # 汇总
    print(bold("\n---------- 汇总 ----------"))
    print(f"  版本相同:   {green(str(len(groups[ST_SAME])))}")
    print(f"  版本不同:   {red(str(len(groups[ST_DIFF])))}")
    print(f"  非 GitHub:  {yellow(str(len(groups[ST_NON_GH])))}")
    print(f"  检查失败:   {red(str(len(groups[ST_ERROR])))}")


def render_markdown(results: list, root: Path, elapsed: float) -> str:
    """生成 Markdown 报告文本。"""
    groups = _group(results)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# PKGBUILD 版本检测报告",
        "",
        f"- **生成时间**: {now}",
        f"- **扫描目录**: `{root.resolve()}`",
        f"- **软件包总数**: {len(results)}",
        f"- **检测耗时**: {elapsed:.1f} 秒",
        "",
        "| 类别 | 数量 |",
        "| --- | ---: |",
        f"| 版本相同(已是最新) | {len(groups[ST_SAME])} |",
        f"| **版本不同(需更新)** | **{len(groups[ST_DIFF])}** |",
        f"| 非 GitHub(未检查) | {len(groups[ST_NON_GH])} |",
        f"| 检查失败 | {len(groups[ST_ERROR])} |",
    ]

    # 版本不同
    lines += ["", f"## ⬆️ 版本不同({len(groups[ST_DIFF])} 个)", ""]
    if groups[ST_DIFF]:
        lines += ["| 软件包 | 当前版本 | 最新版本 | 状态 | 目录 | 上游 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for r in groups[ST_DIFF]:
            direction = "上游有新版本" if "上游有新版本" in r.note else (
                "本地高于上游" if "本地版本高于上游" in r.note else "不同")
            lines.append(
                f"| `{r.pkgname}` | `{r.pkgver}` | [`{r.latest}`]({r.latest_url}) "
                f"| {direction} | `{r.path}` | [{r.repo or r.url}]({r.url}) |")
    else:
        lines.append("所有已检测的软件包均已是最新版本。")

    # 版本相同
    lines += ["", f"## ✅ 版本相同({len(groups[ST_SAME])} 个)", ""]
    if groups[ST_SAME]:
        lines += ["| 软件包 | 版本 | 目录 | 上游 |", "| --- | --- | --- | --- |"]
        for r in groups[ST_SAME]:
            note = f"<br>*{r.note}*" if r.note else ""
            lines.append(
                f"| `{r.pkgname}` | `{r.pkgver}`{note} | `{r.path}` | [{r.repo or r.url}]({r.url}) |")
    else:
        lines.append("(无)")

    # 非 GitHub
    lines += ["", f"## ➖ 非 GitHub 未检查({len(groups[ST_NON_GH])} 个)", ""]
    if groups[ST_NON_GH]:
        lines += ["| 软件包 | 版本 | url | 备注 | 目录 |", "| --- | --- | --- | --- | --- |"]
        for r in groups[ST_NON_GH]:
            lines.append(f"| `{r.pkgname}` | `{r.pkgver}` | {r.url} | {r.note} | `{r.path}` |")
    else:
        lines.append("(无)")

    # 检查失败
    if groups[ST_ERROR]:
        lines += ["", f"## ⚠️ 检查失败({len(groups[ST_ERROR])} 个)", ""]
        lines += ["| 软件包 | 原因 | 目录 |", "| --- | --- | --- |"]
        for r in groups[ST_ERROR]:
            lines.append(f"| `{r.pkgname or r.path}` | {r.note} | `{r.path}` |")

    lines += ["", "---", "", "*由 `checkUpdate.py` 自动生成*"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程

def main() -> int:
    parser = argparse.ArgumentParser(
        description="PKGBUILD 仓库 GitHub Release 版本更新检测"
                    "(报告默认打印到 stdout,用 -o 保存 Markdown 报告)")
    parser.add_argument("-d", "--dir", default=".", help="要扫描的仓库根目录(默认当前目录)")
    parser.add_argument("-o", "--output", default=None, metavar="FILE",
                        help="将 Markdown 报告写入指定文件(默认不保存文件)")
    parser.add_argument("--no-color", action="store_true", help="禁用控制台颜色")
    args = parser.parse_args()

    if args.no_color:
        global USE_COLOR
        USE_COLOR = False

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(red(f"错误: 目录不存在: {root}"), file=sys.stderr)
        return 2

    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()

    pkgbuilds = scan_pkgbuilds(root)
    if not pkgbuilds:
        info("未找到任何 PKGBUILD 文件")
        return 0

    gh_count = sum(1 for pb in pkgbuilds if GITHUB_URL_RE.match(parse_pkgbuild(pb).get("url", "")))
    info(f"扫描到 {len(pkgbuilds)} 个 PKGBUILD(GitHub: {gh_count}),"
         f"开始检测上游版本...\n")

    t0 = time.time()
    results = build_results(pkgbuilds, root, token)
    elapsed = time.time() - t0

    render_console(results, elapsed)   # 报告本身 -> stdout

    if args.output:
        report = render_markdown(results, root, elapsed)
        out = Path(args.output)
        try:
            out.write_text(report, encoding="utf-8")
            info(f"Markdown 报告已写入: {out.resolve()}")
        except OSError as e:
            print(red(f"错误: 无法写入报告文件 {out}: {e}"), file=sys.stderr)
            return 2

    return 1 if _group(results)[ST_DIFF] or _group(results)[ST_ERROR] else 0


if __name__ == "__main__":
    sys.exit(main())
