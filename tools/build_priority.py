"""Turn a MicroStrategy sales export (the .xls saved as a web page) into data/priority.json.

Usage:  python tools/build_priority.py "Total_Revenue.xls"
Each row is matched to a sub-category in taxonomy.yaml (ALIASES covers names that differ). The output holds only a
relative weight per segment and sub-category, used to order the hub. No sales values are written to the file, so it is
safe to publish. Keep the sales export itself out of the repository. Needs only PyYAML."""
import email, json, math, pathlib, re, sys
from email import policy
from html.parser import HTMLParser
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sales-file sub-segment -> taxonomy sub-category, where the names differ (segment names are matched separately)
ALIASES = {
    "MISC. (GENERAL INTEREST)": "General interest", "PC TECHNICAL": "PC help & technical", "PC HELP": "PC help & technical",
    "GAMING - PC GAMES": "Gaming - PC", "RAILWAYS - MODERN": "Railways", "RAILWAYS - STEAM": "Railways",
    "CYCLE SPORTS - CYCLING": "Cycling", "CYCLE SPORTS - MOUTAIN BIKE": "Mountain bike", "CRAFTS - GENERAL CRAFT": "Crafts - general",
    "POP MUSIC": "Pop", "OTHER": "Other music", "SUMMER SPLS / ANNUALS / CALENDERS": "Summer specials, annuals & calendars",
    "SKI-ING AND SNOWBOARDING": "Skiing & snowboarding", "WATERSPORT": "Watersports", "ONE SHOTS": "One shots",
    "BUYING & SELLING - GENERAL": "General", "BUYING & SELLING - PROPERTY": "Property", "TRADE & PROFESSIONAL": "Trade & professional",
    "DEL - COOKERY": "Cookery & kitchen", "COOKERY & KITCHEN": "Cookery & kitchen", "NEEDLECRAFT": "Crafts - needlecraft",
    "CRAFTS - NEEDLECRAFT": "Crafts - needlecraft", "MENS LIFESTYLE": "Mens lifestyle", "MENS WEEKLY": "Mens weekly",
    "CAMPING/WALKING/CLIMBING": "Camping, walking & climbing", "ENTERTAINMENT/LISTINGS": "Entertainment & listings",
    "DANCE & THEATRE": "Dance & theatre", "ELECTRONICS & RADIO": "Electronics & radio", "FILM & VIDEO REVIEWS": "Film & video reviews",
    "GENERAL HEALTH & FITNESS": "General health & fitness", "TV & FILM SPIN OFFS": "TV & film spin-offs", "VIDEO & CAMCORDERS": "Video & camcorders",
    "TRANSPORT - GENERAL": "Transport - general", "RADIO CONTROLLED MODELLING": "Radio controlled modelling",
    "FICTION (ROMANTIC / LITERARY)": "Fiction", "WEDDINGS & BRIDES": "Weddings & brides", "WOMEN'S CELEBRITY WEEKLIES": "Women's celebrity weeklies",
    "MACHINE KNITTING": "Crafts - knitting & crochet", "SEWING & KNITTING": "Crafts - sewing & quilting",
    "CURRENT AFFAIRS - DOMESTIC": "Current affairs - domestic", "CURRENT AFFAIRS - INTERNATIONAL": "Current affairs - international",
    "SUMMER SPLS": "Summer specials, annuals & calendars", "JOB ADVERTS": "Job adverts", "HEAVY METAL": "Heavy metal",
    "PRACTICAL/PLAYING": "Practical & playing", "SATELLITE/CABLE": "Satellite & cable", "RADIO & TV GUIDES": "Radio & TV guides",
    "BUSINESS & FINANCE": "Business & finance", "LOGIC TITLES": "Logic titles", "PUZZLE ARROWWORDS": "Puzzle arrowwords",
}
norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())

class Rows(HTMLParser):
    def __init__(self): super().__init__(); self.rows, self.row, self.cell, self.on = [], None, "", False
    def handle_starttag(self, t, a):
        if t == "tr": self.row = []
        if t in ("td", "th"): self.cell, self.on = "", True
    def handle_endtag(self, t):
        if t in ("td", "th") and self.row is not None: self.row.append(re.sub(r"\s+", " ", self.cell).strip()); self.on = False
        if t == "tr" and self.row is not None: self.rows.append(self.row); self.row = None
    def handle_data(self, d):
        if self.on: self.cell += d

def read_rows(path):
    raw = pathlib.Path(path).read_bytes()
    if raw[:8] == b"MIME-Ver":  # an Excel "web page" export
        msg = email.message_from_bytes(raw, policy=policy.default)
        html = max((p.get_payload(decode=True) or b"" for p in msg.walk() if p.get_content_type() == "text/html"), key=len)
    else:
        html = raw
    p = Rows(); p.feed(html.decode("utf-8", "replace"))
    out = []
    for r in p.rows:
        m = re.search(r"£\s*([\d,]+(?:\.\d+)?)", " ".join(r))
        if m and len(r) >= 3 and r[0] and r[1]: out.append((r[0], r[1], float(m.group(1).replace(",", ""))))
    return out

def main(path):
    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())
    segs = {norm(k): k for k in tax}
    chans = {(norm(s), norm(c)): (s, c) for s, cs in tax.items() for c in cs}
    alias = {norm(k): v for k, v in ALIASES.items()}
    rows = read_rows(path)
    total = sum(r[2] for r in rows)
    out, miss = {}, []
    for seg, sub, rev in rows:
        s = segs.get(norm(seg))
        c = (chans.get((norm(seg), norm(sub))) or (s and (s, alias[norm(sub)]) if norm(sub) in alias else None))
        if not s or not c or c[1] not in tax[s]: miss.append((seg, sub, rev)); continue
        d = out.setdefault(s, dict(revenue=0.0, channels={}))
        d["revenue"] += rev; d["channels"][c[1]] = d["channels"].get(c[1], 0) + rev
    w = lambda v: round(1 + math.log2(1 + 100 * v / total), 2)  # relative weight only, not a sales value
    res = dict(note="Relative weights used to order the hub. Not sales values.", segments={})
    for s_, d in out.items():
        res["segments"][s_] = dict(weight=w(d["revenue"]), channels={c: w(v) for c, v in d["channels"].items()})
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "priority.json").write_text(json.dumps(res, indent=1))
    print(f"{len(rows)} rows. Mapped {len(rows) - len(miss)} rows into {sum(len(v['channels']) for v in out.values())} sub-categories. Wrote data/priority.json (weights only).")
    if miss:
        print("Not mapped (left out of the hub):")
        for seg, sub, rev in sorted(miss, key=lambda x: -x[2]): print(f"  {seg} / {sub}")

if __name__ == "__main__":
    main(sys.argv[1])
