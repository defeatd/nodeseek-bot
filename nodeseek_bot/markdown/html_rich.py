from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from nodeseek_bot.utils import collapse_ws, truncate


@dataclass(frozen=True)
class RichTextConfig:
    enabled: bool = True
    max_chars: int = 20000
    max_code_blocks: int = 6
    max_code_chars_total: int = 6000
    max_table_rows: int = 30
    max_links: int = 40


class _Budget:
    def __init__(self, cfg: RichTextConfig):
        self.cfg = cfg
        self.code_blocks_used = 0
        self.code_chars_used = 0
        self.links_used = 0

    def can_add_code_block(self, n_chars: int) -> bool:
        if self.code_blocks_used >= int(self.cfg.max_code_blocks):
            return False
        if self.code_chars_used + n_chars > int(self.cfg.max_code_chars_total):
            return False
        return True

    def add_code_block(self, n_chars: int) -> None:
        self.code_blocks_used += 1
        self.code_chars_used += n_chars

    def can_add_link(self) -> bool:
        return self.links_used < int(self.cfg.max_links)

    def add_link(self) -> None:
        self.links_used += 1


def _node_text(node: Node) -> str:
    return collapse_ws(node.text(separator="\n"))


def _safe_join(base_url: str, maybe_url: str) -> str:
    u = (maybe_url or "").strip()
    if not u:
        return ""
    if base_url:
        return urljoin(base_url, u)
    return u


def _append_nonempty(lines: list[str], s: str) -> None:
    s = (s or "").strip()
    if s:
        lines.append(s)


def _render_table(table: Node, cfg: RichTextConfig) -> list[str]:
    rows = table.css("tr")
    if not rows:
        return []

    out: list[list[str]] = []
    for r in rows[: max(1, int(cfg.max_table_rows))]:
        cells = r.css("th, td")
        if not cells:
            continue
        out.append([collapse_ws(c.text(separator=" ")) for c in cells])

    if not out:
        return []

    # Normalize column count
    ncol = max(len(r) for r in out)
    for r in out:
        while len(r) < ncol:
            r.append("")

    lines: list[str] = []
    header = out[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * ncol) + " |")
    for r in out[1:]:
        lines.append("| " + " | ".join(r) + " |")

    return lines


def _render_list(list_node: Node, ordered: bool, base_url: str, indent: int, budget: _Budget) -> list[str]:
    lines: list[str] = []
    items = list_node.css("> li")
    for idx, li in enumerate(items, start=1):
        prefix = f"{idx}. " if ordered else "- "
        pad = "  " * indent

        # Render non-nested content first
        parts: list[str] = []
        for child in li.iter(include_text=False):
            # stop at nested lists: handled separately
            if child.tag in {"ul", "ol"}:
                break
        # A simpler approach: take li.text but it includes nested list text; we try to remove it
        txt = collapse_ws(li.text(separator=" "))
        if txt:
            parts.append(txt)
        line = pad + prefix + " ".join(parts).strip()
        _append_nonempty(lines, line)

        # nested lists
        for child in li.css("> ul, > ol"):
            lines.extend(_render_list(child, ordered=(child.tag == "ol"), base_url=base_url, indent=indent + 1, budget=budget))

    return lines


def _walk(node: Node, *, base_url: str, indent: int, budget: _Budget, out: list[str], cfg: RichTextConfig) -> None:
    tag = (node.tag or "").lower()

    if tag in {"script", "style", "noscript"}:
        return

    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag[1])
        txt = _node_text(node)
        _append_nonempty(out, "#" * level + " " + txt)
        return

    if tag == "p":
        txt = _node_text(node)
        _append_nonempty(out, txt)
        return

    if tag == "br":
        out.append("")
        return

    if tag == "blockquote":
        txt = _node_text(node)
        if txt:
            for line in txt.split("\n"):
                _append_nonempty(out, "> " + line)
            out.append("")
        return

    if tag == "pre":
        code_node = node.css_first("code")
        code_text = (code_node.text() if code_node is not None else node.text()) or ""
        code_text = code_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not code_text:
            return

        trimmed = code_text
        if len(trimmed) > 2000:
            trimmed = trimmed[:2000] + "\n…"

        if not budget.can_add_code_block(len(trimmed)):
            return

        budget.add_code_block(len(trimmed))
        out.append("```")
        out.append(trimmed)
        out.append("```")
        out.append("")
        return

    if tag == "table":
        out.extend(_render_table(node, cfg))
        out.append("")
        return

    if tag == "ul":
        out.extend(_render_list(node, ordered=False, base_url=base_url, indent=indent, budget=budget))
        out.append("")
        return

    if tag == "ol":
        out.extend(_render_list(node, ordered=True, base_url=base_url, indent=indent, budget=budget))
        out.append("")
        return

    if tag == "img":
        alt = (node.attributes.get("alt") or "").strip()
        src = _safe_join(base_url, node.attributes.get("src") or node.attributes.get("data-src") or "")
        if src:
            _append_nonempty(out, f"[image] {alt} {src}".strip())
        return

    if tag == "a":
        href = _safe_join(base_url, node.attributes.get("href") or "")
        txt = collapse_ws(node.text(separator=" "))
        if href and budget.can_add_link():
            budget.add_link()
            if txt:
                _append_nonempty(out, f"[{txt}]({href})")
            else:
                _append_nonempty(out, href)
        else:
            _append_nonempty(out, txt)
        return

    # Default: walk children
    for child in node.child_nodes:
        if child is None:
            continue
        if getattr(child, "tag", None) is None:
            # text node
            txt = collapse_ws(str(child))
            if txt:
                _append_nonempty(out, txt)
            continue
        _walk(child, base_url=base_url, indent=indent, budget=budget, out=out, cfg=cfg)


def html_to_rich_text(html: str, *, base_url: str = "", cfg: RichTextConfig | None = None) -> str:
    """Convert (post body) HTML into a markdown-like rich text.

    Goal: keep structure that corresponds to Markdown features (headings/lists/code/tables/quotes/links/images).
    """
    if not html:
        return ""

    c = cfg or RichTextConfig()
    if not c.enabled:
        return ""

    tree = HTMLParser(html)

    # choose a reasonable root
    root = tree.css_first("article") or tree.css_first(".post-content") or tree.css_first(".topic-content") or tree.css_first(".markdown-body")
    if root is None:
        root = tree.root

    budget = _Budget(c)
    out: list[str] = []
    _walk(root, base_url=base_url, indent=0, budget=budget, out=out, cfg=c)

    text = "\n".join([line.rstrip() for line in out])
    text = text.replace("\n\n\n", "\n\n")
    return truncate(text.strip(), int(c.max_chars))
