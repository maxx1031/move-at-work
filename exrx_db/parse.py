"""Parse downloaded ExRx pages (data/exrx/pages) into JSONL records.

Outputs in data/exrx/parsed/:
  exercises.jsonl  one record per exercise page (weights, stretches, plyometrics, aerobic)
  muscles.jsonl    one record per /Muscles/<X> page
  listings.jsonl   one record per (exercise, list page) placement: region, muscle group,
                   equipment, parent exercise (nested variations in the ExList trees)
"""
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

ROOT = Path(__file__).resolve().parents[1] / "data" / "exrx"
PAGES = ROOT / "pages"
OUT = ROOT / "parsed"
EXERCISE_SECTIONS = ("WeightExercises", "Stretches", "Plyometrics", "Aerobic/Exercises")
BASE = "https://exrx.net"


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def site_path(href, page_path):
    """Resolve a link on `page_path` to a canonical exrx path like /Muscles/BicepsBrachii."""
    if not href or href.startswith(("mailto:", "javascript:")):
        return None
    url = urljoin(BASE + page_path, href)
    # Wayback-rewritten links: /web/<ts>/https://exrx.net/...
    url = re.sub(r"^https?://web\.archive\.org/web/\d+[a-z_]*/", "", url)
    u = urlparse(url)
    if not re.search(r"(^|\.)exrx\.net$", u.netloc, re.I):
        return None
    return u.path.rstrip("/") or "/"


def slug(path):
    """Record-id-safe key from a site path: /WeightExercises/X/Y -> weightexercises_x_y."""
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def section_blocks(soup, title):
    """Sibling elements following the <h2> whose text starts with `title`."""
    h = soup.find("h2", string=re.compile(rf"^\s*{title}", re.I))
    blocks = []
    if h is None:
        return blocks
    for sib in h.find_next_siblings():
        if sib.name in ("h2", "h3"):
            break
        blocks.append(sib)
    return blocks


def parse_classification(soup):
    """{utility, mechanics, force, ...}; a cell may hold several 'Label: value' pairs."""
    out = {}
    for block in section_blocks(soup, "Classification"):
        for tr in block.find_all("tr"):
            text = " ".join(clean(td.get_text(" ")) for td in tr.find_all("td"))
            labels = [clean(s.get_text()).rstrip(":") for s in tr.find_all("strong")]
            labels = [l for l in labels if l]
            for i, label in enumerate(labels):
                start = text.find(label) + len(label)
                end = text.find(labels[i + 1], start) if i + 1 < len(labels) else len(text)
                value = text[start:end].strip(" :")
                if value:
                    out[label.lower()] = value
    return out


def parse_articulations(soup, page_path):
    """[{kind, joint, action}] from 'Force (Articulation)': kind label > joint li > action links."""
    rows, kind = [], None
    for block in section_blocks(soup, r"Force \(Articulation"):
        if block.name == "p":
            label = clean(block.get_text())
            if label:
                kind = label.lower()
            continue
        if block.name != "ul":
            continue
        for li in block.find_all("li", recursive=False):
            joint = clean("".join(c for c in li.children if isinstance(c, NavigableString)))
            for a in li.find_all("a", href=True):
                p = site_path(a["href"], page_path)
                if p and p.startswith("/Articulations/"):
                    rows.append({"kind": kind, "joint": joint or p.split("/")[2],
                                 "joint_path": p, "action": clean(a.get_text())})
    return rows


def parse_text_section(soup, title):
    """Instructions-style section: {subheading: text}; text before any subheading -> 'text'."""
    out, current = {}, "text"
    for block in section_blocks(soup, title):
        strong = block.find("strong")
        if block.name == "p" and strong and clean(strong.get_text()) == clean(block.get_text()):
            current = clean(strong.get_text()).rstrip(":").lower()
            continue
        txt = clean(block.get_text(" "))
        if txt:
            out[current] = (out.get(current, "") + " " + txt).strip()
    return out


def parse_muscles(soup, page_path):
    """[{role, muscle_path, name, head}] from the Muscles section (role labels precede <ul>s)."""
    rows, role = [], None
    for block in section_blocks(soup, "Muscles"):
        if block.name == "p":
            label = clean(block.get_text())
            if label:
                # "Dynamic Stabilizers (see comments)" -> dynamic_stabilizers
                label = re.sub(r"\(.*?\)|\bsee (comments?|notes?)\b", "", label, flags=re.I)
                role = re.sub(r"[^a-z]+", "_", label.lower()).strip("_")
                role = re.sub(r"(synergist|stabilizer)\b", r"\1s", role)  # singular -> plural
            continue
        if block.name != "ul" or role is None:
            continue
        for li in block.find_all("li"):
            a = li.find("a", recursive=False) or li.find("a")
            own_text = clean("".join(c if isinstance(c, NavigableString) else c.get_text()
                                     for c in li.children if not (isinstance(c, Tag) and c.name == "ul")))
            if not own_text:
                continue
            mpath = site_path(a["href"], page_path) if a and a.has_attr("href") else None
            if mpath and not mpath.startswith("/Muscles/"):
                mpath = None
            rows.append({"role": role, "muscle_path": mpath, "name": own_text})
    return rows


def parse_media(soup, page_path, ts):
    media = []
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if "/application/files/" not in src or "/cache/" in src or "logo" in src.lower():
            continue
        p = site_path(src, page_path)
        # exercise animations are GIFs; JPG/PNGs on these pages are store/ad banners
        if p and p.lower().endswith(".gif"):
            media.append({"kind": "image", "url": BASE + p,
                          "archive_url": f"https://web.archive.org/web/{ts}im_/{BASE}{p}"})
    for fr in soup.find_all("iframe", src=True):
        m = re.search(r"player\.vimeo\.com/video/(\d+)", fr["src"])
        if m:
            media.append({"kind": "video", "provider": "vimeo",
                          "url": f"https://vimeo.com/{m.group(1)}",
                          "embed_url": f"https://player.vimeo.com/video/{m.group(1)}"})
        m = re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", fr["src"])
        if m:
            media.append({"kind": "video", "provider": "youtube",
                          "url": f"https://www.youtube.com/watch?v={m.group(1)}",
                          "embed_url": fr["src"]})
    seen, uniq = set(), []
    for m in media:
        if m["url"] not in seen:
            seen.add(m["url"])
            uniq.append(m)
    return uniq


def parse_exercise(soup, path, ts):
    h1 = soup.find("h1")
    if not h1:
        return None
    muscles = parse_muscles(soup, path)
    articulations = parse_articulations(soup, path)
    instructions = parse_text_section(soup, "Instructions")
    if not muscles and not articulations and not instructions:
        return None  # index/landing page, not an exercise
    crumbs = [clean(a.get_text()) for a in h1.find_all_next("a", limit=4)
              if a.has_attr("href") and "ExList" in a["href"]]
    parts = path.strip("/").split("/")
    return {
        "key": slug(path),
        "path": path,
        "name": clean(h1.get_text()),
        "section": parts[0] if parts[0] != "Aerobic" else "Aerobic",
        "folder": parts[1] if len(parts) > 2 else None,
        "breadcrumb_group": crumbs[0] if crumbs else None,
        "classification": parse_classification(soup),
        "instructions": instructions,
        "comments": parse_text_section(soup, "Comments").get("text"),
        "muscles": muscles,
        "articulations": articulations,
        "media": parse_media(soup, path, ts),
        "source_url": BASE + path,
        "archive_timestamp": ts,
    }


def parse_muscle(soup, path):
    h1 = soup.find("h1")
    if not h1:
        return None
    rec = {"key": slug(path), "path": path, "name": clean(h1.get_text()), "source_url": BASE + path}
    for title in ("Other Names", "Heads", "Movement", "Attachments", "Comments"):
        blocks = section_blocks(soup, title)
        txt = clean(" ".join(b.get_text(" ") for b in blocks))
        if txt:
            rec[re.sub(r"\s+", "_", title.lower())] = txt
    related = []
    for b in section_blocks(soup, "Related Muscles"):
        for a in b.find_all("a", href=True):
            p = site_path(a["href"], path)
            if p and p.startswith("/Muscles/"):
                related.append(p)
    rec["related_muscles"] = sorted(set(related))
    rec["exercise_lists"] = sorted({p for a in soup.find_all("a", href=True)
                                    if (p := site_path(a["href"], path)) and "/Lists/ExList/" in p})
    return rec


def parse_listing(soup, path):
    """Walk ExList-style trees: h2 = muscle group, top <li> label = equipment, nesting = variation."""
    rows = []
    title = clean(soup.find("h1").get_text()) if soup.find("h1") else path

    def label_of(li):
        return clean("".join(c for c in li.children if isinstance(c, NavigableString)))

    def walk(ul, group, equipment, category, parent):
        for li in ul.find_all("li", recursive=False):
            a = li.find("a", recursive=False, href=True)
            ex_path = site_path(a["href"], path) if a else None
            is_ex = ex_path and ex_path.startswith(tuple("/" + s for s in EXERCISE_SECTIONS))
            label = label_of(li) if not a else ""
            eq, cat = equipment, category
            if not is_ex and label:
                if equipment is None:
                    eq = label
                else:
                    cat = label
            if is_ex:
                rows.append({"exercise_path": ex_path, "list_path": path, "list_title": title,
                             "group": group, "equipment": equipment, "subcategory": category,
                             "parent_path": parent})
            for sub in li.find_all("ul", recursive=False):
                walk(sub, group, eq, cat, ex_path if is_ex else parent)

    group = None
    for el in soup.find_all(["h2", "ul"]):
        if el.name == "h2":
            group = clean(el.get_text(" "))
        elif el.find_parent("ul") is None and el.find("a", href=re.compile("|".join(EXERCISE_SECTIONS))):
            walk(el, group, None, None, None)
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for line in (ROOT / "manifest.jsonl").open():
        r = json.loads(line)
        if r.get("ok"):
            manifest[r["path"]] = r
    exercises, muscles, listings = [], [], []
    for path, r in sorted(manifest.items()):
        soup = BeautifulSoup((ROOT / r["file"]).read_text(encoding="utf-8", errors="replace"), "html.parser")
        if path.startswith(tuple("/" + s for s in EXERCISE_SECTIONS)):
            ex = parse_exercise(soup, path, r["timestamp"])
            if ex:
                exercises.append(ex)
        elif path.startswith("/Muscles/"):
            m = parse_muscle(soup, path)
            if m:
                muscles.append(m)
        if path.startswith("/Lists/"):
            listings.extend(parse_listing(soup, path))
    for name, rows in (("exercises", exercises), ("muscles", muscles), ("listings", listings)):
        with (OUT / f"{name}.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{name}: {len(rows)}")


if __name__ == "__main__":
    main()
