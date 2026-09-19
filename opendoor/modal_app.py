"""Open Door on Modal: resolve(org), capture(target), box(target).
Deploy: uv run modal deploy opendoor/modal_app.py
Test:   uv run modal run opendoor/modal_app.py::smoke     (one practice end to end)
        uv run modal run opendoor/modal_app.py::newham    (24 Newham practices, writes fixtures/pages/)
        uv run modal run opendoor/modal_app.py::boxes     (boxes quotes on fixture pages + nhs.uk, writes fixtures/boxed/)
Read only: never submits a form, never types into a field. At most 2 browser page loads per practice site per run:
capture makes 1 (2 when it has to find the registration link on the homepage itself), and stores an offline MHTML
snapshot so box can draw on the very same page without contacting the site again.
"""
import html, json, re, time, urllib.parse, urllib.request, zlib
from pathlib import Path
import modal

app = modal.App("opendoor")
image = modal.Image.debian_slim(python_version="3.12").run_commands(
    "pip install playwright", "playwright install-deps chromium", "playwright install chromium")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 "
      "OpenDoorResearch/0.1 (non-commercial hackathon research on GP registration wording; read only; max 2 page loads; no form submission)")
CHALLENGE = re.compile(r"verify (?:that )?you are (?:a )?human|human verification|security verification|just a moment|checking your browser"
                       r"|attention required|cf-chl|captcha|access denied|request blocked|incapsula|are you a robot", re.I)
# Copy of opendoor.models.DOC_RE (the container does not import the local package). smoke() asserts they match.
DOC_RE = re.compile(
    r"passport|proof of (?:address|id|identity|identification|residence|residency)|photo(?:graphic)? ?(?:id|identification)"
    r"|utility bill|driving licen[cs]e|bank statement|tenancy agreement|council tax bill|immigration status|visa|biometric"
    r"|\bID\b|identification|birth certificate", re.I)
# The national NHS registration form sits behind a "Human Verification" wall: never load it, just record the link.
NATIONAL = re.compile(r"^https?://(?:gp-registration\.nhs\.uk|register-with-gp\.)", re.I)
NATIONAL_LINK = re.compile(NATIONAL.pattern + r"|^https?://(?:www\.)?nhs\.uk/.*register-with-a-gp", re.I)  # contract: links_national_form
REG_LINK = re.compile(r"regist|new.?patient|join (?:the |our |this )?(?:practice|surgery)|join us", re.I)
snaps = modal.Dict.from_name("opendoor-snapshots", create_if_missing=True)  # final_url -> {"mhtml": zlib bytes, "loads": n}
COLOURS = {"red": "213,40,27", "green": "0,127,59", "amber": "237,139,0"}
norm = lambda s: re.sub(r"\s+", " ", s or "").strip()
host = lambda u: (urllib.parse.urlsplit(u).hostname or "").removeprefix("www.")


def _get(url):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"}), timeout=12)
    return r.geturl(), r.read(1_500_000).decode("utf-8", "ignore")


def _reg_links(base, pairs):
    """pairs = (href, label). Keeps registration links on the practice's own host, or the national form; own pages first."""
    links = []
    for href, label in pairs:
        l = urllib.parse.urljoin(base, href).split("#")[0]
        if REG_LINK.search(label + " " + href) and l.startswith("http") and not l.lower().split("?")[0].endswith(".pdf") \
                and l not in links and l.rstrip("/") != base.split("#")[0].rstrip("/") and (host(l) == host(base) or NATIONAL.search(l)):
            links.append(l)
    best = re.compile(r"new.patient|join|register.with|how.to.register|registration", re.I)
    return sorted(links, key=lambda l: (bool(NATIONAL.search(l)), not best.search(l)))[:3]


@app.function(image=image, max_containers=4, timeout=60)
@modal.concurrent(max_inputs=25)
def resolve(org: dict) -> dict:
    """Adds site (from the practice's nhs.uk profile), reg_links and reg_url (from the practice homepage). Two GETs."""
    out = {**org, "site": None, "reg_links": [], "reg_url": None, "error": None}
    try:
        _, p = _get("https://www.nhs.uk/services/gp-surgery/x/%s/contact-details-and-opening-times" % org["code"])
        m = re.search(r'contact_info_panel_website_link"[^>]*href="([^"]+)"', p)
        if not m: return out
        out["site"] = out["reg_url"] = html.unescape(m.group(1))
        base, h = _get(out["site"])
        # Sites behind a JS challenge (AWS WAF answers plain GETs with 202) give no links here: capture then finds the link in the browser.
        out["reg_links"] = _reg_links(base, [(html.unescape(a.group(1)), norm(html.unescape(re.sub(r"<[^>]+>", " ", a.group(2)))))
                                             for a in re.finditer(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', h)])
        if out["reg_links"]: out["reg_url"] = out["reg_links"][0]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


def _page(p):
    browser = p.chromium.launch()
    return browser, browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-GB", accept_downloads=False).new_page()


def _goto(page, url):
    """One page load. Returns the http status."""
    assert not NATIONAL.search(url), "national form is never loaded"
    resp = page.goto(url, timeout=25_000, wait_until="domcontentloaded")
    try: page.wait_for_load_state("networkidle", timeout=6_000)
    except Exception: pass
    return resp.status if resp else None


LINKS_JS = "els => els.map(e => [e.href, (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim()])"


@app.function(image=image, max_containers=100, timeout=120, cpu=1.0, memory=2048)
def capture(target: dict) -> dict:
    from playwright.sync_api import sync_playwright
    t0, url = time.time(), target.get("reg_url") or target.get("site")
    out = {**target, "status": "ok", "final_url": url, "title": "", "challenge": False, "text": "", "links_national_form": False,
           "doc_hits": [], "national_form_only": False, "http_status": None, "loads": 0, "_png": None, "secs": 0.0, "error": None}
    if not url:
        return {**out, "status": "no_site"}
    if NATIONAL.search(url):
        return {**out, "links_national_form": True, "national_form_only": True}
    mhtml = None
    with sync_playwright() as p:
        browser = None
        try:
            browser, page = _page(p)
            st, out["loads"] = _goto(page, url), 1
            pairs = page.eval_on_selector_all("a[href]", LINKS_JS)
            if not target.get("reg_links") and host(url) == host(target.get("site") or ""):  # on the homepage: follow its registration link once
                links = _reg_links(page.url, pairs)
                own = [l for l in links if not NATIONAL.search(l)]
                out["reg_links"], out["national_form_only"] = links, bool(links) and not own
                if own:
                    out["reg_url"] = own[0]; st, out["loads"] = _goto(page, own[0]), 2
                    pairs += page.eval_on_selector_all("a[href]", LINKS_JS)
            txt = page.inner_text("body")
            out.update(http_status=st, final_url=page.url, title=page.title(), text=norm(txt)[:40_000], _png=page.screenshot(type="png"))
            out["challenge"] = bool(st in (401, 403, 429, 503) or (CHALLENGE.search(out["title"] + " " + txt[:3000]) and len(txt) < 3000))
            out["links_national_form"] = any(NATIONAL_LINK.search(h) for h, _ in pairs + [[page.url, ""]])
            out["doc_hits"] = sorted({m.group(0).lower() for m in DOC_RE.finditer(out["text"])})
            if out["challenge"] or (st or 0) >= 400: out["status"] = "blocked"
            else:  # offline copy of exactly what was read, so box needs no further contact with the site
                mhtml = page.context.new_cdp_session(page).send("Page.captureSnapshot", {"format": "mhtml"})["data"]
        except Exception as e:
            out.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")
        if browser: browser.close()
    if mhtml:  # outside the playwright block: its event loop makes Modal's blocking Dict call warn
        snaps[out["final_url"]] = {"mhtml": zlib.compress(mhtml.encode()), "loads": out["loads"]}
    out["secs"] = round(time.time() - t0, 1)
    return out


# Finds the quote ignoring whitespace and case (inner_text applies CSS text-transform, the DOM does not), scrolls it to the centre.
FIND_JS = """(quote) => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), map = []; let s = '', n;
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (!p || p.closest('script,style,noscript,template') || !p.getClientRects().length || getComputedStyle(p).visibility === 'hidden') continue;
    for (let i = 0; i < n.data.length; i++) { const c = n.data[i]; if (/\\s/.test(c)) continue; for (const l of c.toLowerCase()) { s += l; map.push([n, i]); } }
  }
  const q = quote.replace(/\\s+/g, '').toLowerCase(), at = q ? s.indexOf(q) : -1;
  if (at < 0) return false;
  const r = document.createRange(), a = map[at], b = map[at + q.length - 1];
  r.setStart(a[0], a[1]); r.setEnd(b[0], b[1] + 1); window.__od = r;
  a[0].parentElement.scrollIntoView({block: 'center', behavior: 'instant'});
  const c = r.getBoundingClientRect(); window.scrollBy({top: c.top + c.height / 2 - innerHeight / 2, behavior: 'instant'});
  return true;
}"""
# Draws the outline over window.__od (a Range) or over a given rect. position:fixed because the viewport is screenshotted straight after.
DRAW_JS = """([rect, rgb]) => {
  if (!rect) { const rs = [...window.__od.getClientRects()].filter(r => r.width > 1 && r.height > 1); if (!rs.length) return null;
    const x = Math.min(...rs.map(r => r.left)), y = Math.min(...rs.map(r => r.top));
    rect = {x, y, width: Math.max(...rs.map(r => r.right)) - x, height: Math.max(...rs.map(r => r.bottom)) - y}; }
  const d = document.createElement('div'), pad = 8;
  d.style.cssText = `position:fixed;z-index:2147483647;pointer-events:none;box-sizing:border-box;border-radius:6px;left:${rect.x - pad}px;top:${rect.y - pad}px;` +
    `width:${rect.width + 2 * pad}px;height:${rect.height + 2 * pad}px;border:4px solid rgb(${rgb});background:rgba(${rgb},0.12)`;
  document.documentElement.appendChild(d);
  return {x: Math.round(rect.x - pad), y: Math.round(rect.y - pad), width: Math.round(rect.width + 2 * pad), height: Math.round(rect.height + 2 * pad)};
}"""


@app.function(image=image, max_containers=30, timeout=120, cpu=1.0, memory=2048)
def box(target: dict) -> dict:
    """target: {final_url or reg_url, quote, colour: red|green|amber}. quote_box is in pixels of the returned 1280x900 _png.
    Draws on capture's offline snapshot when there is one. Loads the live page only if that keeps the site at 2 loads or fewer."""
    from playwright.sync_api import sync_playwright
    t0, quote = time.time(), norm(target.get("quote"))
    urls = [u for u in (target.get("final_url"), target.get("reg_url"), target.get("site")) if u]
    rgb = COLOURS.get(target.get("colour"), COLOURS["red"])
    out = {**target, "found": False, "quote_box": None, "source": None, "_png": None, "secs": 0.0, "error": None}
    snap = next((s for s in (snaps.get(u) for u in urls) if s), None)
    with sync_playwright() as p:
        browser = None
        try:
            browser, page = _page(p)
            hit = False
            if snap:
                Path("/tmp/snap.mhtml").write_bytes(zlib.decompress(snap["mhtml"]))
                page.goto("file:///tmp/snap.mhtml"); out["source"] = "snapshot"
                hit = bool(quote) and page.evaluate(FIND_JS, quote)
            if not hit and (not snap or snap["loads"] < 2):
                _goto(page, urls[0]); out["source"] = "live"
                hit = bool(quote) and page.evaluate(FIND_JS, quote)
            if hit:
                page.wait_for_timeout(400)  # let sticky headers and lazy images settle after the scroll
                out["quote_box"] = page.evaluate(DRAW_JS, [None, rgb])
            elif quote:  # fallback: element containing the first 60 chars
                loc = page.get_by_text(re.compile(r"\s+".join(re.escape(w) for w in quote[:60].split()), re.I))
                for i in range(min(loc.count(), 8)):
                    if loc.nth(i).is_visible():
                        loc.nth(i).scroll_into_view_if_needed(timeout=3000)
                        out["quote_box"] = page.evaluate(DRAW_JS, [loc.nth(i).bounding_box(), rgb]); break
            out["found"] = bool(out["quote_box"])
            out["_png"] = page.screenshot(type="png")
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        if browser: browser.close()
    out["secs"] = round(time.time() - t0, 1)
    return out


# ---------- local entrypoints (testing only) ----------
ROOT = Path(__file__).resolve().parent.parent
NHS = ("https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/", "You do not need ID, proof of address or proof of immigration status.")


def _sentence(text):
    """A sentence from the page text that holds a document phrase (stand-in for the classifier's quote)."""
    for s in re.split(r"(?<=[.!?:])\s+", text):
        if 30 < len(s) < 300 and DOC_RE.search(s): return s
    return None


def _save(r, folder, name):
    png = r.pop("_png", None); d = ROOT / "fixtures" / folder; d.mkdir(parents=True, exist_ok=True)
    if png: (d / f"{name}.png").write_bytes(png)
    return r


@app.local_entrypoint()
def smoke(code: str = "F84093", name: str = "TOLLGATE MEDICAL CENTRE"):
    import sys; sys.path.insert(0, str(ROOT))
    from opendoor import models
    assert models.DOC_RE.pattern == DOC_RE.pattern and models.DOC_RE.flags == DOC_RE.flags, "DOC_RE drifted from models.py"
    assert (models.NHS_GUIDANCE_URL, models.NHS_GUIDANCE_QUOTE) == NHS
    assert NATIONAL.search("https://gp-registration.nhs.uk/F84681/gpregistration/landing") and NATIONAL.search("https://register-with-gp.ht1.uk/?gpCode=F84672")
    assert NATIONAL_LINK.search(NHS[0]) and not NATIONAL.search(NHS[0]) and not NATIONAL_LINK.search("https://essexlodge.com/register-with-our-practice/")
    assert _reg_links("https://a.nhs.uk/", [("/new-patients/", "Join"), ("https://klinik.example/x", "Register"), (NHS[0], "How to register"), ("/x.pdf", "Registration form"),
                                            ("https://gp-registration.nhs.uk/F1", "Register online")]) == ["https://a.nhs.uk/new-patients/", "https://gp-registration.nhs.uk/F1"]
    t = resolve.remote({"code": code, "name": name}); print("resolve:", json.dumps(t))
    c = capture.remote(t); png = c.pop("_png"); print("capture:", json.dumps({**c, "text": c["text"][:200] + "..."}), "| png bytes", len(png or b""))
    assert set(c) >= {"status", "final_url", "title", "challenge", "text", "links_national_form", "doc_hits", "secs", "error"}
    q = _sentence(c["text"]); print("quote:", q)
    b = _save(box.remote({"code": code, "final_url": c["final_url"], "quote": q, "colour": "red"}), "boxed", f"smoke_{code}")
    print("box:", json.dumps({k: b[k] for k in ("found", "quote_box", "source", "secs", "error")}))
    nat = capture.remote({"code": "X", "reg_url": "https://gp-registration.nhs.uk/F84681/gpregistration/landing"})
    assert nat["national_form_only"] and nat["links_national_form"] and nat["status"] == "ok" and nat["text"] == "" and nat["secs"] == 0.0
    assert capture.remote({"code": "X"})["status"] == "no_site"
    print("national form guard ok, no_site ok")


@app.local_entrypoint()
def newham():
    gps = json.loads((ROOT.parent / "probe" / "targets.json").read_text())["gps"]
    own = lambda g: g.get("url") and (host(g["url"]) == host(g["site"] or "") or NATIONAL.search(g["url"]))
    targets = [{**g, "reg_url": g["url"]} if own(g) else {**g, "reg_url": g.get("site"), "reg_links": []} for g in gps]  # third-party hosts are never loaded
    t0, res = time.time(), []
    for r in capture.map(targets, order_outputs=False, return_exceptions=True):
        if not isinstance(r, dict): print("EXC", repr(r)); continue
        res.append(_save(r, "pages", r["code"]))
        (ROOT / "fixtures" / "pages" / f"{r['code']}.json").write_text(json.dumps(r, indent=1))
    wall = round(time.time() - t0, 1)
    for r in sorted(res, key=lambda r: r["code"]):
        print(f"{r['code']:7} {r['status']:8} http={r['http_status']} chal={r['challenge']} nat_only={r['national_form_only']} nat_link={r['links_national_form']} txt={len(r['text'])} hits={len(r['doc_hits'])} {r['secs']}s {r['error'] or ''}")
    n = lambda s: sum(1 for r in res if r["status"] == s)
    print(f"{len(res)}/{len(targets)} returned in {wall}s wall | ok={n('ok')} (of which national_form_only={sum(r['national_form_only'] for r in res)}) blocked={n('blocked')} error={n('error')} no_site={n('no_site')} | container secs={round(sum(r['secs'] for r in res), 1)}")


@app.local_entrypoint()
def boxes(limit: int = 6):
    """Boxes a document sentence on the first `limit` fixture pages that have one, plus the nhs.uk guidance quote in green."""
    jobs = [{"code": "NHSUK", "final_url": NHS[0], "quote": NHS[1], "colour": "green"}]
    for f in sorted((ROOT / "fixtures" / "pages").glob("*.json")):
        r = json.loads(f.read_text()); q = r["status"] == "ok" and _sentence(r["text"])
        if q and len(jobs) <= limit: jobs.append({"code": r["code"], "final_url": r["final_url"], "quote": q, "colour": "red"})
    for b in box.map(jobs, order_outputs=False, return_exceptions=True):
        if not isinstance(b, dict): print("EXC", repr(b)); continue
        _save(b, "boxed", b["code"]); print(f"{b['code']:7} found={b['found']} src={b['source']} box={b['quote_box']} {b['secs']}s {b['error'] or ''} | {b['quote'][:90]}")
