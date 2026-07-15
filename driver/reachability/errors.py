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
