"""Daily trends pipeline: ingest -> dedupe -> cluster -> score -> (optional) summarise."""
import datetime as dt, hashlib, json, os, pathlib, re, time, urllib.parse
import feedparser, numpy as np, yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = pathlib.Path(__file__).resolve().parent.parent
ITEMS, OUT = ROOT / "data" / "items.json", ROOT / "docs" / "data" / "trends.json"
KEEP_DAYS, SIM, MIN_ITEMS, MIN_SOURCES, TOP_N = 60, 0.35, 3, 2, 6
NOW = dt.datetime.now(dt.timezone.utc)

def fetch(seg, chan, q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:14d", "hl": "en-GB", "gl": "GB", "ceid": "GB:en"})
    try:
        feed = feedparser.parse(url)
    except Exception as e:  # one failing source must not stop the run
        print(f"skip {seg}/{chan}: {e}"); return []
    out = []
    for e in feed.entries:
        if not e.get("published_parsed"): continue
        out.append(dict(
            id=hashlib.md5(e.link.encode()).hexdigest(), seg=seg, chan=chan,
            title=re.sub(r"\s+-\s+[^-]+$", "", e.title), link=e.link,
            source=(e.get("source") or {}).get("title", ""),
            ts=dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).isoformat()))
    time.sleep(1)  # be polite
    return out

def load_items():
    store = {i["id"]: i for i in json.loads(ITEMS.read_text())} if ITEMS.exists() else {}
    cutoff = NOW - dt.timedelta(days=KEEP_DAYS)
    return {k: v for k, v in store.items() if dt.datetime.fromisoformat(v["ts"]) > cutoff}

def cluster(items):
    X = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform([i["title"] for i in items])
    S = cosine_similarity(X)
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
        heads = "\n".join(f"- {i['title']} ({i['source']})" for i in trend["items"][:8])
        msg = anthropic.Anthropic().messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=300,
            messages=[{"role": "user", "content":
                "You brief magazine editors. Using ONLY these headlines, write two lines.\n"
                "Line 1: what the trend is and why it matters (max 30 words).\n"
                "Line 2: one content or commercial angle for a UK publisher (max 25 words).\n\n" + heads}])
        a, _, b = msg.content[0].text.strip().partition("\n")
        trend["summary"], trend["angle"] = a.strip(), b.strip()
    except Exception as e:
        print("summary skipped:", e)

def main():
    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())
    store = load_items()
    for seg, chans in tax.items():
        for chan, q in chans.items():
            for it in fetch(seg, chan, q): store.setdefault(it["id"], it)
    ITEMS.parent.mkdir(exist_ok=True)
    ITEMS.write_text(json.dumps(list(store.values())))
    d7, d14 = NOW - dt.timedelta(days=7), NOW - dt.timedelta(days=14)
    result = []
    for seg in tax:
        items = sorted((i for i in store.values() if i["seg"] == seg and
                        dt.datetime.fromisoformat(i["ts"]) > d14), key=lambda i: i["ts"], reverse=True)
        trends = []
        if len(items) >= MIN_ITEMS:
            S, labels = cluster(items)
            for c in set(labels):
                idx = [n for n, l in enumerate(labels) if l == c]
                members = [items[n] for n in idx]
                srcs = {m["source"] for m in members if m["source"]}
                if len(members) < MIN_ITEMS or len(srcs) < MIN_SOURCES: continue
                cur = sum(dt.datetime.fromisoformat(m["ts"]) > d7 for m in members)
                prev = len(members) - cur
                medoid = idx[int(np.argmax(S[np.ix_(idx, idx)].sum(axis=1)))]
                trends.append(dict(title=items[medoid]["title"], signal=signal(cur, prev),
                                   recent=cur, previous=prev, sources=len(srcs),
                                   channels=sorted({m["chan"] for m in members}),
                                   items=[{k: m[k] for k in ("title", "link", "source", "ts")} for m in members[:8]],
                                   score=cur * (1 + 0.3 * len(srcs))))
        trends = sorted(trends, key=lambda t: -t["score"])[:TOP_N]
        for t in trends: summarise(t)
        result.append(dict(name=seg, trends=trends))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(updated=NOW.isoformat(), segments=result), indent=1))
    print("done:", sum(len(s["trends"]) for s in result), "trends")

if __name__ == "__main__":
    main()
