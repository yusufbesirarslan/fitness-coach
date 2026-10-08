"""Pre-allocation structural admission for all remote menu HTML/JSON paths."""
import json
from html.parser import HTMLParser

MAX_INPUT = 3_000_000
MAX_NODES = 6000
MAX_DEPTH = 64
MAX_OUTPUT = 40000
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())


def bounded_soup(text):
    from bs4 import BeautifulSoup
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT:
        raise ValueError("MENU_PARSE_LIMIT")

    class Admission(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack = []
            self.nodes = 0

        def count(self):
            self.nodes += 1
            if self.nodes > MAX_NODES:
                raise ValueError("MENU_PARSE_LIMIT")

        def handle_starttag(self, tag, attrs):
            self.count()
            if len(attrs) > 64 or len(self.get_starttag_text()) > 8192:
                raise ValueError("MENU_PARSE_LIMIT")
            if tag not in _VOID:
                self.stack.append(tag)
                if len(self.stack) > MAX_DEPTH:
                    raise ValueError("MENU_PARSE_LIMIT")

        def handle_endtag(self, tag):
            if tag in self.stack:
                del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

        def handle_data(self, data):
            self.count()

        def handle_comment(self, data):
            self.count()

        def handle_entityref(self, name):
            self.count()

        def handle_charref(self, name):
            self.count()

        def handle_pi(self, data):
            self.count()

        def handle_decl(self, decl):
            self.count()

        def unknown_decl(self, data):
            self.count()

    parser = Admission()
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        raise ValueError("MENU_PARSE_LIMIT") from None
    soup = BeautifulSoup(text, "html.parser")
    for index, node in enumerate(soup.descendants, 1):
        if index > MAX_NODES or sum(1 for _ in node.parents) > MAX_DEPTH + 1:
            raise ValueError("MENU_PARSE_LIMIT")
    return soup


def bounded_json(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT:
        raise ValueError("MENU_PARSE_LIMIT")
    depth = nodes = 0
    quoted = escaped = False
    for ch in text:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
            nodes += 1
        elif ch in "[{":
            depth += 1
            nodes += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ",":
            nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise ValueError("MENU_PARSE_LIMIT")
    return json.loads(text)


def bounded_text(node, limit=40000):
    """Stop traversal/output construction at the cap, rather than slice a join."""
    parts = []
    left = limit
    for value in node.stripped_strings:
        if left <= 0:
            break
        part = value[:left]
        parts.append(part)
        left -= len(part) + 1
    return "\n".join(parts)[:limit]


def bound_sections(sections):
    result = []
    left = MAX_OUTPUT
    for sec in sections[:100]:
        if left <= 0:
            break
        category = str(sec.get("category", "Genel"))[:256]
        text = str(sec.get("text", ""))[:max(0, left - len(category) - 4)]
        result.append({"category": category, "text": text})
        left -= len(text) + len(category) + 4
    return result


class SectionAccumulator(list):
    """Bound retained sections during extraction, including repeated containers."""
    def __init__(self):
        super().__init__()
        self.left = MAX_OUTPUT

    def append(self, section):
        if self.left <= 4 or len(self) >= 100:
            return
        category = str(section.get("category", "Genel"))[:min(256, self.left - 4)]
        text = str(section.get("text", ""))[:max(0, self.left - len(category) - 4)]
        super().append({"category": category, "text": text})
        self.left -= len(category) + len(text) + 4

    def extend(self, sections):
        for section in sections:
            self.append(section)
