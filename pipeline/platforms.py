"""Platform-originated trend candidates. Wikipedia surges and YouTube chart entries become items alongside news."""
import datetime as dt, hashlib, json, os, pathlib, re, time, urllib.parse
from signals import SKIP, get_json, words

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE, DAYS = ROOT / "data" / "wiki_desc.json", 14
# Default sub-category per segment when nothing in the news matches the item better.
DEFAULT = {"Sport": "Football - adult", "Music": "Pop", "Leisure Interests": "Film & video reviews", "Womens Interest": "Women's celebrity weeklies",
           "Computing": "Gaming - multi-format", "Motoring & Motorcycling": "Motoring - general", "News & Current Affairs": "Current affairs - domestic", "Children's Magazines": "Pre-teen"}
# Wikipedia short descriptions -> segment (first match wins). Edit to taste.
WIKI_SEG = {
    "Children's Magazines": ["children's television", "children's animated", "animated television series", "preschool television", "children's book", "toy", "cartoon", "toy line", "children's film"],
    "Sport": ["footballer", "football club", "football manager", "cricketer", "rugby", "golfer", "cyclist", "boxer", "tennis player",
              "racing driver", "athlete", "swimmer", "Formula One", "NFL", "NBA", "wrestler", "sprinter"],
    "Music": ["singer", "rapper", "band", "album", "song", "musician", "DJ", "composer", "songwriter", "record producer"],
    "Leisure Interests": ["film", "television series", "actor", "actress", "film director", "miniseries", "sitcom", "anime"],
    "Computing": ["video game", "software", "smartphone", "operating system", "programming language", "chatbot", "technology company"],
    "Motoring & Motorcycling": ["automobile", "car", "motorcycle", "vehicle manufacturer"],
    "Womens Interest": ["television personality", "influencer", "socialite", "fashion designer", "model"],
    "News & Current Affairs": ["politician", "prime minister", "president", "election", "war", "conflict", "journalist", "political party"]}
# YouTube video category -> segment.
YT_SEG = {"10": "Music", "20": "Computing", "17": "Sport", "2": "Motoring & Motorcycling", "26": "Womens Interest",
          "28": "Computing", "24": "Leisure Interests", "25": "News & Current Affairs"}

def wiki_weight(rank):
    return 3 if rank <= 25 else 2 if rank <= 100 else 1 if rank <= 300 else 0

def wiki_segment(desc):
    d = desc.lower()
    for seg, keys in WIKI_SEG.items():
        if any(re.search(r"\b" + re.escape(k.lower()) + r"\b", d) for k in keys): return seg

def wiki_candidates(now):
    """Articles whose rank in the daily top lists is rising: one item per day the article charted."""
    pres, got, d = {}, 0, now.date() - dt.timedelta(days=1)
    while got < DAYS and d > now.date() - dt.timedelta(days=DAYS + 5):
        try:
            data = get_json("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/"
                            f"{d:%Y}/{d:%m}/{d:%d}")
            for a in data["items"][0]["articles"]:
                w = wiki_weight(a["rank"])
                if w and not a["article"].startswith(SKIP): pres.setdefault(a["article"], {})[d] = w
            got += 1
        except Exception:
            pass  # day not published yet
        d -= dt.timedelta(days=1)
    if not got: raise ValueError("no data")
    cut, keep = now.date() - dt.timedelta(days=7), []
    for art, days in pres.items():  # keep risers only, so evergreen pages never count as trends
        rec = sum(w for dd, w in days.items() if dd > cut)
        prev = sum(w for dd, w in days.items() if dd <= cut)
        if rec >= 4 and rec >= 1.5 * (prev + 1): keep.append((rec, art, days))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    items = []
    for rec, art, days in sorted(keep, reverse=True)[:150]:
        if art not in cache:
            try:
                cache[art] = get_json("https://en.wikipedia.org/api/rest_v1/page/summary/" +
                                      urllib.parse.quote(art, safe="")).get("description", "") or ""
            except Exception:
                continue  # retry on the next run
            time.sleep(0.05)
        seg = wiki_segment(cache[art])
        if not seg: continue
        title = re.sub(r"\s*\(.*?\)", "", art.replace("_", " "))
        for day, w in days.items():
            items.append(dict(id=hashlib.md5(f"{art}{day}".encode()).hexdigest(), seg=seg, region="ALL", title=title,
                              link="https://en.wikipedia.org/wiki/" + art, source="Wikipedia", platform="wikipedia", w=w, known=True,
                              ts=dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.timezone.utc).isoformat()))
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps({k: v for k, v in cache.items() if k in pres}))
    return items

def youtube_candidates(now):
    """Videos on the UK most-popular chart, by category (needs YOUTUBE_API_KEY)."""
    key = os.getenv("YOUTUBE_API_KEY")
    if not key: raise ValueError("no YOUTUBE_API_KEY set")
    out = []
    for cid, seg in YT_SEG.items():
        try:
            q = urllib.parse.urlencode(dict(part="snippet,statistics", chart="mostPopular", regionCode="GB",
                                            videoCategoryId=cid, maxResults=40, key=key))
            for v in get_json("https://www.googleapis.com/youtube/v3/videos?" + q).get("items", []):
                views = int(v["statistics"].get("viewCount", 0))
                out.append(dict(id=v["id"], seg=seg, region="UK", title=v["snippet"]["title"], source="YouTube", platform="youtube",
                                link="https://www.youtube.com/watch?v=" + v["id"], known=True,
                                w=1 + (views > 1_000_000) + (views > 5_000_000), ts=v["snippet"]["publishedAt"].replace("Z", "+00:00")))
        except Exception as e:
            print(f"platforms: youtube category {cid} skipped ({e})")
    return out

def collect(now, tax, news):
    """Candidates from both platforms, each placed in the sub-category whose news it overlaps most."""
    cands = []
    for fn in (wiki_candidates, youtube_candidates):
        try:
            got = fn(now); cands += got; print(f"platforms: {fn.__name__} {len(got)} items")
        except Exception as e:
            print(f"platforms: {fn.__name__} skipped ({e})")
    nw = {}
    for i in news: nw.setdefault((i["seg"], i["chan"]), set()).update(words(i["title"]))
    out = []
    for it in cands:
        chans = tax.get(it["seg"])
        if not chans: continue
        w, best, score = words(it["title"]), None, 0
        for (seg, chan), ws in nw.items():
            ov = len(w & ws)
            if seg == it["seg"] and ov and ov / len(ws) ** 0.5 > score: best, score = chan, ov / len(ws) ** 0.5
        d = DEFAULT.get(it["seg"])
        it["chan"] = best or (d if d in chans else next(iter(chans)))
        out.append(it)
    return out
