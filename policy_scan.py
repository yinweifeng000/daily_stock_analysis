#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日政策扫描与宏观解读 (GitHub Action 版)
================================================
- 使用 Gemini + Google Search 实时检索 8 大部委官网政策 + 全球事件/科学论文/国际政策
- 三视角深度解读：资深交易员 / 宏观经济分析师 / 公司基本面研究员
- 输出落盘 policy_digest/YYYY-MM-DD.md
- 精简摘要推送微信 (PushPlus)

依赖：仅标准库 + requests（已在仓库 requirements.txt 中）
环境变量：
  GEMINI_API_KEY    (必填，复用股票分析 Action 的同名 secret)
  PUSHPLUS_TOKEN    (选填，填了就推微信；不填则只落盘)
  PUSHPLUS_TOPIC    (选填，一对多群组编码；填了则推给整个群组而非仅 token 本人)
  GEMINI_MODEL     (选填，默认 gemini-2.5-flash，需支持 googleSearch grounding)
  POLICY_FORCE      (选填，任意非空则视为手动触发，即使非交易日也跑)
"""

import os
import sys
import json
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
if not GEMINI_KEY:
    _keys = os.environ.get("GEMINI_API_KEYS", "").strip()
    if _keys:
        GEMINI_KEY = _keys.split(",")[0].strip()

PUSHPLUS_TOKEN = (os.environ.get("PUSHPLUS_TOKEN") or "").strip()
PUSHPLUS_TOPIC = (os.environ.get("PUSHPLUS_TOPIC") or "").strip()  # 一对多群组编码；不填则推给 token 本人
MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash").strip()

GEN_DATE = datetime.date.today().strftime("%Y-%m-%d")
GEN_DATE_CN = f"{GEN_DATE}（北京时间）"


def build_prompt() -> str:
    site_lines = "\n".join(f"{i}. {name}（官网：{url}）" for i, (name, url) in enumerate(SITES, 1))
    return f"""你是一位同时具备「资深交易员 / 宏观经济分析师 / 公司基本面研究员」三重视角的中国资本市场政策分析专家。今天是 {GEN_DATE_CN}。

请严格按以下结构产出一份中文 Markdown 报告（不要用代码块包裹，直接输出 Markdown 正文）：

# 每日政策扫描与宏观解读 · {GEN_DATE}

## 一、国内政策扫描与三视角解读
请使用 Google 搜索，逐一检索以下 8 个官方网站「今天（{GEN_DATE}）新发布/新出台」的政策、通知、公告、规划、征求意见稿，优先看政策文件、要闻、新闻发布、政策解读栏目，抓取发布日期与原文链接：
{site_lines}

对每一条重要新政策，做深度解读，必须同时覆盖三个视角：
1. **资深交易员视角**：哪些板块/题材短线、中线利好或利空？市场是否有预期差？资金可能如何博弈？
2. **宏观经济分析师视角**：对总量经济、流动性、增长/通胀、信用周期的影响；属于逆周期还是顺周期调节？
3. **公司基本面研究员视角**：对哪些行业龙头/细分赛道的营收、利润率、资本开支、竞争格局产生实质影响。

输出结构：政策清单（标题 + 来源 + 发布日期 + 链接）+ 逐条「利好方向 / 利空方向 + 核心逻辑 + 持续性（短期/中期/长期）」。若某网站当日无重大新政策，明确标注「无重大新政策」。

## 二、全球事件与跨市场资金面分析
使用 Google 搜索检索今日国际新闻、国际政策、地缘政治事件、重要科学论文/技术突破。分析这些外部变量对 A 股/港股/全球风险偏好的资金面影响（流入/流出、避险/risk-on），并判断持续性（一次性冲击 vs 趋势性变化）。

## 三、明日关注清单
给出最值得跟踪的 3-5 个方向与对应触发信号。

约束：仅基于公开信息分析，不提供个股买卖建议；区分「已落地政策」与「传闻/预期」。"""


def call_gemini(prompt: str) -> str:
    if not GEMINI_KEY:
        raise RuntimeError("未配置 GEMINI_API_KEY，无法调用大模型。请在仓库 Secrets 中配置。")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_KEY}"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192, "topP": 0.95},
    }
    data = json.dumps(body).encode("utf-8")
    last_err = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            parts = obj.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
            raise RuntimeError("Gemini 返回空内容：" + json.dumps(obj, ensure_ascii=False)[:500])
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[policy_scan] Gemini 调用第 {attempt} 次失败: {e}", file=sys.stderr)
    raise RuntimeError(f"Gemini 调用失败: {last_err}")


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


def make_summary(report: str) -> str:
    """从完整报告中抽取政策清单 + 利好/利空 + 明日关注，作为微信精简摘要。"""
    lines = report.splitlines()
    keep, in_global = [], False
    scan_section, watch = [], []
    capture_scan = False
    for ln in lines:
        if ln.strip().startswith("## 一"):
            capture_scan = True
            scan_section.append(ln)
        elif ln.strip().startswith("## 二"):
            capture_scan = False
            in_global = True
        elif ln.strip().startswith("## 三"):
            in_global = False
            watch.append(ln)
        elif capture_scan:
            scan_section.append(ln)
        elif ln.strip().startswith(("-", "*")) and ("关注" in ln or watch):
            watch.append(ln)
        elif in_global and ln.strip().startswith(("-", "*")):
            # 全球部分仅保留带方向的要点
            if any(k in ln for k in ("利好", "利空", "资金", "risk", "流入", "流出")):
                watch.append(ln)
    summary = "\n".join(scan_section[:60])
    if watch:
        summary += "\n\n## 明日关注（节选）\n" + "\n".join(watch[:15])
    return summary or report[:1500]


def main() -> int:
    print(f"[policy_scan] 开始生成 {GEN_DATE} 政策扫描报告，模型={MODEL}")
    try:
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
