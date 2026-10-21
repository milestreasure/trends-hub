"""Daily trends pipeline: ingest (UK + US) -> cluster -> score per region -> signals -> (optional) summaries."""
import datetime as dt, hashlib, json, os, pathlib, re, time, urllib.parse
import feedparser, numpy as np, yaml
import platforms
from signals import Signals, phrase, words
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = pathlib.Path(__file__).resolve().parent.parent
ITEMS, OUT = ROOT / "data" / "items.json", ROOT / "docs" / "data" / "trends.json"
KEEP_DAYS, SIM, MIN_ITEMS, MIN_SOURCES, TOP_N, TOPIC_N, DAYS = 60, 0.4, 2, 2, 5, 60, 14
REGIONS = {"UK": ("en-GB", "GB", "GB:en"), "US": ("en-US", "US", "US:en")}
NOW = dt.datetime.now(dt.timezone.utc)
STOP = list(ENGLISH_STOP_WORDS | {"uk", "us", "new", "news", "best", "says", "say", "latest", "2025", "2026", "video", "watch",
                                   "live", "report", "reports", "review", "guide", "big", "year", "day", "week", "amid", "gets", "get"})

def _pubs():
    p = ROOT / "publishers.yaml"
    d = (yaml.safe_load(p.read_text()) if p.exists() else None) or {}
    return [x.lower() for x in d.get("uk", [])], [x.lower() for x in d.get("us", [])]
UK_PUBS, US_PUBS = _pubs()

def classify(href, edition):
    """Label a story by the publisher's country; fall back to the edition that returned it."""
    host = (urllib.parse.urlparse(href).hostname or "").lower().removeprefix("www.")
    if host:
        if host.endswith(".uk") or any(host == d or host.endswith("." + d) for d in UK_PUBS): return "UK", True
        if host.endswith((".us", ".gov", ".edu", ".mil")) or any(host == d or host.endswith("." + d) for d in US_PUBS): return "US", True
    return edition, False

def fetch(seg, chan, q, region):
    hl, gl, ceid = REGIONS[region]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:14d", "hl": hl, "gl": gl, "ceid": ceid})
    try:
        feed = feedparser.parse(url)
    except Exception as e:  # one failing source must not stop the run
        print(f"skip {region} {seg}/{chan}: {e}"); return []
    out = []
    for e in feed.entries:
        if not e.get("published_parsed"): continue
        reg, known = classify((e.get("source") or {}).get("href", ""), region)
        out.append(dict(
            id=hashlib.md5(e.link.encode()).hexdigest(), seg=seg, chan=chan, region=reg, known=known, edition=region,
            title=re.sub(r"\s+-\s+[^-]+$", "", e.title), link=e.link,
            source=(e.get("source") or {}).get("title", ""),
            ts=dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).isoformat()))
    time.sleep(1)  # be polite
    return out

def load_items():
    store = {i["id"]: i for i in json.loads(ITEMS.read_text())} if ITEMS.exists() else {}
    cutoff = NOW - dt.timedelta(days=KEEP_DAYS)
    store = {k: v for k, v in store.items() if dt.datetime.fromisoformat(v["ts"]) > cutoff
             and not (v.get("region") == "US" and "edition" not in v)}  # drop US items labelled by the old edition-based method
    for v in store.values(): v.setdefault("region", "UK")
    return store

def cluster(items):
    try:
        X = TfidfVectorizer(stop_words=STOP, ngram_range=(1, 1)).fit_transform([i["title"] for i in items])
        S = cosine_similarity(X)
    except ValueError:  # every title was stop words: treat each item as its own trend
        return np.eye(len(items)), list(range(len(items)))
    labels, seeds = [-1] * len(items), []
    for n in range(len(items)):  # items are newest-first
        for c, s in enumerate(seeds):
            if S[n, s] >= SIM: labels[n] = c; break
        else:
            seeds.append(n); labels[n] = len(seeds) - 1
    return S, labels

def signal(cur, prev):
    r = (cur + 1) / (prev + 1)
    return "rising" if r >= 1.5 else "fading" if r <= 0.67 else "steady"

def summarise(trend):
    if not os.getenv("ANTHROPIC_API_KEY"): return
    try:
        import anthropic
        heads = "\n".join(f"- {i['title']} ({i['source']}, {i['region']})" for i in trend["items"][:8])
        msg = anthropic.Anthropic().messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=300,
            messages=[{"role": "user", "content":
                "You brief UK magazine editors. Using ONLY these headlines, write two lines.\n"
                "Line 1: what the trend is and why it matters (max 30 words). Say if it is mainly a US trend.\n"
                "Line 2: one content or commercial angle for a UK publisher (max 25 words).\n\n" + heads}])
        a, _, b = msg.content[0].text.strip().partition("\n")
        trend["summary"], trend["angle"] = a.strip(), b.strip()
    except Exception as e:
        print("summary skipped:", e)

def daily(members):
    out = [0] * DAYS  # oldest -> newest, by publish date
    for m in members:
        age = (NOW - dt.datetime.fromisoformat(m["ts"])).days
        if 0 <= age < DAYS: out[DAYS - 1 - age] += m.get("w", 1)
    return out

def region_stats(members, d7, region):
    ms = [m for m in members if m["region"] in (region, "ALL")]  # "ALL" = global platform activity, counted in both
    cur = sum(m.get("w", 1) for m in ms if dt.datetime.fromisoformat(m["ts"]) > d7)
    tot = sum(m.get("w", 1) for m in ms)
    return dict(recent=cur, previous=tot - cur, daily=daily(ms), signal=signal(cur, tot - cur),
                sources=len({m["source"] for m in ms if m["source"]}))

def make_trend(members, title, d7, sig):
    srcs = {m["source"] for m in members if m["source"]}
    news_n = sum(1 for m in members if not m.get("platform"))
    plat_w = sum(m.get("w", 1) for m in members if m.get("platform"))
    if not ((news_n >= MIN_ITEMS and len(srcs) >= MIN_SOURCES) or plat_w >= 4): return None  # news story, or a real platform surge
    uk, us = region_stats(members, d7, "UK"), region_stats(members, d7, "US")
    ut, st = uk["recent"] + uk["previous"], us["recent"] + us["previous"]
    origin = "us" if ut == 0 else "uk" if st == 0 else "us" if (st >= 3 and ut <= 1) else "both"
    plat, seen = [], set()
    for m in members:
        if m.get("platform") and m["link"] not in seen: seen.add(m["link"]); plat.append(m)
    show = [m for m in members if m["region"] == "UK" and not m.get("platform")][:5] + \
           [m for m in members if m["region"] == "US"][:3] + plat[:3]
    t = dict(title=title, origin=origin, uk=uk, us=us, platforms=sorted({m["platform"] for m in members if m.get("platform")}),
             sources=len(srcs), items=[{k: m[k] for k in ("title", "link", "source", "ts", "region")} for m in show],
             score=(uk["recent"] + 0.5 * us["recent"]) * (1 + 0.3 * len(srcs)))
    t["signals"] = sig.match([m["title"] for m in members if not m.get("platform")])
    t["score"] *= 1 + 0.25 * len(t["signals"])  # boost trends seen on other platforms
    return t

def build_trends(items, d7, sig):
    """News headlines are clustered first. Wikipedia and YouTube items then join the news trend they are named in,
    and any left over (a surge the news has not covered yet) are clustered among themselves as trends of their own."""
    news = [i for i in items if not i.get("platform")]
    plat = [i for i in items if i.get("platform")]
    groups = []
    if news:
        S, labels = cluster(news)
        for c in sorted(set(labels)):
            idx = [n for n, l in enumerate(labels) if l == c]
            pool = [n for n in idx if news[n]["region"] == "UK"] or idx  # prefer a UK headline as the title
            medoid = pool[int(np.argmax(S[np.ix_(pool, idx)].sum(axis=1)))]
            groups.append(dict(members=[news[n] for n in idx], title=news[medoid]["title"], ws=[words(news[n]["title"]) for n in idx],
                               titles=[news[n]["title"].lower() for n in idx]))
    loose = []
    for p in plat:
        best, hits = None, 0
        for g in groups:
            if p["platform"] == "wikipedia":
                pat = phrase(p["title"])
                n = sum(1 for t in g["titles"] if pat.search(t)) if len(p["title"]) >= 6 else 0
            else:
                pw = words(p["title"]); n = sum(1 for w in g["ws"] if len(pw & w) >= 2)
            if n > hits: best, hits = g, n
        if best: best["members"].append(p)
        else: loose.append(p)
    if loose:
        _, l2 = cluster(loose)
        for c in sorted(set(l2)):
            ms = [loose[n] for n, l in enumerate(l2) if l == c]
            top = max(ms, key=lambda m: (m["platform"] == "wikipedia", m.get("w", 1)))
            groups.append(dict(members=ms, title=top["title"]))
    trends = [t for t in (make_trend(g["members"], g["title"], d7, sig) for g in groups) if t]
    return sorted(trends, key=lambda t: -t["score"])[:TOP_N]

def tag_topics(trend, topics):
    texts = [i["title"].lower() for i in trend["items"]]
    found = []
    for name, words in topics.items():
        pats = [re.compile(r"\b" + re.escape(w.lower()) + r"s?\b") for w in words]
        if sum(any(p.search(t) for p in pats) for t in texts) >= 2: found.append(name)
    return found

def main():
    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())
    tp = ROOT / "topics.yaml"
    topics = yaml.safe_load(tp.read_text()) if tp.exists() else {}
    mp = ROOT / "data" / "market.json"  # optional: sales value per segment and sub-category (tools/build_market.py)
    market = json.loads(mp.read_text()) if mp.exists() else {"segments": {}}
    fp = ROOT / "fit.yaml"  # optional: how much trends drive sales per segment
    fit = (yaml.safe_load(fp.read_text()) if fp.exists() else None) or {}
    sig = Signals()
    store = load_items()
    for seg, chans in tax.items():
        for chan, qs in chans.items():
            for q in (qs if isinstance(qs, list) else [qs]):  # a sub-category can have several searches
                for region in REGIONS:
                    for it in fetch(seg, chan, q, region):
                        cur = store.get(it["id"])  # the same article from both editions is stored once
                        if cur is None: store[it["id"]] = it
                        elif it["known"] and not cur.get("known"): cur.update(region=it["region"], known=True)
    ITEMS.parent.mkdir(exist_ok=True)
    ITEMS.write_text(json.dumps(list(store.values())))
    d7, d14 = NOW - dt.timedelta(days=7), NOW - dt.timedelta(days=14)
    recent_news = [i for i in store.values() if dt.datetime.fromisoformat(i["ts"]) > d14]
    plat = {}
    for it in platforms.collect(NOW, tax, recent_news):  # Wikipedia and YouTube candidates, placed by sub-category
        if dt.datetime.fromisoformat(it["ts"]) > d14: plat.setdefault((it["seg"], it["chan"]), []).append(it)
    segments, by_topic = [], {}
    for seg, chans in tax.items():
        out_chans = []
        for chan in chans:
            items = sorted([i for i in store.values() if i["seg"] == seg and i["chan"] == chan and
                            dt.datetime.fromisoformat(i["ts"]) > d14] + plat.get((seg, chan), []),
                           key=lambda i: i["ts"], reverse=True)
            trends = build_trends(items, d7, sig)
            for n, t in enumerate(trends):
                if n < 2: summarise(t)  # limit LLM calls to the top 2 per channel
                t["segment"], t["channel"] = seg, chan
                t["topics"] = tag_topics(t, topics)
                for name in t["topics"]: by_topic.setdefault(name, []).append(t)
            if trends: out_chans.append(dict(name=chan, trends=trends, market=market['segments'].get(seg, {}).get('channels', {}).get(chan)))
        segments.append(dict(name=seg, channels=out_chans, fit=fit.get(seg, 'medium'), market={k: v for k, v in market['segments'].get(seg, {}).items() if k != 'channels'} or None))
    topic_out = sorted(({"name": n, "total": len(v), "trends": sorted(v, key=lambda t: -t["score"])[:TOPIC_N]}
                        for n, v in by_topic.items()), key=lambda t: -t["total"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(version=2, updated=NOW.isoformat(), market_total=market.get('total'),
        days=[(NOW - dt.timedelta(days=DAYS - 1 - i)).date().isoformat() for i in range(DAYS)],
        segments=segments, topics=topic_out), indent=1))
    print("done:", sum(len(c["trends"]) for s in segments for c in s["channels"]), "trends,", len(topic_out), "topics")

if __name__ == "__main__":
    main()
