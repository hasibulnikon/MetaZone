"""Parses raw AI text output into title/description/keywords, plus
keyword-list post-processing (single-word enforcement, copyright-term
stripping)."""
def parse_meta(text):
    """
    Robust 3-pass parser:
    Pass 1 — exact prefix match on each line
    Pass 2 — looser case-insensitive scan with common label variants
    Pass 3 — positional fallback (line1=title, line2=desc, line3+=kw)
    """
    title = desc = kw = ""
    lines = [l.strip() for l in text.strip().splitlines()]

    def _after(line, prefix):
        return line[len(prefix):].strip()

    # Pass 1: exact prefix match
    i = 0
    while i < len(lines):
        u = lines[i].upper()
        if u.startswith("TITLE:") and not title:
            title = _after(lines[i], lines[i][:6])
        elif (u.startswith("DESCRIPTION:") or u.startswith("DESC:")) and not desc:
            tag_len = 12 if u.startswith("DESCRIPTION:") else 5
            desc = lines[i][tag_len:].strip()
            # absorb continuation lines (skip blanks, stop at next key)
            i += 1
            while i < len(lines):
                nxt = lines[i].upper()
                if nxt.startswith("KEYWORD") or nxt.startswith("TITLE:") or nxt.startswith("TAGS:"):
                    i -= 1; break
                if lines[i]:   # skip blank continuation lines — don't absorb
                    desc += " " + lines[i]
                i += 1
            desc = desc.strip()
        elif (u.startswith("KEYWORDS:") or u.startswith("KEYWORD:") or
              u.startswith("TAGS:") or u.startswith("KW:")) and not kw:
            col = lines[i].index(":") + 1
            kw = lines[i][col:].strip()
            i += 1
            while i < len(lines):
                nxt = lines[i].upper()
                if nxt.startswith("TITLE:") or nxt.startswith("DESCRIPTION:") or nxt.startswith("DESC:"):
                    i -= 1; break
                if lines[i]:
                    # Some models wrap the keyword list across lines without
                    # a trailing comma — joining with a plain space here used
                    # to silently glue two distinct keywords into one word
                    # (e.g. "...office" + "chair" -> "office chair" merged
                    # into the count), which is how a card could come back
                    # with far fewer keywords than requested.
                    if kw and not kw.rstrip().endswith(","):
                        kw += ","
                    kw += " " + lines[i]
                i += 1
            kw = kw.strip()
        i += 1

    # Pass 2: looser scan if any field still missing
    if not desc or not kw:
        for line in lines:
            u = line.upper().lstrip("*-# ")
            if not desc and any(u.startswith(p) for p in ["DESCRIPTION","DESC"]):
                desc = line.split(":",1)[-1].strip()
            if not kw and any(u.startswith(p) for p in ["KEYWORD","KW","TAG"]):
                kw = line.split(":",1)[-1].strip()

    # Pass 3: title/desc parsed fine but the model dropped the KEYWORDS:
    # label entirely — this used to leave kw empty even though the model
    # almost certainly still wrote a comma-separated list somewhere in the
    # response (this was the "14 of 30 cards skipped keywords" bug).
    # Salvage it: any leftover line that looks like a keyword list (lots of
    # commas, not the title/desc text, and not itself a label line) is used
    # instead of giving up. A malformed line like "DESCRIPTION:, possibly,
    # smile, extending, perfect" is still a label line even though its own
    # content is empty/broken — it must NOT be swept in as keywords, which
    # is exactly what happened before this exclusion existed.
    _LABEL_PREFIXES = ("TITLE:","DESCRIPTION:","DESC:","KEYWORDS:","KEYWORD:","TAGS:","KW:")
    if not kw and (title or desc):
        used = {title, desc}
        candidates = [l for l in lines if l and l not in used and l.count(",") >= 2
                      and not l.upper().lstrip("*-# ").startswith(_LABEL_PREFIXES)]
        if candidates:
            kw = ", ".join(candidates)

    # Pass 3: positional fallback — models sometimes drop the labels entirely
    if not title and not desc and not kw:
        non_blank = [l for l in lines if l]
        if len(non_blank) >= 1: title = non_blank[0]
        if len(non_blank) >= 2: desc  = non_blank[1]
        if len(non_blank) >= 3: kw    = ", ".join(non_blank[2:])

    return title.strip(), desc.strip(), kw.strip()


def smart_trim(text, max_len, must_include=None):
    """Trim text to max_len without leaving it mid-sentence. Prefers cutting
    at the last sentence-ending punctuation (. ! ?) within the limit; if
    none exists there, falls back to the last complete word, then strips
    any trailing comma/conjunction/dash so it doesn't read as if it were
    cut off mid-thought. Used as a last-resort safety net — the prompt
    itself already asks the model to finish its sentence within the
    requested length, so this should rarely have to trim much.

    If must_include is given (e.g. "isolated on a transparent background"
    for a content-type directive), the result is guaranteed to still
    contain it: trimming shrinks the rest of the sentence further to make
    room, rather than risk the mandatory phrase itself getting cut off,
    and it gets appended if the model left it out entirely but there's
    still room for it.
    """
    def _has(s):
        return must_include and must_include.lower() in s.lower()

    def _base_trim(s, limit):
        if len(s) <= limit:
            return s
        window = s[:limit]
        best_end = -1
        for punct in (".", "!", "?"):
            idx = window.rfind(punct)
            if idx > best_end:
                best_end = idx
        if best_end >= limit * 0.5:
            return window[:best_end + 1].strip()
        trimmed = window.rsplit(" ", 1)[0].strip()
        trimmed = trimmed.rstrip(",;:-–— ")
        for conj in (" and", " or", " with", " in", " on", " at", " of", " a", " the"):
            if trimmed.lower().endswith(conj):
                trimmed = trimmed[:-len(conj)].rstrip(",;:-–— ")
        return trimmed + "."

    result = _base_trim(text, max_len)

    if must_include and not _has(result):
        addition = must_include[0].upper() + must_include[1:]
        suffix = f", {addition}."
        budget = max_len - len(suffix)
        if budget > 15:
            shorter = _base_trim(text, budget).rstrip(". ")
            candidate = shorter + suffix
            if len(candidate) <= max_len:
                return candidate
        # Not enough room to safely add it without further mangling the
        # sentence — better to return the clean trim than force it in.
    return result


def enforce_single_keywords(kw_string):
    raw = [k.strip() for k in kw_string.split(",") if k.strip()]
    seen = set(); result = []
    for kw in raw:
        single = kw.split()[0] if kw.split() else kw
        if single.lower() not in seen:
            seen.add(single.lower()); result.append(single)
    return ", ".join(result)


# Common brand/trademark fragments to filter from keywords when avoid_copyright is on.
# This is a post-processing safety net — the main enforcement is in the prompt itself.
_COPYRIGHT_KW_BLOCKLIST = {
    "nike","adidas","puma","reebok","apple","iphone","android","samsung","sony","disney",
    "marvel","pixar","dc comics","warner bros","netflix","coca-cola","coke","pepsi",
    "mcdonalds","starbucks","google","microsoft","windows","facebook","instagram","twitter",
    "tesla","bmw","mercedes","audi","toyota","honda","ford","chevrolet","ferrari","lamborghini",
    "louis vuitton","gucci","chanel","prada","rolex","batman","superman","spiderman",
    "mickey mouse","hello kitty","pokemon","mario","minecraft","playstation","xbox","nintendo",
}

def _strip_copyright_keywords(kw_string):
    """Drop any keyword that matches (or contains) a known brand/trademark fragment."""
    if not kw_string:
        return kw_string
    raw = [k.strip() for k in kw_string.split(",") if k.strip()]
    kept = []
    for kw in raw:
        low = kw.lower()
        if any(term in low for term in _COPYRIGHT_KW_BLOCKLIST):
            continue
        kept.append(kw)
    return ", ".join(kept)
