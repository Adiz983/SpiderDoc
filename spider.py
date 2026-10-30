#!/usr/bin/env python3
"""Doc Spider (terminal). Python 3.8+, standard library only, no API keys.

  python3 spider.py --demo                          fictional filing, works offline
  python3 spider.py --file filing.htm               any saved filing (.htm/.html/.txt)
  python3 spider.py MSFT --email you@example.com    latest 10-K from SEC EDGAR
  python3 spider.py HOOD --form 8-K --insiders --email you@example.com
Options: --speed 2 (1,2,4..)  --budget 30 (target seconds)  --no-anim
"""
import sys, os, re, time, shutil, hashlib, argparse, urllib.request, json
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from datetime import date

ESC = '\x1b'; GREEN, PURPLE = 83, 141; PRE = 12
NR = re.compile(r'^\(?-?\$?\s?\d[\d,]*(\.\d+)?\)?\s?%?$')
KEY = re.compile(r'revenue|net income|net loss|total assets|total liabilities|equity|cash and cash equivalents|operating (income|expenses)|net cash|per share|ebitda|gross (profit|margin)|capital expenditures|free cash flow', re.I)
CATS = [(re.compile(r'insider|related[- ]party|10b5-1|blackout', re.I), 'INSIDER / RELATED PARTY'),
        (re.compile(r'material weakness|going concern|restatement|SEC inquiry|subpoena', re.I), 'RED FLAG'),
        (re.compile(r'non-GAAP', re.I), 'ADJUSTED RESULT AS STATED'),
        (re.compile(r'\bexpect|outlook|guidance', re.I), 'GUIDANCE')]
BLOCK = {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}

def sty(txt, fg=None, bg=None, bold=False):
    p = (['1'] if bold else []) + ([f'38;5;{fg}'] if fg is not None else []) + ([f'48;5;{bg}'] if bg is not None else [])
    return f'{ESC}[{";".join(p)}m{txt}{ESC}[0m' if p else txt
def clean(s): return re.sub(r'\s+', ' ', s).strip()
def tonum(s):
    v = float(re.sub(r'[^\d.]', '', s) or 0); return -v if s.strip().startswith('(') else v

# ---------- parsing ----------
def table_items(rows):
    cols, out = None, []
    for r in rows:
        r = [x for x in r if x and not re.fullmatch(r'[$)%]', x)]
        ys = re.findall(r'\b20\d\d\b', ' '.join(r))
        if cols is None and len(ys) >= 2: cols = ys[:2]; continue
        lab = next((x for x in r if not NR.match(x)), None)
        n = [x for x in r if NR.match(x) and not re.fullmatch(r'20\d\d', x)]
        if lab and len(lab) < 80 and len(n) >= 2: out.append([lab, n[0], n[1]])
    if not out: return []
    c = cols or ['Current', 'Prior']
    if cols and int(c[0]) < int(c[1]): c = [c[1], c[0]]; out = [[l, b, a] for l, a, b in out]
    return [('cols', c)] + [('r', l, a, b) for l, a, b in out]

class P(HTMLParser):
    def __init__(s):
        super().__init__(convert_charrefs=True)
        s.items, s.buf, s.rows, s.row, s.cell, s.tab, s.skip = [], [], [], None, None, 0, 0
    def flush(s):
        t = clean(''.join(s.buf)); s.buf = []
        if len(t) < 4: return
        s.items.append(('h', t) if t == t.upper() and re.search('[A-Z]{4}', t) and len(t) < 100 else ('p', t))
    def handle_starttag(s, tag, a):
        if tag in ('script', 'style'): s.skip += 1
        elif tag == 'table':
            if s.tab == 0: s.rows = []
            s.tab += 1
        elif tag == 'tr' and s.tab: s.row = []
        elif tag in ('td', 'th') and s.tab: s.cell = []
        elif tag in BLOCK and not s.tab: s.flush()
    def handle_endtag(s, tag):
        if tag in ('script', 'style'): s.skip = max(0, s.skip - 1)
        elif tag in ('td', 'th') and s.cell is not None and s.row is not None: s.row.append(clean(''.join(s.cell))); s.cell = None
        elif tag == 'tr' and s.row is not None: s.rows.append(s.row); s.row = None
        elif tag == 'table' and s.tab:
            s.tab -= 1
            if s.tab == 0: s.items += table_items(s.rows)
        elif tag in BLOCK and not s.tab: s.flush()
    def handle_data(s, d):
        if s.skip: return
        if s.cell is not None: s.cell.append(d)
        elif not s.tab: s.buf.append(d)

def parse_html(h):
    p = P(); p.feed(h); p.flush(); return p.items

def parse_text(t):
    rowre = re.compile(r'^(.{3,70}?[A-Za-z].*?)\s{2,}\$?\s*(\(?[\d,]+(?:\.\d+)?\)?)\s+\$?\s*(\(?[\d,]+(?:\.\d+)?\)?)\s*$')
    items, rows = [], []
    for l in t.splitlines():
        l = l.strip()
        if not l: continue
        m = rowre.match(l)
        if m: rows.append([m.group(1), m.group(2), m.group(3)]); continue
        if rows: items += [('cols', ['Current', 'Prior'])] + [('r', *r) for r in rows]; rows = []
        items.append(('h', l) if l == l.upper() and re.search('[A-Z]{4}', l) and len(l) < 100 else ('p', l))
    if rows: items += [('cols', ['Current', 'Prior'])] + [('r', *r) for r in rows]
    return items

def build(items):
    L, T, cols, chars, nkey = [], [], ['Current', 'Prior'], 0, 0
    for it in items:
        k = it[0]
        if k == 'cols': cols = it[1]; continue
        page = chars // 3200 + 1
        if k == 'h':
            L.append(('h', it[1])); T.append(dict(k='h', i=len(L) - 1, sec=it[1], page=page)); chars += 80
        elif k == 'r':
            L.append(('r', it[1], it[2], it[3])); chars += 60
            if KEY.search(it[1]) and nkey < 40:
                nkey += 1; T.append(dict(k='r', i=len(L) - 1, lab=it[1], a=it[2], b=it[3], cols=cols, page=page))
        else:
            L.append(('x', it[1][:400])); chars += len(it[1])
            for s in re.split(r'(?<=\.)\s+', it[1]):
                c = next((n for r, n in CATS if r.search(s)), None)
                if c: L.append(('s', s)); T.append(dict(k='s', i=len(L) - 1, cat=c, s=s, page=page))
    return L, T

# ---------- rendering ----------
def style_line(l, W, rd):
    k = l[0]
    if k == 'h': t = l[1][:W]; return sty(t, 16, GREEN, True) if rd else sty(t, bold=True)
    if k == 'r':
        lw = max(10, W - 26); lab = l[1][:lw].ljust(lw); a, b = l[2].rjust(13), l[3].rjust(13)
        return sty(lab, GREEN) + sty(a, 255, 99) + sty(b, 255, 99) if rd else lab + a + b
    if k == 's': t = ('▸ ' + l[1])[:W]; return sty(t, 16, GREEN) if rd else t
    return sty(l[1][:W], 244)

def render(S, L):
    C, R = shutil.get_terminal_size((100, 30)); H = max(8, int(R * .56)); W = C - PRE - 12
    cen = int(round(S['sp'])); top = cen - H // 2; rec = S['hits'][-8:]; out = []
    for r in range(H):
        idx = top + r; pre = [(' ', None)] * PRE
        for j, (hi, col) in enumerate(rec):
            cx = 1 + 2 * (j % 4)
            if min(hi, cen) <= idx <= max(hi, cen): pre[cx] = ('│', col)
            if idx == hi: pre[cx] = ('●', col)
        if idx == cen: pre[8], pre[9], pre[10] = ('╲', GREEN), ('◉', GREEN), ('╱', GREEN)
        p = ''.join(sty(ch, col) if col else ch for ch, col in pre)
        if 0 <= idx < len(L):
            line = style_line(L[idx], W, S['read'].get(idx))
            if S['pop'] and S['pop'][0] == idx: line += ' ' + sty(' ' + S['pop'][1][:10] + ' ', 16, GREEN, True)
        else: line = ''
        out.append(p + line)
    pill = sty(f'  {S["tk"]}   {S["fig"]} of {S["N"]} figures  ', GREEN, 16, True)
    out.append(' ' * max(0, (C - len(f'  {S["tk"]}   {S["fig"]} of {S["N"]} figures  ')) // 2) + pill)
    out.append(sty(f' SECTIONS READ {len(S["secs"])}   FLAGS {len(S["flags"])}   {S["title"][:40]}', 244))
    for cd in S['cards'][:max(1, R - H - 3)]: out.append(cd)
    sys.stdout.write(ESC + '[H' + ''.join(x + ESC + '[K\n' for x in out)[:-1] + ESC + '[J'); sys.stdout.flush()

# ---------- scan ----------
def scan(items, title, tk, budget, speed, anim, snapshot=False):
    L, T = build(items)
    S = dict(sp=0.0, hits=[], read={}, pop=None, fig=0, N=sum(t['k'] == 'r' for t in T), secs=[], flags=[], cards=[], tk=tk, title=title, figs=[])
    if not T: print('Nothing to scan (no headings, key figures, or flagged language found).'); return S
    per = min(.35, max(.05, budget / len(T))) / speed; hop = per * .6
    C = shutil.get_terminal_size((100, 30)).columns; n = 0
    def arrive(t):
        i = t['i']; S['hits'].append((i, GREEN if len(S['hits']) % 2 == 0 else PURPLE)); S['hits'] = S['hits'][-8:]
        S['pop'] = None
        if t['k'] == 'h':
            S['read'][i] = 1; S['secs'].append((t['sec'], t['page']))
        elif t['k'] == 'r':
            S['read'][i] = 1; S['fig'] += 1; S['pop'] = (i, t['a'])
            a, b = tonum(t['a']), tonum(t['b']); ch = (a - b) / abs(b) * 100 if b else None
            S['figs'].append((t['lab'], t['a'], t['b'], t['cols'], ch, t['page']))
            det = (f'{"▲" if ch >= 0 else "▼"} {ch:+.1f}% vs {t["cols"][1]}' if ch is not None else '') + f'  ~p.{t["page"]}'
            tag = t['lab'].upper()[:26]
            S['cards'].insert(0, sty(f' {tag} ', 16, GREEN, True) + ' ' + sty(t['a'], PURPLE, bold=True) + '  ' + det[:max(5, C - len(tag) - len(t['a']) - 8)])
        else:
            S['read'][i] = 1; S['flags'].append((t['cat'], t['s'], t['page']))
            S['cards'].insert(0, sty(f' {t["cat"]} ', 16, GREEN, True) + ' ' + t['s'][:max(10, C - len(t['cat']) - 6)])
    if anim: sys.stdout.write(ESC + '[?1049h' + ESC + '[?25l')
    try:
        for t in T:
            n += 1
            if anim:
                a, b, t0 = S['sp'], t['i'], time.time()
                while True:
                    k = min(1, (time.time() - t0) / max(hop, .001)); e = k * k * (3 - 2 * k)
                    S['sp'] = a + (b - a) * e; render(S, L); time.sleep(1 / 40)
                    if k >= 1: break
                arrive(t); render(S, L); time.sleep(per * .4)
            else:
                S['sp'] = t['i']; arrive(t)
            if snapshot and n >= len(T) // 2: break
    except KeyboardInterrupt: pass
    finally:
        if anim: sys.stdout.write(ESC + '[?25h' + ESC + '[?1049l')
    return S

# ---------- EDGAR ----------
def make_get(email):
    cache = os.path.expanduser('~/.docspider-cache'); os.makedirs(cache, exist_ok=True)
    def get(url):
        f = os.path.join(cache, hashlib.md5(url.encode()).hexdigest())
        if os.path.exists(f): return open(f, 'rb').read()
        time.sleep(.12)
        req = urllib.request.Request(url, headers={'User-Agent': f'DocSpider {email}'})
        data = urllib.request.urlopen(req, timeout=30).read(); open(f, 'wb').write(data); return data
    return get

def insiders(cik, sub, get):
    r = sub['filings']['recent']; n = len(r['form']); items = r.get('items', [''] * n)
    rows = list(zip(r['form'], r['accessionNumber'], r['filingDate'], r['primaryDocument'], items))
    earn = [date.fromisoformat(d) for f, _, d, _, it in rows if f == '8-K' and '2.02' in (it or '')]
    trades = []
    for f, acc, d, doc, _ in [x for x in rows if x[0] == '4'][:25]:
        try:
            raw = get(f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-", "")}/{re.sub(r"^xsl[^/]+/", "", doc)}')
            root = ET.fromstring(raw); plan = '10b5-1' in raw.decode('utf-8', 'ignore')
            name = root.findtext('.//reportingOwnerId/rptOwnerName') or '?'
            role = root.findtext('.//reportingOwnerRelationship/officerTitle') or 'Director/Owner'
            for t in root.findall('.//nonDerivativeTransaction'):
                code = t.findtext('.//transactionCode')
                if code not in ('P', 'S'): continue
                trades.append(dict(name=name, role=role, code=code, date=(t.findtext('.//transactionDate/value') or '')[:10],
                                   shares=float(t.findtext('.//transactionShares/value') or 0), price=float(t.findtext('.//transactionPricePerShare/value') or 0), plan=plan))
        except Exception: continue
    out = []
    for t in sorted(trades, key=lambda x: x['date'], reverse=True):
        flags = []
        try:
            dt = date.fromisoformat(t['date']); nxt = [e for e in earn if e >= dt]
            if t['code'] == 'S' and nxt and (min(nxt) - dt).days <= 30: flags.append(f'sale {(min(nxt) - dt).days}d before earnings 8-K')
        except Exception: pass
        if t['code'] == 'S' and not t['plan']: flags.append('no 10b5-1 mention')
        out.append((t, flags))
    sellers = [(date.fromisoformat(t['date']), t['name']) for t, _ in out if t['code'] == 'S' and t['date']]
    for d0, _ in sellers:
        if len({nm for d1, nm in sellers if 0 <= (d1 - d0).days <= 7}) >= 3:
            out.append(({'name': 'CLUSTER', 'role': '', 'code': '', 'date': str(d0), 'shares': 0, 'price': 0, 'plan': False}, ['3+ insiders sold within 7 days'])); break
    return out

def edgar(tk, form, email):
    get = make_get(email)
    m = next((v for v in json.loads(get('https://www.sec.gov/files/company_tickers.json')).values() if v['ticker'] == tk.upper()), None)
    if not m: sys.exit(f'Ticker {tk} not found.')
    cik = m['cik_str']; sub = json.loads(get(f'https://data.sec.gov/submissions/CIK{cik:010d}.json')); r = sub['filings']['recent']
    for f, acc, d, doc in zip(r['form'], r['accessionNumber'], r['filingDate'], r['primaryDocument']):
        if f == form:
            url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-", "")}/{doc}'
            return m['title'], d, get(url).decode('utf-8', 'replace'), (cik, sub, get)
    sys.exit(f'No {form} found for {tk}.')

# ---------- demo ----------
def demo_html():
    def tb(cols, rows):
        return '<table><tr><td></td>' + ''.join(f'<td>{c}</td>' for c in cols) + '</tr>' + ''.join(
            f'<tr><td>{l}</td><td>$</td><td>{a}</td><td>$</td><td>{b}</td></tr>' for l, a, b in rows) + '</table>'
    return ('<p>NORTHWIND MARKETS, INC.</p><p>FORM 8-K</p><p>CONDENSED CONSOLIDATED BALANCE SHEETS</p>'
        + tb(['Jun 30, 2026', 'Dec 31, 2025'], [('Cash and cash equivalents', '5,412', '4,301'), ('Total assets', '56,550', '38,180'), ('Total liabilities', '47,009', '29,080'), ('Total equity', '9,541', '9,100')])
        + '<p>CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS</p>'
        + tb(['Q2 2026', 'Q2 2025'], [('Total net revenues', '1,122', '894'), ('Total operating expenses', '407', '331'), ('Net income', '722', '611'), ('Diluted earnings per share', '0.62', '0.51')])
        + '<p>CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS</p>'
        + tb(['6M 2026', '6M 2025'], [('Net cash from operating activities', '720', '512'), ('Capital expenditures', '118', '96'), ('Net cash from financing activities', '410', '355')])
        + '<p>RESULTS AND STATED OUTLOOK</p><p>Adjusted EBITDA (non-GAAP) increased 35% year-over-year to $741 million. We expect full-year 2026 adjusted operating expenses of $2.1 billion to $2.2 billion.</p>'
        + '<p>GOVERNANCE AND CONTROLS</p><p>Management identified a material weakness in internal control over the financial close process. Our Chief Financial Officer sold 120,000 shares under a 10b5-1 plan ten days before the quarter-end blackout period. The company also disclosed a related party transaction with a director.</p>')

# ---------- main ----------
def report(S, ins, title, tk):
    ln = [f'# Doc Spider report: {title} ({tk})', '', f'{S["fig"]} key figures, {len(S["secs"])} sections read, {len(S["flags"])} passages flagged', '', '## Key figures']
    for lab, a, b, cols, ch, pg in S['figs']:
        ln.append(f'- {lab}: {a} vs {b} ({cols[0]} vs {cols[1]})' + (f', {ch:+.1f}%' if ch is not None else '') + f' [~p.{pg}]')
    ln += ['', '## Flagged language (for review, not evidence of wrongdoing)'] + [f'- [{c}] {s} [~p.{p}]' for c, s, p in S['flags']]
    if ins:
        ln += ['', '## Insider trades (Form 4)']
        for t, fl in ins: ln.append(f'- {t["date"]} {t["name"]} {t["role"]} {t["code"]} {t["shares"]:,.0f} @ {t["price"]:.2f}' + (f'  FLAGS: {"; ".join(fl)}' if fl else ''))
        ln.append('Timing patterns are for review only; they are not evidence of insider trading.')
    ln += ['', 'Page numbers are estimates. Figures are read from the filing tables as printed (units not converted).']
    return '\n'.join(ln)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Doc Spider terminal')
    ap.add_argument('ticker', nargs='?'); ap.add_argument('--form', default='10-K'); ap.add_argument('--email', default=os.environ.get('SEC_EMAIL'))
    ap.add_argument('--file'); ap.add_argument('--demo', action='store_true'); ap.add_argument('--insiders', action='store_true')
    ap.add_argument('--speed', type=float, default=1); ap.add_argument('--budget', type=float, default=30)
    ap.add_argument('--no-anim', action='store_true'); ap.add_argument('--snapshot', action='store_true')
    a = ap.parse_args(); ins = None
    if a.demo: title, tk, items = 'Northwind Markets, Inc.', 'NWM 8-K', parse_html(demo_html())
    elif a.file:
        t = open(a.file, encoding='utf-8', errors='replace').read()
        title, tk, items = os.path.basename(a.file), 'FILE', parse_html(t) if re.search(r'<(p|div|table)\b', t, re.I) else parse_text(t)
    elif a.ticker:
        if not a.email: sys.exit('SEC requires contact info: pass --email you@example.com (or set SEC_EMAIL).')
        title, fdate, html, ctx = edgar(a.ticker, a.form, a.email); tk = f'{a.ticker.upper()} {a.form}'; items = parse_html(html)
        if a.insiders: ins = insiders(*ctx)
    else: ap.print_help(); sys.exit()
    anim = sys.stdout.isatty() and not a.no_anim
    S = scan(items, title, tk, a.budget, a.speed, anim, a.snapshot)
    txt = report(S, ins, title, tk); print(txt)
    fn = re.sub(r'\W+', '_', tk) + '-spider-report.md'; open(fn, 'w').write(txt); print(f'\nSaved {fn}')
