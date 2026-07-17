import os


def decode(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def tail(value, limit=4000):
    text = decode(value).strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def build_looks_cached(output):
    text = output or ""
    if any(marker in text for marker in (
        "Nothing to be done", " is up to date", "ninja: no work to do",
        "Nothing to do",
    )):
        return True
    return "Finished" in text and "Compiling " not in text


def build_is_cached(output, artifact_path, build_started_at, tolerance=2.0):
    """Whether the analyzed artifact predates this build (a genuine cache hit).

    ``build_looks_cached`` only scans build text, so a single up-to-date
    subdirectory in a recursive ``make`` makes it fire even when other targets
    recompiled. Confirm the text verdict against the artifact's mtime: if it was
    written during this build it is fresh, so the marker was a partial-cache
    false positive. ``tolerance`` absorbs coarse filesystem mtime granularity.
    Fall back to the text verdict when the artifact cannot be stat'd.
    """
    if not build_looks_cached(output):
        return False
    try:
        return os.path.getmtime(artifact_path) < build_started_at - tolerance
    except OSError:
        return True
