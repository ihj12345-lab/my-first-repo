from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import yfinance as yf
from pykrx import stock as krx
from datetime import datetime, timedelta
import json
import os

app = FastAPI()

CUSTOM_FILE = os.path.join(os.path.dirname(__file__), "custom_stocks.json")

def load_custom():
    base = dict(DEFAULT_CUSTOM)
    if os.path.exists(CUSTOM_FILE):
        with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
            base.update(json.load(f))
    return base

def save_custom(data: dict):
    with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.post("/stocks/add")
def add_stock(name: str = Form(...), symbol: str = Form(...)):
    custom = load_custom()
    custom[name.strip()] = symbol.strip().upper()
    save_custom(custom)
    return RedirectResponse("/", status_code=303)

@app.post("/stocks/remove")
def remove_stock(name: str = Form(...)):
    custom = load_custom()
    custom.pop(name, None)
    save_custom(custom)
    return RedirectResponse("/", status_code=303)

INDICES = {
    "S&P 500": "^GSPC",
    "나스닥": "^IXIC",
    "다우존스": "^DJI",
}

COMMODITIES = {
    "달러/원 환율": "KRW=X",
    "WTI 유가": "CL=F",
    "금": "GC=F",
}

SEMIS = {
    "엔비디아": "NVDA",
    "TSMC": "TSM",
    "마이크론": "MU",
}

KR_STOCKS = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "현대차": "005380",
}

DEFAULT_CUSTOM = {}

OTHER = {
    "스페이스X": "SPCX",
    "롯데칠성": "005300",
    "이더리움": "ETH-USD",
}

def fetch_mixed(ticker_map):
    kr_map = {n: s for n, s in ticker_map.items() if s.isdigit()}
    us_map = {n: s for n, s in ticker_map.items() if not s.isdigit()}
    results = {}
    if kr_map:
        results.update(fetch_kr(kr_map))
    if us_map:
        results.update(fetch(us_map))
    return {n: results[n] for n in ticker_map if n in results}

def ai_summary(indices, commodities, kr_stocks):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_summary(indices, commodities, kr_stocks)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        lines = []
        for name, d in indices.items():
            if isinstance(d["price"], (int, float)):
                lines.append(f"{name}: {d['price']:,.2f} ({d['pct']:+.2f}%)")
        for name, d in commodities.items():
            if isinstance(d["price"], (int, float)):
                lines.append(f"{name}: {d['price']:,.2f} ({d['pct']:+.2f}%)")
        kr_up = [n for n, d in kr_stocks.items() if isinstance(d["pct"], (int, float)) and d["pct"] > 0]
        kr_dn = [n for n, d in kr_stocks.items() if isinstance(d["pct"], (int, float)) and d["pct"] < 0]
        lines.append(f"한국 종목 상승: {len(kr_up)}개, 하락: {len(kr_dn)}개")
        data_str = "\n".join(lines)
        msg = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=200,
            thinking={"type": "adaptive"},
            messages=[{
                "role": "user",
                "content": f"아래는 오늘 주요 시장 데이터입니다. 이를 바탕으로 오늘 시장 상황을 한국어로 한 문장(50자 이내)으로 핵심만 요약해주세요. 설명 없이 요약 문장만 출력하세요.\n\n{data_str}"
            }]
        )
        for block in msg.content:
            if block.type == "text":
                return block.text.strip()
    except Exception:
        pass
    return _rule_based_summary(indices, commodities, kr_stocks)

def _rule_based_summary(indices, commodities, kr_stocks):
    sp = indices.get("S&P 500", {})
    nasdaq = indices.get("나스닥", {})
    krw = commodities.get("달러/원 환율", {})
    sp_pct = sp.get("pct", 0) if isinstance(sp.get("pct"), (int, float)) else 0
    nasdaq_pct = nasdaq.get("pct", 0) if isinstance(nasdaq.get("pct"), (int, float)) else 0
    krw_pct = krw.get("pct", 0) if isinstance(krw.get("pct"), (int, float)) else 0
    avg = (sp_pct + nasdaq_pct) / 2
    trend = "상승" if avg > 0 else "하락" if avg < 0 else "보합"
    krw_trend = "강세" if krw_pct > 0 else "약세" if krw_pct < 0 else "보합"
    return f"미국 증시 {trend} ({avg:+.2f}%), 달러/원 {krw_trend} ({krw_pct:+.2f}%)"

def fetch_kr(ticker_map):
    results = {}
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    for name, code in ticker_map.items():
        try:
            df = krx.get_market_ohlcv(start, today, code)
            df = df.dropna()
            if len(df) >= 2:
                closes = [round(float(v), 0) for v in df["종가"].tolist()]
                prev = df["종가"].iloc[-2]
                last = df["종가"].iloc[-1]
                change = last - prev
                pct = (change / prev) * 100
                results[name] = {
                    "price": float(round(last, 0)),
                    "change": float(round(change, 0)),
                    "pct": round(pct, 2),
                    "closes": closes,
                    "post_price": None,
                    "post_pct": None,
                }
            elif len(df) == 1:
                last = df["종가"].iloc[-1]
                results[name] = {"price": round(last, 0), "change": 0, "pct": 0, "closes": [float(last)], "post_price": None, "post_pct": None}
        except Exception:
            results[name] = {"price": "-", "change": 0, "pct": 0, "closes": [], "post_price": None, "post_pct": None}
    return results

def fetch(ticker_map):
    results = {}
    for name, symbol in ticker_map.items():
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d")
            closes = [round(float(v), 4) for v in hist["Close"].tolist()]
            if len(hist) >= 2:
                prev = hist["Close"].iloc[-2]
                last = hist["Close"].iloc[-1]
                change = last - prev
                pct = (change / prev) * 100
                data = {
                    "price": round(last, 2),
                    "change": round(change, 2),
                    "pct": round(pct, 2),
                    "closes": closes,
                    "post_price": None,
                    "post_pct": None,
                }
                results[name] = data
            elif len(hist) == 1:
                last = hist["Close"].iloc[-1]
                results[name] = {"price": round(last, 2), "change": 0, "pct": 0, "closes": closes, "post_price": None, "post_pct": None}
        except Exception:
            results[name] = {"price": "-", "change": 0, "pct": 0, "closes": [], "post_price": None, "post_pct": None}
    return results

@app.get("/", response_class=HTMLResponse)
def index():
    indices = fetch(INDICES)
    commodities = fetch(COMMODITIES)
    semis = fetch(SEMIS)
    kr_stocks = fetch_kr(KR_STOCKS)
    other = fetch_mixed(OTHER)
    krw_rate = commodities.get("달러/원 환율", {}).get("price")
    if "이더리움" in other and isinstance(other["이더리움"]["price"], (int, float)) and isinstance(krw_rate, (int, float)):
        other["이더리움"]["krw_price"] = round(other["이더리움"]["price"] * krw_rate, 0)
    custom = load_custom()
    custom_data = fetch(custom) if custom else {}
    summary = ai_summary(indices, commodities, kr_stocks)
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render(indices, commodities, semis, kr_stocks, other, updated, summary, custom_data)

def color(pct):
    if isinstance(pct, str):
        return "#888"
    return "#3b82f6" if pct < 0 else "#ef4444" if pct > 0 else "#888"

def arrow(pct):
    if isinstance(pct, str):
        return ""
    return "▼" if pct < 0 else "▲" if pct > 0 else "─"

KR_CODES = set(KR_STOCKS.keys()) | {n for n, s in OTHER.items() if s.isdigit()}

def fmt_price(name, price):
    if name == "달러/원 환율":
        return f"{price:,.1f} 원"
    if name == "WTI 유가":
        return f"${price:,.2f}"
    if name == "금":
        return f"${price:,.2f}"
    if name in KR_CODES:
        return f"{int(price):,} 원" if isinstance(price, (int, float)) else "-"
    return f"{price:,.2f}"

def sparkline(closes, line_color):
    if len(closes) < 2:
        return ""
    w, h = 120, 36
    mn, mx = min(closes), max(closes)
    rng = mx - mn if mx != mn else 1
    pts = []
    for i, v in enumerate(closes):
        x = round(i / (len(closes) - 1) * w, 1)
        y = round(h - (v - mn) / rng * h, 1)
        pts.append(f"{x},{y}")
    poly = " ".join(pts)
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;margin-top:10px"><polyline points="{poly}" fill="none" stroke="{line_color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>'

def card(name, data, removable=False):
    p = data["price"]
    pct = data["pct"]
    chg = data["change"]
    c = color(pct)
    a = arrow(pct)
    price_str = fmt_price(name, p) if isinstance(p, (int, float)) else "-"
    remove_btn = f'<form method="post" action="/stocks/remove" style="display:inline"><input type="hidden" name="name" value="{name}"><button class="rm-btn" type="submit">✕</button></form>' if removable else ""
    chart = sparkline(data.get("closes", []), c)
    krw_price = data.get("krw_price")
    krw_html = f'<div class="card-after">≈ {int(krw_price):,} 원</div>' if krw_price else ""
    post_html = ""
    post_price = data.get("post_price")
    post_pct = data.get("post_pct")
    if post_price:
        pc = "#22c55e" if post_pct and post_pct > 0 else "#ef4444" if post_pct and post_pct < 0 else "#888"
        pa = "▲" if post_pct and post_pct > 0 else "▼" if post_pct and post_pct < 0 else "─"
        post_html = f'<div class="card-after">시간외 {post_price:,.2f} <span style="color:{pc}">{pa}{abs(post_pct):.2f}%</span></div>'
    return f"""
    <div class="card">
        <div class="card-header"><div class="card-name">{name}</div>{remove_btn}</div>
        <div class="card-price">{price_str}</div>
        <div class="card-change" style="color:{c}">{a} {abs(chg):,.2f} ({abs(pct):.2f}%)</div>
        {krw_html}{post_html}
        {chart}
    </div>"""

def section(title, data, removable=False):
    cards = "".join(card(n, d, removable) for n, d in data.items())
    return f'<div class="section"><h2>{title}</h2><div class="grid">{cards}</div></div>'

def render(indices, commodities, semis, kr_stocks, other, updated, summary="", custom_data=None):
    s1 = section("🇺🇸 미국 주요 지수", indices)
    s2 = section("📊 주요 경제지표", commodities)
    s_semi = section("💾 미국 반도체 주요 종목", semis)
    s3 = section("🇰🇷 한국 대표 종목", kr_stocks)
    s_other = section("🌐 기타 주요 종목", other)
    s4 = section("⭐ 내 종목", custom_data, removable=True) if custom_data else ""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>증시 대시보드</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #f1f5f9; font-family: 'Segoe UI', sans-serif; padding: 24px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.85rem; margin-bottom: 32px; }}
  .section {{ margin-bottom: 36px; }}
  .section h2 {{ font-size: 1.1rem; color: #94a3b8; margin-bottom: 14px; border-bottom: 1px solid #1e293b; padding-bottom: 8px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 18px; height: 160px; display: flex; flex-direction: column; justify-content: space-between; box-sizing: border-box; }}
  .card-price {{ font-size: 1.3rem; font-weight: 700; margin-bottom: 6px; }}
  .card-change {{ font-size: 0.88rem; font-weight: 500; }}
  .card-after {{ font-size: 0.78rem; color: #94a3b8; margin-top: 4px; }}
  .ai-summary {{ background: #1e3a5f; border: 1px solid #2d6a9f; border-radius: 10px; padding: 14px 18px; margin-bottom: 28px; font-size: 0.95rem; color: #93c5fd; display: flex; align-items: center; gap: 10px; }}
  .ai-summary .ai-icon {{ font-size: 1.1rem; flex-shrink: 0; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
  .card-name {{ font-size: 1rem; font-weight: 600; color: #e2e8f0; }}
  .rm-btn {{ background: none; border: none; color: #64748b; cursor: pointer; font-size: 0.75rem; padding: 0; line-height: 1; }}
  .rm-btn:hover {{ color: #ef4444; }}
  .add-form {{ background: #1e293b; border-radius: 12px; padding: 16px 18px; margin-bottom: 36px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  .add-form input {{ background: #0f172a; border: 1px solid #334155; color: #f1f5f9; border-radius: 8px; padding: 8px 12px; font-size: 0.85rem; width: 160px; }}
  .add-form input::placeholder {{ color: #475569; }}
  .add-form button {{ background: #3b82f6; color: white; border: none; border-radius: 8px; padding: 8px 16px; font-size: 0.85rem; cursor: pointer; }}
  .add-form button:hover {{ background: #2563eb; }}
  .refresh {{ margin-top: 32px; text-align: center; }}
  .refresh a {{ color: #3b82f6; text-decoration: none; font-size: 0.9rem; }}
  .refresh a:hover {{ text-decoration: underline; }}
  .row-wrap {{ display: flex; gap: 32px; flex-wrap: wrap; align-items: flex-start; }}
  .row-wrap .section {{ flex: 1; min-width: 280px; margin-bottom: 36px; }}
</style>
</head>
<body>
<h1>📈 증시 대시보드</h1>
<div class="subtitle">전일 종가 기준 · 마지막 업데이트: {updated}</div>
<div class="ai-summary"><span class="ai-icon">🤖</span><span>{summary}</span></div>
<div class="row-wrap">{s1}{s2}</div>{s_semi}<div class="row-wrap">{s3}{s_other}</div>
<div class="section">
<h2>⭐ 내 종목 추가</h2>
<form class="add-form" method="post" action="/stocks/add">
  <input name="name" placeholder="종목 이름 (예: 애플)" required>
  <input name="symbol" placeholder="티커 (예: AAPL)" required>
  <button type="submit">+ 추가</button>
</form>
</div>
{s4}
<div class="refresh"><a href="/">🔄 새로고침</a></div>
</body>
</html>"""
