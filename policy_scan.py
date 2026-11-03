#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日政策扫描与宏观解读 (GitHub Action 版)
================================================
- 优先使用 智谱 GLM-4-Flash（免费 + 内置 web_search 实时联网检索）扫描 8 大部委官网政策 + 全球事件/科学论文/国际政策
- 未配置智谱 key 时，自动回退到 Gemini + Google Search 实现（需 GEMINI_API_KEY）
- 三视角深度解读：资深交易员 / 宏观经济分析师 / 公司基本面研究员
- 输出落盘 policy_digest/YYYY-MM-DD.md
- 精简摘要推送微信 (PushPlus)

依赖：仅标准库（urllib / json），无需第三方包
环境变量（二选一即可）：
  [智谱 GLM-4-Flash，推荐，免费且实时联网]
  ZHIPU_API_KEY     (必填，智谱 API Key，申请：https://open.bigmodel.cn/usercenter/apikeys)
  ZHIPU_MODEL       (选填，默认 glm-4-flash)
  ZHIPU_BASE_URL    (选填，默认 https://open.bigmodel.cn/api/paas/v4)
  [Gemini 兜底]
  GEMINI_API_KEY    (选填；未配 ZHIPU_API_KEY 时作为兜底，复用股票分析 Action 同名 secret)
  GEMINI_API_KEYS   (选填，多个 key 逗号分隔，429 限流时自动轮换)
  GEMINI_MODEL     (选填，默认 gemini-3-flash-preview，需支持 googleSearch grounding)
  GEMINI_BASE_URL  (选填，中转网关地址；默认官方 https://generativelanguage.googleapis.com/v1beta)
  [通用]
  PUSHPLUS_TOKEN    (选填，填了就推微信；不填则只落盘)
  PUSHPLUS_TOPIC    (选填，一对多群组编码；填了则推给整个群组而非仅 token 本人)
  POLICY_FORCE      (选填，任意非空则视为手动触发，即使非交易日也跑)
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request
import urllib.error

# ---------- 配置 ----------
SITES = [
    ("国家新闻出版署", "https://www.nppa.gov.cn"),
    ("国家发改委", "https://www.ndrc.gov.cn"),
    ("财政部", "https://www.mof.gov.cn"),
    ("工信部", "https://www.miit.gov.cn"),
    ("商务部", "https://www.mofcom.gov.cn"),
    ("中国人民银行", "https://www.pbc.gov.cn"),
    ("证监会", "https://www.csrc.gov.cn"),
    ("中央人民政府", "https://www.gov.cn"),
]

GEMINI_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
# 支持多个 key 轮换（环境变量 GEMINI_API_KEYS，逗号分隔），在 429 限流时自动切换
_KEYS_RAW = (os.environ.get("GEMINI_API_KEYS") or "").strip()
GEMINI_KEYS_LIST = [k.strip() for k in _KEYS_RAW.split(",") if k.strip()] if _KEYS_RAW else []
if not GEMINI_KEY and GEMINI_KEYS_LIST:
    GEMINI_KEY = GEMINI_KEYS_LIST[0]

PUSHPLUS_TOKEN = (os.environ.get("PUSHPLUS_TOKEN") or "").strip()
PUSHPLUS_TOPIC = (os.environ.get("PUSHPLUS_TOPIC") or "").strip()  # 一对多群组编码；不填则推给 token 本人
MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-3-flash-preview").strip()
# Gemini 接口基地址（中转网关可覆盖）。默认官方地址；地区受限无法直连时改为中转网关地址，
# 例如 https://你的网关域名/v1beta 。接口路径与官方一致，googleSearch 联网检索同样可用。
GEMINI_BASE_URL = (os.environ.get("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta").strip().rstrip("/")

# 智谱 GLM-4-Flash（免费 + 内置 web_search 实时联网检索）。优先于 Gemini 使用。
ZHIPU_KEY = (os.environ.get("ZHIPU_API_KEY") or "").strip()
ZHIPU_MODEL = (os.environ.get("ZHIPU_MODEL") or "glm-4-flash").strip()
ZHIPU_BASE_URL = (os.environ.get("ZHIPU_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4").strip().rstrip("/")

GEN_DATE = datetime.date.today().strftime("%Y-%m-%d")
GEN_DATE_CN = f"{GEN_DATE}（北京时间）"
NEXT_DATE = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def build_prompt() -> str:
    site_lines = "\n".join(f"{i}. {name}（官网：{url}）" for i, (name, url) in enumerate(SITES, 1))
    return f"""你是一位同时具备「资深交易员 / 宏观经济分析师 / 公司基本面研究员」三重视角的中国资本市场政策分析专家。今天是 {GEN_DATE_CN}。

请严格按以下结构产出一份中文 Markdown 报告（不要用代码块包裹，直接输出 Markdown 正文；**每条政策/事件务必附真实可点击的原文/来源链接，使用 [标题](URL) 格式，禁止虚构或占位链接**）：

# 每日政策扫描与宏观解读 · {GEN_DATE}

## 一、国内政策扫描与三视角解读
请使用联网搜索，逐一检索以下 8 个官方网站「今天（{GEN_DATE}）新发布/新出台」的政策、通知、公告、规划、征求意见稿（优先政策文件、要闻、新闻发布、政策解读栏目），抓取发布日期与原文链接：
{site_lines}

对每一条重要新政策，用 ### 小标题区分不同政策，并按以下字段逐条输出：
- 政策清单：标题 + 来源部门 + 发布日期 + 原文链接（用 Markdown 链接 [标题](真实URL)）
- 三视角解读：
  - **资深交易员视角**：哪些板块/题材短线、中线利好或利空？有无预期差？资金如何博弈？
  - **宏观经济分析师视角**：对总量经济、流动性、增长/通胀、信用周期的影响；逆周期 vs 顺周期？
  - **公司基本面研究员视角**：对哪些行业龙头/细分赛道的营收、利润率、资本开支、竞争格局有实质影响。
- 利好方向 / 利空方向 + 核心逻辑
- **影响资金规模**：估算该政策涉及或影响的资金体量（如专项资金规模、行业总市值、北向/公募潜在增配规模、相关 ETF 规模等），给出量级区间与依据
- 持续性：短期 / 中期 / 长期
若某网站当日无重大新政策，标注「无重大新政策」。

## 二、全球事件与跨市场资金面分析
使用联网搜索，检索**今天已发生或明天（{NEXT_DATE}）有可能发生**的以下四类信息（每条标注时间并附来源链接 [标题](URL)）：
- **国际事件**：地缘政治、主要经济体突发事件
- **科学论文 / 技术突破**：近期发表、对产业有潜在影响的研究
- **国际新闻**：全球宏观、市场、大宗商品相关的重要新闻
- **国际政策**：各国央行、政府新出台或拟出台的政策
分析这些外部变量对 A 股/港股/全球风险偏好的资金面影响（流入/流出、避险/risk-on），并判断持续性（一次性冲击 vs 趋势性变化）。

## 三、标的推荐
基于以上分析，给出最值得关注的 3-5 个方向/板块及代表性标的（附 A 股/港股代码与名称），说明推荐逻辑、催化时点与风险。**明确标注「非买卖建议，仅供研究参考」**。

## 四、明日关注清单
给出最值得跟踪的 3-5 个方向与对应触发信号。

约束：仅基于公开信息分析；区分「已落地政策」与「传闻/预期」；每条政策/事件必须附真实可点击的原文链接（[标题](URL) 格式）。"""


def call_gemini(prompt: str) -> str:
    if not GEMINI_KEY:
        raise RuntimeError("未配置 GEMINI_API_KEY，无法调用大模型。请在仓库 Secrets 中配置。")
    keys = GEMINI_KEYS_LIST if GEMINI_KEYS_LIST else [GEMINI_KEY]
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192, "topP": 0.95},
    }
    data = json.dumps(body).encode("utf-8")
    # 只调用配置的单个模型（MODEL），不再做多模型降级链——在免费档/限流时，
    # 多模型重试只会把本就紧张的额度快速打光。仅在 429 限流时做有限退避重试。
    last_err = None
    last_quota = False
    for ki, key in enumerate(keys):
        url = f"{GEMINI_BASE_URL}/models/{MODEL}:generateContent?key={key}"
        for attempt in range(1, 3):  # 最多 2 次尝试（含 1 次退避），省着用额度
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                parts = obj.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    print(f"[policy_scan] 模型 {MODEL}（key#{ki + 1}）生成成功")
                    return text
                raise RuntimeError("Gemini 返回空内容：" + json.dumps(obj, ensure_ascii=False)[:500])
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:800]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {detail}"
                print(f"[policy_scan] 模型 {MODEL}（key#{ki + 1}）第 {attempt} 次失败: HTTP {e.code} {detail}", file=sys.stderr)
                if e.code == 429:
                    # 配额/限流：标记后尝试下一个 key；若已是最后一个 key，则退避一次再试
                    last_quota = True
                    if ki < len(keys) - 1:
                        break
                    time.sleep(30)
                    continue
                if 400 <= e.code < 500:
                    # key 无效 / 模型不存在等客户端错误：重试无意义，直接报错
                    raise RuntimeError(
                        f"Gemini 客户端错误（请检查 GEMINI_API_KEY / GEMINI_MODEL / GEMINI_BASE_URL）: {last_err}"
                    )
                # 5xx 等服务端错误：退避后重试
                time.sleep(min(20, 5 * attempt))
                continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[policy_scan] 模型 {MODEL}（key#{ki + 1}）第 {attempt} 次失败: {e}", file=sys.stderr)
                time.sleep(min(20, 5 * attempt))
                continue
    hint = "（疑似 Gemini API 配额耗尽 429，请等待配额重置或更换/升级 API Key；免费档额度有限，建议改用 DeepSeek/硅基流动/智谱等免费渠道）" if last_quota else ""
    raise RuntimeError(f"Gemini 调用失败（模型 {MODEL}，key 数 {len(keys)}）: {last_err} {hint}")


def _zhipu_request(url: str, body: dict, key: str) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_zhipu(prompt: str) -> str:
    """调用智谱 GLM-4-Flash（OpenAI 兼容接口），使用其内置 web_search 工具做实时联网检索。"""
    if not ZHIPU_KEY:
        raise RuntimeError("未配置 ZHIPU_API_KEY，无法调用智谱模型。请在仓库 Secrets 中配置。")
    url = f"{ZHIPU_BASE_URL}/chat/completions"
    search_query = f"{GEN_DATE} 中国部委最新政策 全球宏观经济事件 国际新闻 资本市场影响 A股"
    # 内置 web_search 工具：让模型自行决定是否联网检索并综合结果
    body = {
        "model": ZHIPU_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search", "web_search": {"search_query": search_query}}],
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_tokens": 8000,
    }
    last_err = None
    last_quota = False
    for attempt in range(1, 4):  # 最多 3 次尝试，含 429 退避
        try:
            obj = _zhipu_request(url, body, ZHIPU_KEY)
            msg = (obj.get("choices") or [{}])[0].get("message", {})
            text = (msg.get("content") or "").strip()
            if not text and msg.get("tool_calls"):
                # 极少数情况下模型先返回 tool_calls：带工具结果再请求一次（退化兜底）
                body2 = {
                    "model": ZHIPU_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": None, "tool_calls": msg["tool_calls"]},
                        {"role": "tool", "content": "（已执行联网检索，请基于检索结果作答）", "tool_call_id": msg["tool_calls"][0].get("id", "1")},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 4000,
                }
                obj2 = _zhipu_request(url, body2, ZHIPU_KEY)
                text = (((obj2.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or "").strip()
            if text:
                print(f"[policy_scan] 智谱模型 {ZHIPU_MODEL} 生成成功（{len(text)} 字）")
                return text
            raise RuntimeError("智谱返回空内容：" + json.dumps(obj, ensure_ascii=False)[:500])
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {detail}"
            print(f"[policy_scan] 智谱第 {attempt} 次失败: HTTP {e.code} {detail}", file=sys.stderr)
            if e.code == 429:
                last_quota = True
                time.sleep(30)
                continue
            if 400 <= e.code < 500:
                raise RuntimeError(f"智谱客户端错误（请检查 ZHIPU_API_KEY / ZHIPU_MODEL / ZHIPU_BASE_URL）: {last_err}")
            time.sleep(min(20, 5 * attempt))
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[policy_scan] 智谱第 {attempt} 次失败: {e}", file=sys.stderr)
            time.sleep(min(20, 5 * attempt))
            continue
    hint = "（疑似智谱额度/限流 429，请稍后重试或检查账号额度）" if last_quota else ""
    raise RuntimeError(f"智谱调用失败（模型 {ZHIPU_MODEL}）: {last_err} {hint}")


def push_wechat(title: str, content: str) -> str:
    if not PUSHPLUS_TOKEN:
        return "跳过（未配置 PUSHPLUS_TOKEN）"
    url = "https://www.pushplus.plus/send"
    payload = {"token": PUSHPLUS_TOKEN, "title": title, "content": content}
    if PUSHPLUS_TOPIC:
        payload["topic"] = PUSHPLUS_TOPIC  # 一对多：推送给整个群组（含 hiChenYuYi）
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        if obj.get("code") == 200:
            return "成功"
        return f"失败(code={obj.get('code')}): {obj.get('msg')}"
    except Exception as e:  # noqa: BLE001
        return f"异常: {e}"


def _strip_code_fence(text: str) -> str:
    """去掉模型偶尔包裹的 ```markdown ... ``` 代码围栏。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首行 ``` 或 ```markdown
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        # 去掉结尾 ```
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _inline(text: str) -> str:
    """将 Markdown 行内语法转换为适合微信(手机)显示的 HTML。"""
    # 1) Markdown 链接 [文字](URL) -> 可点击超链接（原文可点进去）
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: (
            f'<a href="{m.group(2)}" '
            f'style="color:#1677ff;text-decoration:none;border-bottom:1px solid #1677ff;'
            f'word-break:break-all;">{m.group(1)}</a>'
        ),
        text,
    )
    # 2) 裸 URL -> 超链接（避免长链接撑破手机屏宽；跳过已写入 href=" 的链接）
    def _bare(m):
        pre, url = m.group(1), m.group(2)
        if pre in ('"', ']'):
            return m.group(0)
        a = (
            f'<a href="{url}" '
            f'style="color:#1677ff;text-decoration:none;border-bottom:1px solid #1677ff;'
            f'word-break:break-all;">{url}</a>'
        )
        return (pre or "") + a

    text = re.sub(r"(.)?(https?://[^\s<)]+)", _bare, text)
    # 3) 加粗 **x** -> 强调
    text = re.sub(r"\*\*(.+?)\*\*", r'<strong style="font-weight:700;color:#222;">\1</strong>', text)
    # 4) 行内代码 `x`
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#f2f2f2;padding:1px 4px;border-radius:3px;'
        r'font-size:13px;color:#c0392b;">\1</code>',
        text,
    )
    return text


def markdown_to_html(md: str) -> str:
    """将 Markdown 报告渲染为 iPhone 友好的 HTML（PushPlus 用 HTML 渲染）。"""
    md = _strip_code_fence(md)
    lines = md.splitlines()
    out = []
    list_type = None
    list_items = []

    def flush_list():
        nonlocal list_type, list_items
        if list_type and list_items:
            tag = "ul" if list_type == "ul" else "ol"
            lis = "".join(f'<li style="margin:4px 0;">{it}</li>' for it in list_items)
            out.append(f'<{tag} style="margin:6px 0;padding-left:20px;">{lis}</{tag}>')
        list_type, list_items = None, []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_list()
            continue
        # 分隔线
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            flush_list()
            out.append('<hr style="border:none;border-top:1px solid #eee;margin:12px 0;">')
            continue
        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_list()
            level = len(m.group(1))
            txt = _inline(m.group(2))
            if level == 1:
                out.append(f'<h1 style="font-size:20px;color:#d4380d;font-weight:700;border-bottom:2px solid #d4380d;padding-bottom:6px;margin:18px 0 10px;">{txt}</h1>')
            elif level == 2:
                out.append(f'<h2 style="font-size:17px;color:#1f1f1f;font-weight:700;border-left:4px solid #d4380d;padding-left:8px;margin:16px 0 8px;">{txt}</h2>')
            elif level == 3:
                out.append(f'<h3 style="font-size:15.5px;color:#d4380d;font-weight:700;margin:12px 0 6px;">{txt}</h3>')
            else:
                out.append(f'<h4 style="font-size:14.5px;color:#444;font-weight:700;margin:10px 0 4px;">{txt}</h4>')
            continue
        # 引用
        if line.lstrip().startswith(">"):
            flush_list()
            out.append(f'<div style="border-left:3px solid #d9d9d9;padding:4px 10px;color:#666;margin:6px 0;">{_inline(line.lstrip()[1:].strip())}</div>')
            continue
        # 无序列表
        lm = re.match(r"^\s*[-*]\s+(.*)$", line)
        olm = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if lm:
            if list_type != "ul":
                flush_list()
                list_type = "ul"
            list_items.append(_inline(lm.group(1)))
            continue
        if olm:
            if list_type != "ol":
                flush_list()
                list_type = "ol"
            list_items.append(_inline(olm.group(1)))
            continue
        # 段落
        flush_list()
        out.append(f'<p style="margin:6px 0;">{_inline(line)}</p>')

    flush_list()
    body = "".join(out)
    wrapper = (
        '<div style="max-width:100%;font-family:-apple-system,BlinkMacSystemFont,'
        '&quot;PingFang SC&quot;,&quot;Helvetica Neue&quot;,Arial,sans-serif;'
        'font-size:15px;line-height:1.7;color:#1f1f1f;word-break:break-word;'
        'padding:2px 4px;">'
        f"{body}"
        "</div>"
    )
    return wrapper


def make_summary(report: str) -> str:
    """兼容保留：返回整篇报告的手机 HTML（含全部章节），超长则截断保护。"""
    html = markdown_to_html(report)
    if len(html) > 40000:
        html = html[:39000] + '<p style="color:#999;margin:6px 0;">（内容较长，已截断，完整版见仓库 policy_digest 目录）</p></div>'
    return html


def main() -> int:
    if ZHIPU_KEY:
        provider, model_name = "智谱 GLM-4-Flash", ZHIPU_MODEL
    elif GEMINI_KEY:
        provider, model_name = "Gemini", MODEL
    else:
        print("[policy_scan] 未配置 ZHIPU_API_KEY 或 GEMINI_API_KEY，无法生成报告。", file=sys.stderr)
        return 1
    print(f"[policy_scan] 开始生成 {GEN_DATE} 政策扫描报告，提供方={provider}，模型={model_name}")
    try:
        if ZHIPU_KEY:
            try:
                report = call_zhipu(build_prompt())
            except Exception as e:  # noqa: BLE001
                if GEMINI_KEY:
                    print(f"[policy_scan] 智谱调用失败，回退 Gemini: {e}", file=sys.stderr)
                    report = call_gemini(build_prompt())
                else:
                    raise
        else:
            report = call_gemini(build_prompt())
    except Exception as e:  # noqa: BLE001
        print(f"[policy_scan] 生成失败: {e}", file=sys.stderr)
        return 1

    out_dir = "policy_digest"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{GEN_DATE}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"[policy_scan] 报告已写入 {out_path}（{len(report)} 字）")

    # 推送微信（精简摘要）
    status = push_wechat(f"每日政策扫描与宏观解读 {GEN_DATE}", make_summary(report))
    print(f"[policy_scan] 微信推送：{status}")

    # 尽量把报告提交回仓库（best-effort，失败不影响主流程）
    try:
        import subprocess
        subprocess.run(["git", "config", "user.email", "action@github.com"], check=False)
        subprocess.run(["git", "config", "user.name", "github-actions"], check=False)
        subprocess.run(["git", "add", out_path], check=False)
        res = subprocess.run(
            ["git", "commit", "-m", f"docs(policy): 添加 {GEN_DATE} 政策扫描报告"],
            capture_output=True, text=True, check=False,
        )
        if res.returncode == 0:
            subprocess.run(["git", "push"], capture_output=True, text=True, check=False)
            print("[policy_scan] 报告已提交并推送至仓库")
        else:
            print("[policy_scan] 无需提交或提交跳过（可能无变更/无推送权限）")
    except Exception as e:  # noqa: BLE001
        print(f"[policy_scan] 报告提交仓库失败（已忽略）: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
