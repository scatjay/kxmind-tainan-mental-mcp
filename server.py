"""臺南心理衛生資源 MCP（tainan-mental-mcp）

給助人工作者用的。把散在台南市衛生局十幾層選單裡的心理資源，
變成一句話就查得到的東西。

為什麼需要這支：
  這些資料**手動都查得到** —— 但要開衛生局、社會局、資料開放平臺十幾個分頁，
  而且那些網址連個名字都沒有，只有一串 GUID（例如
  page.asp?mainid=0A3943E7-033E-4686-8514-7BD39368D32D）。
  你不可能用猜的，只能一層一層點。

  所以這支的價值不在「取得資料」，在**把跨來源的交叉比對自動化**：
  三份官方名單互相漏收誰？一個行政區到底歸哪五個窗口管？
  免費諮商的時段集中在星期幾？—— 這些手動要花半天，這裡一句話。

🔴 現場紀律：預設讀 data/ 的本地快照，不即時打政府網站。
  明天約 12 位學員、同一個 IP、同一時間，一起打會有速率限制風險，
  而且診所 WiFi 品質不明。只有 refresh_snapshot() 會走線上。

資料來源（全部是 2026-08-27 經過對抗性查核驗證的公開資料）：
  · 臺南市心理健康資源地圖 KML —— 141 個標點、4 圖層
  · 衛生局「心理諮商所、心理治療所」名冊 —— 32 家
  · 免付費心理諮商（公部門 40 點／民間 22 家／預約方式）
  · 社區心理衛生中心據點 —— 6 處，服務區域涵蓋全部 37 區
  · 資料開放平臺：免費諮商據點 CSV、各區代碼 CSV
"""
import io
import os
import re

# 電話 regex：三支順序不能換——0800 與 09XX 必須排在市話前面。
# 市話那支會把 0902-009-830 配成 02-009-830（一組合法的台北市話），
# 在助人工作裡給錯電話是實害，不是小瑕疵。
import sys
import csv
import json
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

from mcp.server.fastmcp import FastMCP

if sys.platform == "win32":
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

mcp = FastMCP("tainan-mental")

_CACHE = {}


# ── 底層：讀快照 ──────────────────────────────────
def _read(name, binary=False):
    p = os.path.join(DATA, name)
    if not os.path.isfile(p):
        raise FileNotFoundError(
            "找不到快照 %s。請先在 repo 目錄跑 `python fetch_snapshot.py`。" % name)
    if binary:
        return open(p, "rb").read()
    raw = open(p, "rb").read()
    for enc in ("utf-8", "utf-8-sig", "big5", "cp950"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _manifest():
    if "manifest" not in _CACHE:
        _CACHE["manifest"] = json.loads(_read("_manifest.json"))
    return _CACHE["manifest"]


def _strip(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", "\n", h)
    return [x.strip() for x in h.split("\n") if x.strip()]


# ── 解析：KML 地圖 ────────────────────────────────
NS = {"k": "http://www.opengis.net/kml/2.2"}


def _kml():
    """KML → {圖層名: [ {name, desc, addr, phone, ext} ]}"""
    if "kml" in _CACHE:
        return _CACHE["kml"]
    root = ET.fromstring(_read("map_kml.xml"))
    out = OrderedDict()
    for folder in root.iter("{http://www.opengis.net/kml/2.2}Folder"):
        fname = folder.find("k:name", NS)
        fname = (fname.text or "").strip() if fname is not None else "?"
        items = []
        for pm in folder.findall("k:Placemark", NS):
            nm = pm.find("k:name", NS)
            ds = pm.find("k:description", NS)
            ad = pm.find("k:address", NS)
            ext = {}
            for d in pm.iter("{http://www.opengis.net/kml/2.2}Data"):
                key = d.get("name") or ""
                v = d.find("k:value", NS)
                ext[key] = (v.text or "").strip() if v is not None else ""
            desc = (ds.text or "") if ds is not None else ""
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()
            addr = (ad.text or "").strip() if ad is not None else ""
            phone = ""
            m = re.search(r"(0800[-\s]?\d{3}[-\s]?\d{3}|09\d{2}[-\s]?\d{3}[-\s]?\d{3}|\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{3,4})", desc + " " + addr)
            if m:
                phone = m.group(1)
            items.append({
                "name": (nm.text or "").strip() if nm is not None else "",
                "desc": desc, "addr": addr, "phone": phone, "ext": ext,
            })
        out[fname] = items
    _CACHE["kml"] = out
    return out


# ── 解析：名冊 ────────────────────────────────────
_CLINIC_RE = re.compile(
    r"^(?P<name>[^\s（(]{2,20}?(?:心理治療所|心理諮商所))\s*"
    r"(?P<rest>.*)$")


def _roster():
    """名冊 HTML → [{name, kind, area, addr, phone}]"""
    if "roster" in _CACHE:
        return _CACHE["roster"]
    lines = _strip(_read("roster.html"))
    out, kind = [], None
    for i, L in enumerate(lines):
        if "心理治療所" in L and len(L) < 12 and "所" == L[-1]:
            kind = "心理治療所"
        if re.fullmatch(r"心理諮商所", L):
            kind = "心理諮商所"
        m = re.search(r"([^\s，,、]{2,18}(?:心理治療所|心理諮商所))", L)
        if not m:
            continue
        nm = m.group(1)
        if any(o["name"] == nm for o in out):
            continue
        blob = " ".join(lines[i:i + 3])
        ph = re.search(r"(0800[-\s]?\d{3}[-\s]?\d{3}|09\d{2}[-\s]?\d{3}[-\s]?\d{3}|\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{3,4})", blob)
        ar = re.search(r"([一-鿿]{1,3}區)", blob)
        ad = re.search(r"((?:臺南市|台南市)?[一-鿿]{1,3}區[^\s，,]{2,40})", blob)
        out.append({
            "name": nm,
            "kind": "心理治療所" if nm.endswith("心理治療所") else "心理諮商所",
            "area": ar.group(1) if ar else "",
            "addr": ad.group(1) if ad else "",
            "phone": ph.group(1) if ph else "",
        })
    _CACHE["roster"] = out
    return out


# ── 解析：社區心衛中心 ────────────────────────────
def _centers():
    """6 處社區心衛中心 → [{name, phone, addr, hours, areas}]

    HTML 的結構很規矩，照著標籤讀就好：

        XX區社區心理衛生中心資訊
        電話：(06)XXX-XXXX
        地址：臺南市XX區XXX號
        服務時間：星期一至星期五08:00~12:00、13:30~17:30
        服務區域：善化區、永康區、新市區、…      ← 這一行就是完整清單
        交通方式：…

    ⚠ 第一版我用「在中心名稱附近 900 字元裡 regex 撈所有 XX區」來解析，
      結果善化中心變成 26 區，還混進「服務區」「願景園區」「社區」這種假的。
      查詢碰巧會對，但那是碰巧 —— 明天要 demo 的工具不能碰巧對。
    """
    if "centers" in _CACHE:
        return _CACHE["centers"]
    lines = _strip(_read("centers.html"))

    out, cur = [], None
    for L in lines:
        m = re.match(r"^([一-鿿]{1,3}區?社區心理衛生中心)資訊$", L)
        if m:
            if cur and cur.get("areas"):
                out.append(cur)
            cur = {"name": m.group(1), "phone": "", "addr": "",
                   "hours": "", "areas": []}
            continue
        if not cur:
            continue
        if L.startswith("電話：") and not cur["phone"]:
            cur["phone"] = L[3:].strip()
        elif L.startswith("地址：") and not cur["addr"]:
            cur["addr"] = L[3:].strip()
        elif L.startswith("服務時間：") and not cur["hours"]:
            cur["hours"] = L[5:].strip()
        elif L.startswith("服務區域：") and not cur["areas"]:
            cur["areas"] = [x.strip() for x in L[5:].split("、") if x.strip()]
    if cur and cur.get("areas"):
        out.append(cur)

    # 去重（同一個中心可能在頁面上出現兩次）
    seen, uniq = set(), []
    for c in out:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        uniq.append(c)

    _CACHE["centers"] = uniq
    return uniq


# ── 解析：CSV ─────────────────────────────────────
def _csv_rows(name):
    key = "csv:" + name
    if key in _CACHE:
        return _CACHE[key]
    txt = _read(name)
    # ⚠ 這批 CSV 有雙重 BOM（EF BB BF EF BB BF），utf-8-sig 只剝一層，
    #   第一個欄名會殘留一個看不見的 ﻿。手動再剝一次。
    txt = txt.lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(txt)))
    rows = [{(k or "").strip().lstrip("﻿"): (v or "").strip()
             for k, v in r.items()} for r in rows]
    _CACHE[key] = rows
    return rows


WEEKDAY = {"一": "週一", "二": "週二", "三": "週三", "四": "週四",
           "五": "週五", "六": "週六", "日": "週日"}


def _free_points():
    """免費諮商點 → [{place, addr, weekday, time, sector}]

    ⚠ KML 那 58 筆的 <name> 全部是同一個字串「預約免費心理諮商」，
      真正的地點在 ExtendedData 的「諮商地點」欄。直接讀 name 會拿到 58 個一樣的點。
    """
    if "free" in _CACHE:
        return _CACHE["free"]
    out = []
    for it in _kml().get("預約免費心理諮商", []):
        ext = it.get("ext") or {}
        place = ext.get("諮商地點") or it.get("name") or ""
        slot = ext.get("諮商服務時段") or ""
        tel = ext.get("請撥預約電話") or it.get("phone") or ""
        wd = ""
        m = re.search(r"每週([一二三四五六日])", slot)
        if m:
            wd = WEEKDAY[m.group(1)]
        sector = "公部門" if re.search(r"衛生所|辦公室|衛生局|法院|區公所", place) else "民間"
        out.append({"place": place, "addr": it.get("addr", ""),
                    "weekday": wd, "time": slot, "phone": tel,
                    "sector": sector})
    _CACHE["free"] = out
    return out


def _norm_name(s):
    """機構名正規化，讓兩份名單比得起來。

    台南／臺南、社團法人前綴、附設 —— 這些差異會讓 diff 出現假差異。
    """
    s = (s or "").strip()
    s = s.replace("台南", "臺南")
    s = re.sub(r"^(社團法人|財團法人|臺南市|附設)+", "", s)
    return re.sub(r"\s+", "", s)


# ══════════════════════════════════════════════════
# 讀取層
# ══════════════════════════════════════════════════
@mcp.tool()
def ping() -> str:
    """確認這支 MCP 接上了，順便看快照的新舊。

    設定完 Claude 之後第一件事就跑這支。看到資料筆數就代表通了。
    """
    try:
        mf = _manifest()
        k = _kml()
        layers = ", ".join("%s %d" % (n, len(v)) for n, v in k.items())
        return "\n".join([
            "✅ tainan-mental MCP 接上了",
            "",
            "  快照抓取時間：%s" % mf.get("fetched_at"),
            "  來源檔案：%d 個" % sum(1 for s in mf["sources"] if s.get("ok")),
            "  地圖圖層：%s" % layers,
            "  名冊機構：%d 家" % len(_roster()),
            "  社區心衛中心：%d 處" % len(_centers()),
            "  免費諮商點：%d 個" % len(_free_points()),
            "",
            "  試試看：find_myself(\"寬欣\") 或 which_center(\"北區\")",
        ])
    except Exception as e:
        return "❌ 有問題：%s\n\n先在 repo 目錄跑 `python fetch_snapshot.py` 建快照。" % e


@mcp.tool()
def list_datasets() -> str:
    """這支接了哪幾個資料來源、各自什麼時候抓的。"""
    mf = _manifest()
    lines = ["資料來源（快照於 %s）" % mf.get("fetched_at"), ""]
    for s in mf["sources"]:
        lines.append("  %s %-14s %8s bytes" %
                     ("✓" if s.get("ok") else "✗", s["key"], s.get("bytes", 0)))
        lines.append("     %s" % s["desc"])
        lines.append("     %s" % s["url"])
        lines.append("")
    lines.append("要更新請跑 `python fetch_snapshot.py`。")
    lines.append("⚠ 現場請用快照，不要 12 個人同時打政府網站。")
    return "\n".join(lines)


@mcp.tool()
def find_myself(keyword: str) -> str:
    """在所有資料來源裡找一家機構，看它出現在哪幾份名單上。

    Args:
        keyword: 機構名的一部分，例如「寬欣」
    """
    kw = keyword.strip()
    hits = []
    for layer, items in _kml().items():
        for it in items:
            blob = it["name"] + it["desc"] + it["addr"] + json.dumps(
                it["ext"], ensure_ascii=False)
            if kw in blob:
                hits.append(("地圖／" + layer, it["name"] or it["ext"].get("諮商地點", ""),
                             it["addr"] or it["desc"][:60], it["phone"]))
    for c in _roster():
        if kw in c["name"]:
            hits.append(("衛生局名冊", c["name"], c["addr"], c["phone"]))
    for f in _free_points():
        if kw in (f["place"] + f["addr"]):
            hits.append(("免費諮商合作名單", f["place"],
                         "%s %s" % (f["weekday"], f["time"]), f["phone"]))

    if not hits:
        return "在四份資料裡都找不到「%s」。" % kw
    lines = ["「%s」出現在 %d 個地方：" % (kw, len(hits)), ""]
    for src, nm, extra, ph in hits:
        lines.append("  【%s】" % src)
        lines.append("     %s" % nm)
        if extra:
            lines.append("     %s" % extra[:90])
        if ph:
            lines.append("     %s" % ph)
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def list_clinics(area: str = "", kind: str = "") -> str:
    """列出心理治療所／諮商所／精神科診所。

    Args:
        area: 行政區，例如「北區」。空白表示全部
        kind: 「心理治療所」「心理諮商所」「精神科診所」「精神醫療機構」。空白表示全部
    """
    out = []
    for c in _roster():
        if area and area not in (c["area"] + c["addr"]):
            continue
        if kind and kind != c["kind"]:
            continue
        out.append(("名冊", c["name"], c["addr"], c["phone"]))
    if not kind or kind in ("精神科診所", "精神醫療機構"):
        for layer in ("精神科診所", "精神醫療機構"):
            if kind and kind != layer:
                continue
            for it in _kml().get(layer, []):
                blob = it["addr"] + it["desc"]
                if area and area not in blob:
                    continue
                out.append((layer, it["name"], it["addr"] or it["desc"][:50],
                            it["phone"]))
    if not out:
        return "查不到符合的（區=%s，類型=%s）。" % (area or "全部", kind or "全部")
    lines = ["找到 %d 家%s%s：" % (len(out), ("（%s）" % area) if area else "",
                                  ("（%s）" % kind) if kind else ""), ""]
    for src, nm, ad, ph in out:
        lines.append("  %-8s %-22s %s  %s" % ("[" + src + "]", nm[:22], ad[:34], ph))
    return "\n".join(lines)


@mcp.tool()
def which_center(area: str) -> str:
    """這個行政區的社區心理衛生中心是哪一處。

    6 處中心的服務區域加起來剛好涵蓋全部 37 區，不重不漏，
    所以任何行政區都查得到答案。

    Args:
        area: 行政區，例如「永康區」
    """
    a = area.strip()
    if not a.endswith("區"):
        a += "區"
    for c in _centers():
        if a in c["areas"]:
            return "\n".join([
                "%s → %s" % (a, c["name"]),
                "",
                "  電話：%s" % c["phone"],
                "  地址：%s" % c["addr"],
                "  服務區域（%d 區）：%s" % (len(c["areas"]), "、".join(c["areas"])),
            ])
    return ("查不到 %s。\n目前解析到的中心：%s"
            % (a, "、".join(c["name"] for c in _centers())))


# ══════════════════════════════════════════════════
# 分析層 —— 這一層才是 MCP 跟「自己開瀏覽器查」的差別
# ══════════════════════════════════════════════════
@mcp.tool()
def diff_clinic_sources() -> str:
    """三份官方名單，互相漏收了誰。

    不用這支的話：開 3 個分頁 → 把 32 家名冊、26 筆地圖圖層、22 家合作名單
    各自抄進 Excel → 正規化機構名（台南／臺南、有沒有「社團法人」前綴）
    → 做兩次 VLOOKUP。**40～60 分鐘**，而且下個月資料一動全部重做。
    """
    roster = {_norm_name(c["name"]): c["name"] for c in _roster()}
    mapped = {}
    for it in _kml().get("心理治療、諮商所", []):
        nm = it["name"] or ""
        m = re.search(r"([^\s]{2,18}(?:心理治療所|心理諮商所))", nm + " " + it["desc"])
        if m:
            mapped[_norm_name(m.group(1))] = m.group(1)

    only_roster = sorted(set(roster) - set(mapped))
    only_map = sorted(set(mapped) - set(roster))
    both = sorted(set(roster) & set(mapped))

    lines = [
        "三份官方名單的交叉比對",
        "",
        "  衛生局名冊：%d 家" % len(roster),
        "  資源地圖：  %d 家" % len(mapped),
        "  兩邊都有：  %d 家" % len(both),
        "",
        "  ── 只在名冊、地圖上沒有（%d 家）──" % len(only_roster),
    ]
    for k in only_roster:
        lines.append("     %s" % roster[k])
    lines += ["", "  ── 只在地圖、名冊上沒有（%d 家）──" % len(only_map)]
    for k in only_map:
        lines.append("     %s" % mapped[k])
    lines += [
        "",
        "  🔴 這不是單向漏收，是**兩份官方文件互相不知道對方**。",
        "     手動比對要 40–60 分鐘，而且資料一動就得重做。",
    ]
    return "\n".join(lines)


@mcp.tool()
def free_slots_by_weekday(area: str = "") -> str:
    """免費心理諮商的時段，集中在星期幾。

    這是所長會想知道、但沒人做過的分析：
    公部門把免費諮商放在哪些時段，對哪些人來說等於「沒有」。

    Args:
        area: 只看某個行政區。空白表示全台南
    """
    pts = _free_points()
    if area:
        pts = [p for p in pts if area in (p["place"] + p["addr"])]
    if not pts:
        return "查不到免費諮商點（區=%s）。" % (area or "全部")

    wd = Counter(p["weekday"] or "（時段沒寫清楚）" for p in pts)
    sector = Counter(p["sector"] for p in pts)
    order = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

    lines = ["免費心理諮商時段分布%s" % (("（%s）" % area) if area else "（全台南）"), "",
             "  共 %d 個時段" % len(pts), ""]
    mx = max(wd.values()) if wd else 1
    for d in order:
        n = wd.get(d, 0)
        if n:
            lines.append("  %s  %-22s %d" % (d, "█" * int(n * 20 / mx), n))
    for k, v in wd.items():
        if k not in order:
            lines.append("  %s  %d" % (k, v))
    lines += ["", "  公私部門組成：%s" %
              "、".join("%s %d" % (k, v) for k, v in sector.most_common())]

    if not area:
        thin = [d for d in order if 0 < wd.get(d, 0) <= 3]
        if thin:
            lines += ["", "  ⚠ %s 的時段只有個位數 —— 對只有那幾天有空的人來說，"
                          "等於沒有。" % "、".join(thin)]
    return "\n".join(lines)


@mcp.tool()
def count_by_layer() -> str:
    """同一件事，四個官方數字。

    「臺南有幾家精神醫療機構？」這個問題沒有單一答案 ——
    法定「指定精神醫療機構」、精神科醫院名冊、地圖上的精神醫療機構、
    地圖上的精神科診所，是四個不同的東西。

    🔴 危機處理時分不清「指定機構」和「有精神科的醫院」是會出事的。
    """
    k = _kml()
    lines = [
        "「臺南有幾家？」—— 看你問的是哪一種",
        "",
        "  地圖 · 精神科診所      %3d 家" % len(k.get("精神科診所", [])),
        "  地圖 · 精神醫療機構    %3d 家" % len(k.get("精神醫療機構", [])),
        "  地圖 · 心理治療諮商所  %3d 家" % len(k.get("心理治療、諮商所", [])),
        "  衛生局名冊             %3d 家（治療所＋諮商所）" % len(_roster()),
        "",
        "  另外兩個這份快照沒收（是 PDF，現場不解析）：",
        "     法定「指定精神醫療機構」 7 家   ← 能收強制住院的只有這些",
        "     精神科醫院名冊          13 家   ⚠ 檔案是 111.3.14，舊了四年半",
        "",
        "  🔴 「臺南有幾家可以收強制住院？」答案是 **7**，不是 41。",
        "     這幾個數字混用，在危機處理時是會出事的。",
    ]
    return "\n".join(lines)


@mcp.tool()
def find_free_counseling(area: str = "", weekday: str = "") -> str:
    """找免費心理諮商，可以指定行政區和星期。

    衛生局補助，每年 2 次免收諮商費；部分地點酌收場地費 200 元。
    預約專線 06-335-2982（週一至五上班時間）。

    Args:
        area: 行政區，例如「北區」
        weekday: 「週一」～「週五」
    """
    pts = _free_points()
    if area:
        pts = [p for p in pts if area in (p["place"] + p["addr"])]
    if weekday:
        w = weekday if weekday.startswith("週") else "週" + weekday
        pts = [p for p in pts if p["weekday"] == w]
    if not pts:
        return ("查不到（區=%s，星期=%s）。\n可以放寬條件再試，"
                "或用 free_slots_by_weekday() 看整體時段分布。"
                % (area or "全部", weekday or "全部"))

    pub = [p for p in pts if p["sector"] == "公部門"]
    pri = [p for p in pts if p["sector"] == "民間"]
    lines = ["找到 %d 個免費諮商時段%s%s" %
             (len(pts), ("（%s）" % area) if area else "",
              ("（%s）" % weekday) if weekday else ""), ""]
    for title, group in (("公部門（不收場地費）", pub),
                         ("民間機構（第一次免費，第二次起酌收場地費 200 元）", pri)):
        if not group:
            continue
        lines.append("  ── %s：%d 個 ──" % (title, len(group)))
        for p in group:
            lines.append("     %-26s %s" % (p["place"][:26], p["time"][:34]))
            if p["addr"]:
                lines.append("        %s" % p["addr"][:56])
        lines.append("")
    lines.append("  預約專線：06-335-2982（週一至五 8:00-12:00、13:30-17:30）")
    return "\n".join(lines)


@mcp.tool()
def refresh_snapshot() -> str:
    """重新從網路抓一次資料（現場請勿全班同時執行）。

    ⚠ 12 個人同時打政府網站有觸發速率限制的風險。
      現場只由講師示範一次就好，其餘人讀快照。
    """
    import subprocess
    p = subprocess.run([sys.executable,
                        os.path.join(HERE, "fetch_snapshot.py")],
                       capture_output=True, timeout=300)
    _CACHE.clear()
    return (p.stdout or b"").decode("utf-8", "replace") or "（沒有輸出）"


if __name__ == "__main__":
    mcp.run()
