"""Builds the text prompts sent to the AI (metadata mode and prompt-
generation mode)."""

def build_meta_prompt(title_c, desc_c, kw_n, custom_prompt="",
                      single_kw=False, themes="", prefix="", suffix_title="",
                      avoid_copyright=False, include_desc=True, content_phrase=""):
    directives = []
    if content_phrase:
        directives.append(
            f"This image is: {content_phrase}. This is a MANDATORY, TOP-PRIORITY fact — "
            f"the title MUST explicitly state it (using this phrase or an equivalent), "
            f"mentioned before other stylistic details, not left out or left to chance. "
            f"State it EXACTLY ONCE, near the start — do NOT also repeat or restate it "
            f"again later in the title (e.g. do not open with 'vector illustration' and "
            f"then ALSO close the title with 'A vector illustration.' — that wastes "
            f"character budget on a duplicate statement of the same fact instead of on "
            f"real descriptive content like subject details)."
        )
    if themes:
        directives.append(f"Content theme: {themes}. Reflect this in the metadata.")
    if single_kw:
        directives.append(f"Every keyword must be a single word only (no spaces or hyphens).")
    if avoid_copyright:
        directives.append(
            "Do not include any brand names, company names, trademarked terms, copyrighted "
            "character names, logos, product names, or celebrity names. Use only generic "
            "descriptive language instead (e.g. 'logo' not the brand name, 'sports car' not "
            "the manufacturer, 'cartoon character' not the character's name)."
        )
    if custom_prompt.strip():
        directives.append(
            f"MANDATORY COMMAND — override your defaults and apply this to title+"
            f"{'description+' if include_desc else ''}keywords: "
            f"\"{custom_prompt.strip()}\"")
    directive_block = ("\n\nEXTRA RULES:\n" +
        "\n".join(f"- {d}" for d in directives)) if directives else ""

    prefix_note = f' Start the title with: "{prefix}".' if prefix else ""
    suffix_note = f' End the title with: "{suffix_title}".' if suffix_title else ""
    title_words_lo = max((title_c-20)//6, 6)
    title_words_hi = max(title_c//5, title_words_lo+2)

    if not include_desc:
        # Description skipped entirely — not just shortened. The model's
        # whole token budget goes to title+keywords, which is exactly what
        # was requested: guarantee those two are solid rather than
        # spending tokens on a field that isn't being used at all.
        return (
            f"You are a professional stock image metadata writer for stock photo agencies.\n"
            f"Analyze the image carefully and return metadata in EXACTLY this format "
            f"(2 lines, nothing else before or after):\n\n"
            f"TITLE: <title>\n"
            f"KEYWORDS: <keywords>\n\n"
            f"STRICT REQUIREMENTS — every single one must be satisfied:\n"
            f"1. TITLE: Write a LONG, fully-detailed, keyword-rich title of "
            f"{max(title_c-20,10)}–{title_c} characters (roughly {title_words_lo}–{title_words_hi} words). "
            f"This is for a stock photo search listing — a short, generic, or vague title hurts "
            f"discoverability, so use as much of the allowed length as you can. Describe the "
            f"subject, action, setting, mood AND style in one flowing descriptive sentence — do "
            f"NOT just name the subject in a few words. The title MUST end as a complete, "
            f"well-formed sentence or phrase — NEVER cut off mid-word or mid-clause. If you are "
            f"close to the character limit, wrap the sentence up early rather than let it run out "
            f"unfinished; a shorter complete title is always better than a longer incomplete "
            f"one.{prefix_note}{suffix_note}\n"
            f"2. KEYWORDS: Write EXACTLY {kw_n} keywords separated by commas. "
            f"No fewer, no more. ORDER MATTERS A LOT: put the most relevant, "
            f"best-matched, highest-search-demand keywords FIRST — stock platforms "
            f"like Adobe Stock weight early keywords more heavily in search ranking, "
            f"so the strongest, most obviously-searched-for terms for this exact image "
            f"(main subject, then action/setting) belong at the front of the list, and "
            f"more niche, descriptive, or secondary terms (mood, color, style, abstract "
            f"concepts) belong toward the end. "
            f"No duplicates. No brand names. Cover subject/action/setting/mood/color/style.\n"
            f"3. Do NOT write a description or any other field. Output ONLY the 2 lines above. "
            f"No preamble, no markdown, no numbering, no extra explanation.{directive_block}"
        )

    # KEYWORDS is requested before DESCRIPTION — of the three fields it's
    # the one that was most often coming back empty/truncated, so it goes
    # where the model reaches it first, before it can burn its token
    # budget on the longer free-text description. The parser doesn't care
    # about label order — it scans for each label regardless of position.
    return (
        f"You are a professional stock image metadata writer for stock photo agencies.\n"
        f"Analyze the image carefully and return metadata in EXACTLY this format "
        f"(3 lines, nothing else before or after):\n\n"
        f"TITLE: <title>\n"
        f"KEYWORDS: <keywords>\n"
        f"DESCRIPTION: <description>\n\n"
        f"STRICT REQUIREMENTS — every single one must be satisfied:\n"
        f"1. TITLE: Write a LONG, fully-detailed, keyword-rich title of "
        f"{max(title_c-20,10)}–{title_c} characters (roughly {title_words_lo}–{title_words_hi} words). "
        f"This is for a stock photo search listing — a short, generic, or vague title hurts "
        f"discoverability, so use as much of the allowed length as you can. Describe the "
        f"subject, action, setting, mood AND style in one flowing descriptive sentence — do "
        f"NOT just name the subject in a few words. The title MUST end as a complete, "
        f"well-formed sentence or phrase — NEVER cut off mid-word or mid-clause. If you are "
        f"close to the character limit, wrap the sentence up early rather than let it run out "
        f"unfinished; a shorter complete title is always better than a longer incomplete "
        f"one.{prefix_note}{suffix_note}\n"
        f"2. KEYWORDS: Write EXACTLY {kw_n} keywords separated by commas. "
        f"No fewer, no more. ORDER MATTERS A LOT: put the most relevant, "
            f"best-matched, highest-search-demand keywords FIRST — stock platforms "
            f"like Adobe Stock weight early keywords more heavily in search ranking, "
            f"so the strongest, most obviously-searched-for terms for this exact image "
            f"(main subject, then action/setting) belong at the front of the list, and "
            f"more niche, descriptive, or secondary terms (mood, color, style, abstract "
            f"concepts) belong toward the end. "
        f"No duplicates. No brand names. Cover subject/action/setting/mood/color/style. "
        f"Write this field BEFORE the description.\n"
        f"3. DESCRIPTION: {max(desc_c-30,20)}–{desc_c} characters. Include subject, "
        f"mood, setting, use-case, colors. Just like the title, it MUST end as a complete "
        f"sentence — never cut off mid-word or mid-clause; finish the thought early rather "
        f"than run out of room unfinished.\n"
        f"4. Output ONLY the 3 lines. No preamble, no markdown, no numbering, "
        f"no extra explanation.{directive_block}"
    )


def build_prompt_prompt(max_words, styles, custom_prompt=""):
    style_str = ", ".join(styles) if styles else "realistic photography"
    extra = f"\n- MANDATORY: {custom_prompt.strip()}" if custom_prompt.strip() else ""
    return (
        f"You are an expert AI image generation prompt writer.\n"
        f"Analyze the image and write a detailed generation prompt.\n"
        f"Output ONLY the prompt text — no labels, no explanation.\n"
        f"Rules:\n"
        f"- Max {max_words} words.\n"
        f"- Style: {style_str}.\n"
        f"- Include: subject, lighting, colors, composition, mood, camera angle.\n"
        f"- Write as a flowing comma-separated description.{extra}"
    )

