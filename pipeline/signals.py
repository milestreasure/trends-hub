"""Extra attention signals that need no API key. Each source fails safe: if it errors, it is skipped."""
import datetime as dt, json, re, urllib.request
import feedparser

UA = {"User-Agent": "trends-hub/1.0 (https://github.com; magazine trends research)"}
SKIP = ("Main_Page", "Special:", "Wikipedia:", "Portal:", "Help:", "File:", "Category:", "Template:", "Talk:", "-")

def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.load(r)

def phrase(text):
    return re.compile(r"\b" + re.escape(text.lower()) + r"\b")

class Signals:
    def __init__(self, load=True):
        self.wiki, self.goog, self.masto = [], [], []
        if load:
            for name, fn in (("wikipedia", self.load_wiki), ("google", self.load_google), ("mastodon", self.load_masto)):
                try: fn(); print(f"signals: {name} ok")
                except Exception as e: print(f"signals: {name} skipped ({e})")

    def load_wiki(self):
        totals, days, d = {}, 0, dt.date.today() - dt.timedelta(days=1)
        while days < 7 and d > dt.date.today() - dt.timedelta(days=12):
            try:
                data = get_json("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/"
                                f"{d:%Y}/{d:%m}/{d:%d}")
                for a in data["items"][0]["articles"]:
                    if not a["article"].startswith(SKIP):
                        totals[a["article"]] = totals.get(a["article"], 0) + a["views"]
                days += 1
            except Exception:
                pass  # day not published yet
            d -= dt.timedelta(days=1)
        for art, views in sorted(totals.items(), key=lambda x: -x[1])[:600]:
            title = art.replace("_", " ")
            clean = re.sub(r"\s*\(.*?\)", "", title).strip()
            if len(clean.split()) >= 2 or len(clean) >= 7:  # skip short, generic titles
                self.wiki.append((phrase(clean), title, views))
        if not self.wiki: raise ValueError("no data")

    def load_google(self):
        feed = feedparser.parse("https://trends.google.com/trending/rss?geo=GB")
        for e in feed.entries:
            self.goog.append((phrase(e.title), e.title, e.get("ht_approx_traffic", "")))
        if not self.goog: raise ValueError("empty feed")

    def load_masto(self):
        for t in get_json("https://mastodon.social/api/v1/trends/tags?limit=40"):
            if len(t["name"]) >= 5: self.masto.append((t["name"].lower(), t["name"]))
        if not self.masto: raise ValueError("empty")

    def match(self, headlines):
        text = " | ".join(headlines).lower()
        flat = re.sub(r"[^a-z0-9]", "", text)
        out = []
        w = [(v, t) for p, t, v in self.wiki if p.search(text)]
        if w:
            v, t = max(w)
            out.append(dict(kind="wikipedia", label=f"Popular on Wikipedia this week: {t} ({v:,} views)"))
        g = next(((t, tr) for p, t, tr in self.goog if p.search(text)), None)
        if g:
            out.append(dict(kind="google", label=f"Trending on Google searches: {g[0]}" + (f" ({g[1]} searches)" if g[1] else "")))
        m = next((n for k, n in self.masto if k in flat), None)
        if m:
            out.append(dict(kind="mastodon", label=f"Trending on Mastodon: #{m}"))
        return out
