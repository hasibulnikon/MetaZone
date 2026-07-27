"""Builds the text prompts sent to the AI (metadata mode and prompt-
generation mode)."""

def build_meta_prompt(title_c, desc_c, kw_n, custom_prompt="",
                      single_kw=False, themes="", prefix="", suffix_title="",
                      avoid_copyright=False, include_desc=True):
    directives = []
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
            f"NOT just name the subject in a few words.{prefix_note}{suffix_note}\n"
            f"2. KEYWORDS: Write EXACTLY {kw_n} keywords separated by commas. "
            f"No fewer, no more. Sort by relevance — most specific first. "
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
        f"NOT just name the subject in a few words.{prefix_note}{suffix_note}\n"
        f"2. KEYWORDS: Write EXACTLY {kw_n} keywords separated by commas. "
        f"No fewer, no more. Sort by relevance — most specific first. "
        f"No duplicates. No brand names. Cover subject/action/setting/mood/color/style. "
        f"Write this field BEFORE the description.\n"
        f"3. DESCRIPTION: {max(desc_c-30,20)}–{desc_c} characters. Include subject, "
        f"mood, setting, use-case, colors.\n"
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

