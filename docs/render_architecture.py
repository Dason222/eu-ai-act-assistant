"""Generates the architecture diagrams from one spec:

* docs/architecture.drawio  - editable in diagrams.net (2 pages: prototype components, target production)
* docs/architecture.svg/png, docs/architecture_production.svg/png - rendered previews (Chromium via Playwright)

Run: python docs/render_architecture.py
"""

from __future__ import annotations

import glob
import html
from pathlib import Path

DOCS = Path(__file__).resolve().parent

# layer colours (fill, stroke)
C = {
    "ext": ("#F5F5F5", "#666666"),
    "ingest": ("#DAE8FC", "#6C8EBF"),
    "store": ("#FFF2CC", "#D6B656"),
    "query": ("#D5E8D4", "#82B366"),
    "llm": ("#E1D5E7", "#9673A6"),
    "ui": ("#FFE6CC", "#D79B00"),
    "qa": ("#F8CECC", "#B85450"),
    "ops": ("#E6E6E6", "#4D4D4D"),
}

W, H = 200, 78  # default node size

PROTOTYPE = {
    "name": "Prototype - component view",
    "size": (1700, 1060),
    "lanes": [
        ("Offline ingestion (batch, `aiact-qa ingest`)", 20, 40, 1660, 230, "ingest"),
        ("Online query pipeline (QAEngine)", 20, 300, 1660, 470, "query"),
        ("Quality loop", 20, 800, 1660, 230, "qa"),
    ],
    "nodes": [
        # id, title, detail (input -> output), x, y, layer, w, h
        ("eurlex", "EUR-Lex", "ELI 2024/1689\n(official HTML)", 40, 110, "ext", 160, 78),
        ("fetch", "Fetch & snapshot", "URL -> HTML snapshot\n+ sha256", 280, 110, "ingest", 190, 78),
        ("parse", "Structure-aware parser", "HTML -> 113 articles, 180 recitals,\n13 annexes, 68 definitions", 550,
         110, "ingest", 220, 78),
        ("chunk", "Chunker", "provisions -> 570 chunks\n(<=280 words, breadcrumbs)", 850, 110, "ingest", 190, 78),
        ("embed", "Embedder", "bge-small (ONNX, CPU)\nchunks -> 384-d vectors", 1120, 110, "ingest", 210, 78),
        ("store", "Knowledge store", "regulation.json, chunks.json,\nembeddings.npy, BM25 (in-memory)", 1410, 110,
         "store", 250, 78),
        ("ui", "Streamlit chat UI", "question, model choice -> streamed\nanswer, sources, feedback", 40, 340, "ui",
         210, 78),
        ("api", "REST API (FastAPI)", "/ask, /ask/stream (SSE), /search,\n/provisions, /providers, /feedback", 40,
         470, "ui", 210, 78),
        ("cli", "CLI / notebook", "ingest | ask | search | eval", 40, 600, "ui", 210, 62),
        ("guard", "Input guardrails", "question + history -> validated\n(length, history window)", 330, 340,
         "query", 200, 78),
        ("condense", "Query condenser", "follow-up / non-English ->\nstandalone English query", 610, 340, "query",
         200, 78),
        ("retrieve", "Hybrid retriever", "references + BM25 + dense -> RRF\n-> ranked, diverse chunks", 890, 340,
         "query", 230, 78),
        ("gate", "Relevance gate", "best similarity < 0.55, no reference\n-> out-of-scope reply, no LLM call",
         1200, 340, "query", 240, 78),
        ("context", "Context builder", "top-k chunks -> numbered excerpts\n(6k-word budget) + cached ToC", 1200,
         520, "query", 240, 78),
        ("llm", "LLM gateway (provider adapter)", "system + excerpts + question ->\nstreamed grounded answer", 890,
         520, "llm", 230, 78),
        ("cite", "Citation validator", "[Article 6(2)] -> provision id,\nverified vs sources + EUR-Lex link", 610,
         520, "query", 200, 78),
        ("answer", "Answer", "answer, sources, citations, mode,\nusage, latency, warnings", 330, 520, "query",
         200, 78),
        ("providers", "LLM providers", "Anthropic Claude (default) | OpenAI |\nAzure OpenAI | Mistral (EU) |\n"
         "self-hosted Ollama/vLLM | EU OpenAI-compatible", 870, 655, "ext", 270, 92),
        ("golden", "Golden set (57 Q)", "verified vs. the text\n(13 categories)", 330, 870, "qa", 200, 78),
        ("runner", "Evaluation runner", "questions -> hit@k, recall, MRR,\ncitation precision, must-contain",
         610, 870, "qa", 230, 78),
        ("judge", "LLM judge (optional)", "answer + excerpts ->\nfaithfulness, completeness", 920, 870, "qa", 210,
         78),
        ("feedback", "Feedback log", "thumbs up/down from UI and API\n(JSONL) -> new test cases", 1210, 870, "qa",
         230, 78),
    ],
    "edges": [
        ("eurlex", "fetch", "HTML"), ("fetch", "parse", "snapshot"), ("parse", "chunk", "provisions"),
        ("chunk", "embed", "chunks"), ("embed", "store", "vectors"),
        ("ui", "guard", "question"), ("api", "guard", ""), ("guard", "condense", ""),
        ("condense", "retrieve", "query"), ("store", "retrieve", "index"), ("retrieve", "gate", "hits"),
        ("gate", "context", "in scope"), ("context", "llm", "prompt"), ("llm", "providers", "API call / stream"),
        ("llm", "cite", "draft"), ("cite", "answer", "checked"), ("answer", "api", "JSON / SSE"),
        ("golden", "runner", ""), ("runner", "cli", "aiact-qa eval"), ("runner", "judge", ""),
    ],
}

PRODUCTION = {
    "name": "Target production architecture",
    "size": (1700, 1040),
    "lanes": [
        ("Channels & edge", 20, 40, 1660, 150, "ui"),
        ("Q&A services (stateless, autoscaled containers)", 20, 220, 1660, 270, "query"),
        ("Data & knowledge platform", 20, 520, 1660, 260, "store"),
        ("Operations, governance & quality", 20, 810, 1660, 200, "ops"),
    ],
    "nodes": [
        ("users", "Users", "compliance, legal,\nproduct teams", 40, 80, "ext", 200, 78),
        ("channels", "Web chat | Teams bot | API clients", "SSO session / API key", 310, 80, "ui", 250, 78),
        ("edge", "API gateway / Traefik", "TLS, SSO (OIDC / Entra ID), WAF,\nrate limits, quotas", 640, 80, "ui",
         260, 78),
        ("qa", "Q&A orchestrator", "guardrails, condensing, routing,\nconversation state", 640, 260, "query",
         260, 78),
        ("gw", "LLM gateway", "provider routing & fallback, PII redaction,\nprompt cache, budgets, logging", 990,
         260, "llm", 290, 78),
        ("prov", "LLM providers", "Claude | Azure OpenAI (EU) | Mistral (EU)\n| self-hosted open-weights", 1360,
         260, "ext", 290, 78),
        ("retr", "Retrieval service", "hybrid search + reranker, reference\nresolver, filters by act / version", 640,
         390, "query", 260, 78),
        ("val", "Answer validation", "citation + groundedness checks,\naudit record", 990, 390, "query", 290, 70),
        ("cellar", "EUR-Lex / Cellar", "SPARQL + REST: new acts, corrigenda,\nconsolidated texts, 24 languages",
         40, 620, "ext", 240, 78),
        ("pipe", "Ingestion pipeline", "scheduled: fetch -> parse -> chunk\n-> embed; versioned, idempotent", 360,
         620, "ingest", 240, 78),
        ("vec", "Vector + keyword index", "pgvector / Qdrant + OpenSearch BM25,\nper act, language, version", 680,
         550, "store", 260, 78),
        ("docs", "Document store", "provisions JSON, snapshots, sha256,\nvalidity dates, lineage", 680, 670,
         "store", 260, 78),
        ("conv", "Conversation & audit DB", "sessions, answers, ratings, comments\n(retention policy)", 990, 620,
         "store", 290, 78),
        ("obs", "Observability", "OpenTelemetry traces, Langfuse,\nlatency, cost per answer", 40, 870, "ops", 250,
         78),
        ("eval", "Continuous evaluation", "SME-reviewed golden set, CI gate,\nonline sampling + LLM judge", 340, 870,
         "ops", 270, 78),
        ("gov", "Governance & security", "audit log, access control, DPIA,\nAI Act Art. 50 transparency", 660, 870,
         "ops", 260, 78),
        ("cicd", "CI/CD & IaC", "tests, eval gate, registry,\nTerraform, blue/green", 970, 870, "ops", 240, 78),
        ("sec", "Secrets & keys", "Key Vault / Secrets Manager", 1260, 870, "ops", 240, 62),
    ],
    "edges": [
        ("users", "channels", ""), ("channels", "edge", "HTTPS"), ("edge", "qa", "authenticated request"),
        ("qa", "gw", "prompt"), ("gw", "prov", "API"), ("qa", "retr", "query"), ("qa", "val", "draft"),
        ("retr", "vec", "search"), ("cellar", "pipe", "new acts"), ("pipe", "vec", "index"),
        ("pipe", "docs", "store"), ("val", "conv", "answer + audit"),
    ],
}


# --------------------------------------------------------------------------------------------- #
def _nodes(spec: dict) -> dict[str, dict]:
    out = {}
    for n in spec["nodes"]:
        nid, title, detail, x, y, layer = n[:6]
        w, h = (n[6], n[7]) if len(n) > 6 else (W, H)
        out[nid] = {"id": nid, "title": title, "detail": detail, "x": x, "y": y, "w": w, "h": h, "layer": layer}
    return out


def _anchor(a: dict, b: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    """Connection points on the facing sides of two boxes."""
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    dx, dy = bx - ax, by - ay
    if abs(dx) * a["h"] >= abs(dy) * a["w"]:  # mostly horizontal
        sa = (a["x"] + (a["w"] if dx > 0 else 0), ay)
        sb = (b["x"] + (0 if dx > 0 else b["w"]), by)
    else:
        sa = (ax, a["y"] + (a["h"] if dy > 0 else 0))
        sb = (bx, b["y"] + (0 if dy > 0 else b["h"]))
    return sa, sb


def to_svg(spec: dict) -> str:
    width, height = spec["size"]
    nodes = _nodes(spec)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif">',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
        '<path d="M0,0 L10,4 L0,8 z" fill="#444"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        f'<text x="24" y="26" font-size="18" font-weight="bold" fill="#222">'
        f'EU AI Act Q&amp;A - {html.escape(spec["name"])}</text>',
    ]
    for title, x, y, w, h, layer in spec["lanes"]:
        fill, stroke = C[layer]
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" fill-opacity="0.25" '
                     f'stroke="{stroke}" stroke-dasharray="6 4"/>')
        parts.append(f'<text x="{x + 12}" y="{y + 20}" font-size="13" font-weight="bold" fill="{stroke}">'
                     f'{html.escape(title)}</text>')
    for src, dst, label in spec["edges"]:
        (x1, y1), (x2, y2) = _anchor(nodes[src], nodes[dst])
        dash = ' stroke-dasharray="5 4"' if label in {"out of scope", "rating", "test traffic"} else ""
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#444" stroke-width="1.5"{dash} '
                     'marker-end="url(#arrow)"/>')
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            tw = 6.2 * len(label) + 8
            parts.append(f'<rect x="{mx - tw / 2}" y="{my - 9}" width="{tw}" height="16" rx="3" fill="#FFFFFF" '
                         'fill-opacity="0.9"/>')
            parts.append(f'<text x="{mx}" y="{my + 3}" font-size="11" fill="#333" text-anchor="middle">'
                         f'{html.escape(label)}</text>')
    for n in nodes.values():
        fill, stroke = C[n["layer"]]
        cx = n["x"] + n["w"] / 2
        parts.append(f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" rx="8" fill="{fill}" '
                     f'stroke="{stroke}" stroke-width="1.5"/>')
        parts.append(f'<text x="{cx}" y="{n["y"] + 20}" font-size="13" font-weight="bold" fill="#111" '
                     f'text-anchor="middle">{html.escape(n["title"])}</text>')
        for i, line in enumerate(n["detail"].split("\n")):
            parts.append(f'<text x="{cx}" y="{n["y"] + 38 + i * 14}" font-size="11" fill="#333" '
                         f'text-anchor="middle">{html.escape(line)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def to_drawio_page(spec: dict, page_id: str) -> str:
    nodes = _nodes(spec)
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    title = html.escape(f"EU AI Act Q&A - {spec['name']}")
    cells.append(f'<mxCell id="{page_id}-title" value="{title}" style="text;fontSize=18;fontStyle=1;" vertex="1" '
                 'parent="1"><mxGeometry x="20" y="4" width="900" height="30" as="geometry"/></mxCell>')
    for i, (lane, x, y, w, h, layer) in enumerate(spec["lanes"]):
        fill, stroke = C[layer]
        style = (f"rounded=1;arcSize=3;dashed=1;fillColor={fill};opacity=40;strokeColor={stroke};verticalAlign=top;"
                 f"align=left;spacingLeft=10;fontStyle=1;fontColor={stroke};")
        cells.append(f'<mxCell id="{page_id}-lane{i}" value="{html.escape(lane)}" style="{style}" vertex="1" '
                     f'parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    for n in nodes.values():
        fill, stroke = C[n["layer"]]
        value = html.escape(f"<b>{html.escape(n['title'])}</b><br><font style='font-size:10px'>"
                            + "<br>".join(html.escape(x) for x in n["detail"].split("\n")) + "</font>")
        style = f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=12;"
        cells.append(f'<mxCell id="{page_id}-{n["id"]}" value="{value}" style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" as="geometry"/>'
                     "</mxCell>")
    for i, (src, dst, label) in enumerate(spec["edges"]):
        dashed = "dashed=1;" if label in {"out of scope", "rating", "test traffic"} else ""
        style = f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;fontSize=10;{dashed}"
        cells.append(f'<mxCell id="{page_id}-e{i}" value="{html.escape(label)}" style="{style}" edge="1" '
                     f'parent="1" source="{page_id}-{src}" target="{page_id}-{dst}">'
                     '<mxGeometry relative="1" as="geometry"/></mxCell>')
    width, height = spec["size"]
    return (f'<diagram id="{page_id}" name="{html.escape(spec["name"])}"><mxGraphModel dx="{width}" dy="{height}" '
            f'grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" '
            f'pageScale="1" pageWidth="{width}" pageHeight="{height}" math="0" shadow="0"><root>'
            + "".join(cells) + "</root></mxGraphModel></diagram>")


def render_png(svg_path: Path, png_path: Path, size: tuple[int, int]) -> None:
    from playwright.sync_api import sync_playwright

    candidates = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    with sync_playwright() as p:
        args = ["--disable-gpu", "--no-sandbox"]
        browser = p.chromium.launch(executable_path=candidates[0] if candidates else None, args=args)
        page = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1.5)
        page.set_content(f"<html><body style='margin:0'>{svg_path.read_text(encoding='utf-8')}</body></html>")
        page.screenshot(path=str(png_path), clip={"x": 0, "y": 0, "width": size[0], "height": size[1]},
                        animations="disabled", caret="initial", timeout=60000)
        browser.close()


def main() -> None:
    pages = [(PROTOTYPE, "architecture", "proto"), (PRODUCTION, "architecture_production", "prod")]
    drawio = ['<mxfile host="app.diagrams.net" type="device">']
    for spec, stem, page_id in pages:
        svg = DOCS / f"{stem}.svg"
        svg.write_text(to_svg(spec), encoding="utf-8")
        render_png(svg, DOCS / f"{stem}.png", spec["size"])
        drawio.append(to_drawio_page(spec, page_id))
    drawio.append("</mxfile>")
    (DOCS / "architecture.drawio").write_text("\n".join(drawio), encoding="utf-8")
    print("written:", ", ".join(sorted(p.name for p in DOCS.glob("architecture*"))))


if __name__ == "__main__":
    main()
