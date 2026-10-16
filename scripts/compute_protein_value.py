#!/usr/bin/env python3
"""
麦门显微镜 · 蛋白质性价比计算器

拉取指定门店的菜单价格 + 官方营养表，算出「每 1 元能买到多少克蛋白质」的排行。
同时给出每 100 kcal 的蛋白密度、钠含量，供减脂 / 增肌两种目标分别参考。

用法:
    python3 compute_protein_value.py [storeCode]
    python3 compute_protein_value.py 1420227 --json out.json

Token 从 ~/.workbuddy/mcp.json 读取，不硬编码、不入库。
"""

import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

MCP_URL = "https://mcp.mcd.cn"
DEFAULT_STORE = "1420227"  # 麦当劳深圳北站站内一店

USAGE = """麦门显微镜 · 蛋白质性价比计算器

用法:
    python3 compute_protein_value.py [storeCode] [--density] [--json out.json]

参数:
    storeCode     门店编码，默认 1420227（麦当劳深圳北站站内一店）
                  可用 mcd-mcp 的 query-nearby-stores 查任意门店编码
    --density     改按「g 蛋白 / 100kcal」排序（减脂视角），默认按「g 蛋白 / 元」
    --json PATH   把完整结果另存为 JSON，便于换门店对比或做图
    -h, --help    显示本帮助

Token 从 ~/.workbuddy/mcp.json 读取，不硬编码、不入库。
"""


def _make_ssl_context() -> ssl.SSLContext:
    """构造 SSL 上下文。

    macOS 上 python.org 安装的 Python 默认没有根证书，直接请求会报
    CERTIFICATE_VERIFY_FAILED。优先用 certifi 的 CA bundle；没有 certifi
    再退回系统默认；两者都不可用时给出明确指引，而不是抛一堆栈。
    """
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        ctx = ssl.create_default_context()
        ctx.load_default_certs()
        return ctx
    except Exception:
        raise SystemExit(
            "SSL 根证书不可用。macOS 上请二选一：\n"
            "  1) pip install certifi\n"
            "  2) 双击 /Applications/Python 3.x/Install Certificates.command"
        )


SSL_CTX = _make_ssl_context()


# ---------- MCP 调用 ----------

def load_token() -> str:
    candidates = [
        Path.home() / ".workbuddy" / "mcp.json",
        Path.home() / ".codebuddy" / "mcp.json",
        Path.home() / ".cursor" / "mcp.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for srv in (cfg.get("mcpServers") or {}).values():
            auth = (srv.get("headers") or {}).get("Authorization", "")
            if isinstance(auth, str) and auth.lower().startswith("bearer "):
                return auth[7:].strip()
    raise SystemExit("未找到 MCP Token：请检查 ~/.workbuddy/mcp.json 里的 Authorization 字段")


_TOKEN = None


def get_token() -> str:
    """懒加载 Token —— 这样 --help 在没有配置 token 时也能正常显示。"""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = load_token()
    return _TOKEN


def rpc(method: str, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + get_token())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    try:
        with urllib.request.urlopen(req, timeout=45, context=SSL_CTX) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"HTTP {e.code} — {detail}")
    if "data:" in raw:
        raw = "\n".join(
            line[5:].strip() for line in raw.splitlines() if line.strip().startswith("data:")
        )
    return json.loads(raw)


def extract_json(text: str):
    """工具返回的是「说明文本 + 原始 JSON」的混合体，需要把 JSON 抠出来。

    返回第一个形如 {"code": ...} 的完整 JSON 对象；抠不到则返回 None
    （例如 campaign-calendar 返回的是排版好的富文本，本身就不是 JSON）。
    """
    dec = json.JSONDecoder()
    anchor = text.find("## Original Response")
    pos = anchor + len("## Original Response") if anchor >= 0 else 0
    while pos < len(text):
        p = text.find("{", pos)
        if p < 0:
            break
        try:
            obj, end = dec.raw_decode(text, p)
        except json.JSONDecodeError:
            pos = p + 1
            continue
        if isinstance(obj, dict) and "code" in obj:
            return obj
        pos = end
    return None


def call_tool(name: str, arguments=None):
    resp = rpc("tools/call", {"name": name, "arguments": arguments or {}})
    if "error" in resp:
        raise SystemExit(f"工具 {name} 调用失败：{resp['error']}")
    content = resp.get("result", {}).get("content", [])
    text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    obj = extract_json(text)
    return obj if obj is not None else text


# ---------- 营养表解析 ----------

def parse_nutrition(raw) -> list:
    """把 list-nutrition-foods 的类 CSV 文本解析成结构化列表。"""
    payload = raw
    if isinstance(payload, dict):
        payload = payload.get("data", "")
    if not isinstance(payload, str):
        return []

    rows = []
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        # 末 7 位固定是数值，index 1..-8 合并为描述
        nums = parts[-7:]
        desc = ",".join(parts[1:-7]).strip() or None
        try:
            rows.append({
                "name": parts[0],
                "desc": None if desc in (None, "null", "") else desc,
                "kj": float(nums[0]),
                "kcal": float(nums[1]),
                "protein": float(nums[2]),
                "fat": float(nums[3]),
                "carb": float(nums[4]),
                "sodium": float(nums[5]),
                "calcium": float(nums[6]),
            })
        except ValueError:
            continue
    return rows


# ---------- 名称归一化与匹配 ----------

# 名称里含这些词的一律视为「套餐 / 组合装」，不进入单品排行。
# 注意：不能只靠 tags 判断 —— 实测有多个套餐的 tags 里并没有「套餐」标记
# （如「牛气满满套餐」「苹板支撑Pro套餐」在 500 大卡分类下 tags 为空）。
MEAL_HINTS = ("套餐", "件套", "双人餐", "乐园餐", "亲子", "随心", "精选", "拼盘", "件任选")


def norm(name: str) -> str:
    s = re.sub(r"[（(][^）)]*[）)]", "", name)      # 去括号内容
    s = re.sub(r"[\s·•\-—/、]", "", s)
    return s.upper()


def build_index(nutrition_rows: list) -> dict:
    return {norm(r["name"]): r for r in nutrition_rows}


def match(name: str, index: dict):
    """返回 (营养项, 匹配方式, 候选名列表)。

    匹配方式：
      exact      —— 归一化后完全相同
      fuzzy      —— 双向包含，且候选唯一
      ambiguous  —— 双向包含但候选不唯一。典型场景：菜单只有一个「麦乐鸡」，
                    营养表却有「麦乐鸡5块」「麦乐鸡4块」。份数未知时不可臆测，
                    此类一律不参与排行，交人工核对。
      miss       —— 完全找不到
    """
    key = norm(name)
    if key in index:
        return index[key], "exact", [index[key]["name"]]
    hits = [(k, v) for k, v in index.items() if key in k or k in key]
    if not hits:
        return None, "miss", []
    if len(hits) > 1:
        hits.sort(key=lambda kv: len(kv[0]))
        return hits[0][1], "ambiguous", [v["name"] for _, v in hits]
    return hits[0][1], "fuzzy", [hits[0][1]["name"]]


# ---------- 主流程 ----------

def main():
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        return
    store, json_out, positional = DEFAULT_STORE, None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            if i + 1 >= len(argv):
                raise SystemExit("--json 需要跟一个输出路径")
            json_out = argv[i + 1]
            i += 2
        elif a in ("--density",):
            i += 1
        elif a.startswith("--"):
            raise SystemExit(f"未知参数：{a}\n\n{USAGE}")
        else:
            positional.append(a)
            i += 1
    if positional:
        store = positional[0]

    print(f"门店 storeCode = {store}")
    meals_raw = call_tool("query-meals", {"storeCode": store, "orderType": 1, "beType": 1})
    nutr_raw = call_tool("list-nutrition-foods", {})

    if not isinstance(meals_raw, dict) or not meals_raw.get("data"):
        raise SystemExit(f"菜单拉取失败：{str(meals_raw)[:300]}")

    data = meals_raw["data"]
    meal_map = data.get("meals") or {}

    tags_by_code = {}
    for cat in data.get("categories") or []:
        for m in cat.get("meals") or []:
            tags_by_code.setdefault(m.get("code"), set()).update(m.get("tags") or [])

    nutrition = parse_nutrition(nutr_raw)
    index = build_index(nutrition)
    print(f"营养表 {len(nutrition)} 项 · 菜单 {len(meal_map)} 个餐品\n")

    singles, unresolved, ambiguous = [], [], []
    for code, info in meal_map.items():
        tags = tags_by_code.get(code, set())
        name = info.get("name") or ""
        if "套餐" in tags or any(h in name for h in MEAL_HINTS):
            continue
        try:
            price = float(info.get("currentPrice") or 0)
        except ValueError:
            continue
        if price <= 0:
            continue

        nut, how, cands = match(name, index)
        if not nut:
            unresolved.append(name)
            continue
        if how == "ambiguous":
            ambiguous.append({"name": name, "candidates": cands})
            continue

        prot = nut["protein"]
        kcal = nut["kcal"]
        singles.append({
            "code": code,
            "name": name,
            "price": price,
            "protein": prot,
            "kcal": kcal,
            "fat": nut["fat"],
            "carb": nut["carb"],
            "sodium": nut["sodium"],
            "desc": nut.get("desc"),
            "match": how,
            "protein_per_yuan": round(prot / price, 3) if price else 0,
            "protein_per_100kcal": round(prot / kcal * 100, 1) if kcal else 0,
            "kcal_per_yuan": round(kcal / price, 1) if price else 0,
        })

    by_density = "--density" in argv
    sort_key = "protein_per_100kcal" if by_density else "protein_per_yuan"
    singles.sort(key=lambda x: x[sort_key], reverse=True)
    if by_density:
        print("视图：按蛋白密度排序（g 蛋白 / 100kcal）—— 减脂视角")
        print("提示：只看密度会被「热量极低但蛋白也极少」的项蒙蔽，请结合绝对值判断。\n")

    print("=" * 74)
    print(f"{'餐品':<20}{'价格':>7}{'蛋白':>7}{'kcal':>7}{'钠mg':>7}{'g蛋白/元':>10}{'g蛋白/100kcal':>15}")
    print("-" * 74)
    for s in singles:
        print(
            f"{s['name'][:19]:<20}{s['price']:>7.1f}{s['protein']:>7.0f}{s['kcal']:>7.0f}"
            f"{s['sodium']:>7.0f}{s['protein_per_yuan']:>10.3f}{s['protein_per_100kcal']:>15.1f}"
        )

    if ambiguous:
        print("\n规格 / 份数不明确，已排除（不臆测；如需使用请指定具体规格）：")
        for a in ambiguous:
            cands = a["candidates"]
            shown = " / ".join(cands[:6])
            more = f" …等 {len(cands)} 项" if len(cands) > 6 else ""
            print(f"  - {a['name']}  ← {shown}{more}")

    if unresolved:
        print("\n未匹配到营养数据的餐品（需人工核对，勿臆测）：")
        for n in unresolved:
            print("  -", n)

    if json_out:
        Path(json_out).write_text(
            json.dumps(
                {"storeCode": store, "count": len(singles), "items": singles,
                 "unresolved": unresolved, "ambiguous": ambiguous},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n已写出：{json_out}")


if __name__ == "__main__":
    main()
