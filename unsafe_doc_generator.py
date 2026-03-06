#!/usr/bin/env python3
import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class UnsafeItem:
    index: int
    module_path: str
    api_name: str
    item_type: str
    full_doc: str
    safety_doc: str
    source_path: str
    source_line: int
    api_link: str


def walk_local_rust_files(local_dir: Path) -> List[Tuple[str, str]]:
    root = local_dir.resolve()
    found: List[Tuple[str, str]] = []
    for path in sorted(root.rglob("*.rs")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        found.append((relative, path.as_uri()))
    return found


def normalize_remote_repo_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned


def build_remote_api_link(remote_repo_url: str, remote_ref: str, source_path: str, source_line: int) -> str:
    if not remote_repo_url:
        return ""
    ref = remote_ref.strip() if remote_ref else "main"
    safe_path = source_path.lstrip("/")
    return f"{normalize_remote_repo_url(remote_repo_url)}/blob/{ref}/{safe_path}#L{source_line}"


def find_git_repo_root(path: Path) -> Optional[Path]:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def join_posix(prefix: str, suffix: str) -> str:
    left = prefix.strip("/")
    right = suffix.strip("/")
    if not left:
        return right
    if not right:
        return left
    return f"{left}/{right}"


def resolve_remote_source_path(
    local_dir: Path,
    absolute_path: Path,
    local_relative_path: str,
    remote_path_prefix: str,
) -> str:
    if remote_path_prefix.strip():
        return join_posix(remote_path_prefix, local_relative_path)

    repo_root = find_git_repo_root(local_dir)
    if repo_root is not None:
        try:
            return absolute_path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            pass

    return local_relative_path


def clean_comment_line(line: str) -> str:
    stripped = line.strip()
    prefixes = ("///", "//!", "//", "/**", "/*", "*/", "*")
    for p in prefixes:
        if stripped.startswith(p):
            return stripped[len(p):].strip()
    return stripped


def extract_comment_block(lines: List[str], item_line_idx: int) -> List[str]:
    comments: List[str] = []
    i = item_line_idx - 1
    saw_comment = False

    while i >= 0:
        raw = lines[i].rstrip("\n")
        stripped = raw.strip()

        if stripped == "":
            if saw_comment:
                comments.append(raw)
                i -= 1
                continue
            i -= 1
            continue

        if stripped.startswith("#") and not stripped.startswith("##") and "[" in stripped and "]" in stripped:
            i -= 1
            continue

        if stripped.startswith(("///", "//!", "//", "/**", "/*", "*", "*/")):
            saw_comment = True
            comments.append(raw)
            i -= 1
            continue

        break

    comments.reverse()
    return comments


def extract_full_doc(comment_lines: List[str]) -> str:
    if not comment_lines:
        return ""

    cleaned = [clean_comment_line(line) for line in comment_lines]
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    if not cleaned:
        return ""

    return "\n".join(cleaned).strip()


def extract_safety_doc_from_full_doc(full_doc: str) -> str:
    if not full_doc:
        return ""

    lines = full_doc.splitlines()
    if not lines:
        return ""

    safety_header_index = -1
    for idx, line in enumerate(lines):
        normalized = line.strip().lower()
        if normalized.startswith("# safety"):
            safety_header_index = idx
            break

    if safety_header_index >= 0:
        section: List[str] = [lines[safety_header_index]]
        for i in range(safety_header_index + 1, len(lines)):
            current = lines[i]
            stripped = current.strip()
            if stripped.startswith("#") and stripped.lower() != "# safety":
                break
            section.append(current)
        return "\n".join(section).strip()

    for line in lines:
        if line.strip().lower().startswith("safety:"):
            return line.strip()

    return ""


def module_path_from_file_path(file_path: str) -> str:
    normalized = file_path.strip("/")
    parts = [p for p in normalized.split("/") if p]

    if "src" in parts:
        idx = parts.index("src")
        parts = parts[idx + 1 :]

    if not parts:
        return "crate"

    filename = parts[-1]
    if filename.endswith(".rs"):
        stem = filename[:-3]
        if stem in ("lib", "main", "mod"):
            parts = parts[:-1]
        else:
            parts[-1] = stem

    return "crate" + ("::" + "::".join(parts) if parts else "")


def classify_context(stack: List[Tuple[str, int, Optional[str]]]) -> Tuple[str, Optional[str]]:
    for kind, _, trait_name in reversed(stack):
        if kind == "pub_unsafe_trait":
            return "pub_unsafe_trait", trait_name
        if kind == "impl":
            return "impl", None
    return "root", None


def parse_unsafe_items(
    rust_code: str,
    file_path: str,
    remote_repo_url: str = "",
    remote_ref: str = "main",
    remote_source_path: str = "",
) -> List[UnsafeItem]:
    lines = rust_code.splitlines(keepends=True)
    items: List[UnsafeItem] = []

    re_pub_unsafe_fn = re.compile(r"^\s*pub\s+unsafe\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    re_trait_unsafe_fn = re.compile(r"^\s*(?:pub\s+)?unsafe\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    re_pub_unsafe_trait = re.compile(r"^\s*pub\s+unsafe\s+trait\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    re_impl = re.compile(r"^\s*impl\b")
    link_source_path = remote_source_path if remote_source_path else file_path

    brace_level = 0
    stack: List[Tuple[str, int, Optional[str]]] = []
    pending_contexts: List[Tuple[str, Optional[str]]] = []

    for idx, line in enumerate(lines):
        m_trait = re_pub_unsafe_trait.search(line)
        if m_trait:
            comment_block = extract_comment_block(lines, idx)
            full_doc = extract_full_doc(comment_block)
            items.append(
                UnsafeItem(
                    index=0,
                    module_path=module_path_from_file_path(file_path),
                    api_name=m_trait.group(1),
                    item_type="trait",
                    full_doc=full_doc,
                    safety_doc=extract_safety_doc_from_full_doc(full_doc),
                    source_path=file_path,
                    source_line=idx + 1,
                    api_link=build_remote_api_link(remote_repo_url, remote_ref, link_source_path, idx + 1),
                )
            )
            pending_contexts.append(("pub_unsafe_trait", m_trait.group(1)))

        elif re_impl.search(line):
            pending_contexts.append(("impl", None))

        context, context_trait_name = classify_context(stack)
        if context == "pub_unsafe_trait":
            m_fn = re_trait_unsafe_fn.search(line)
            if not m_fn:
                m_fn = None
        else:
            m_fn = re_pub_unsafe_fn.search(line)

        if m_fn:
            if context == "pub_unsafe_trait":
                item_type = "trait_method"
                api_name = f"{context_trait_name}::{m_fn.group(1)}" if context_trait_name else m_fn.group(1)
            elif context == "impl":
                item_type = "method"
                api_name = m_fn.group(1)
            else:
                item_type = "function"
                api_name = m_fn.group(1)

            comment_block = extract_comment_block(lines, idx)
            full_doc = extract_full_doc(comment_block)
            items.append(
                UnsafeItem(
                    index=0,
                    module_path=module_path_from_file_path(file_path),
                    api_name=api_name,
                    item_type=item_type,
                    full_doc=full_doc,
                    safety_doc=extract_safety_doc_from_full_doc(full_doc),
                    source_path=file_path,
                    source_line=idx + 1,
                    api_link=build_remote_api_link(remote_repo_url, remote_ref, link_source_path, idx + 1),
                )
            )

        for ch in line:
            if ch == "{":
                brace_level += 1
                if pending_contexts:
                    kind, trait_name = pending_contexts.pop(0)
                else:
                    kind, trait_name = "other", None
                stack.append((kind, brace_level, trait_name))
            elif ch == "}":
                brace_level = max(0, brace_level - 1)
                while stack and stack[-1][1] > brace_level:
                    stack.pop()

    return items


def build_html(items: List[UnsafeItem], source_url: str) -> str:
    rows: List[str] = []
    for item in items:
        full_doc_html = escape(item.full_doc).replace("\n", "<br>") if item.full_doc else ""
        safety_html = escape(item.safety_doc).replace("\n", "<br>") if item.safety_doc else ""
        api_html = (
            f"<a href=\"{escape(item.api_link)}\" target=\"_blank\" rel=\"noopener noreferrer\">{escape(item.api_name)}</a>"
            if item.api_link
            else escape(item.api_name)
        )
        rows.append(
            f"<tr data-type=\"{escape(item.item_type)}\" data-module=\"{escape(item.module_path.lower())}\" data-api=\"{escape(item.api_name.lower())}\">"
            f"<td>{item.index}</td>"
            f"<td>{escape(item.module_path)}</td>"
            f"<td>{api_html}</td>"
            f"<td>{escape(item.item_type)}</td>"
            f"<td>{full_doc_html}</td>"
            f"<td>{safety_html}</td>"
            f"<td><button class=\"confirm-btn\" data-key=\"{escape(item.source_path + ':' + item.api_name + ':' + item.item_type)}\">Confirmed</button></td>"
            "</tr>"
        )

    table_rows = "\n".join(rows)
    source_url_escaped = escape(source_url)
    report_data_json = json.dumps([asdict(item) for item in items], ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Rust Public Unsafe API Report</title>
  <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; margin: 24px; line-height: 1.45; background: #f6f8fa; color: #24292f; }}
        h1 {{ margin: 0 0 6px; font-size: 28px; }}
        .meta {{ margin-bottom: 14px; color: #57606a; }}
        .panel {{ background: #fff; border: 1px solid #d0d7de; border-radius: 10px; padding: 14px; box-shadow: 0 1px 2px rgba(31,35,40,0.04); }}
        .controls {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }}
        .toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; }}
        .export-btn {{ border: 1px solid #0969da; color: #0969da; background: #fff; padding: 7px 12px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
        .export-btn:hover {{ background: #f0f7ff; }}
        .control-box label {{ display: block; font-weight: 600; margin-bottom: 6px; font-size: 13px; }}
        .control-box input {{ width: 100%; box-sizing: border-box; border: 1px solid #d0d7de; border-radius: 8px; padding: 8px 10px; font-size: 14px; }}
        .types {{ margin-bottom: 12px; }}
        .type-list {{ display: flex; flex-wrap: wrap; gap: 8px 12px; }}
        .type-item {{ font-size: 13px; color: #24292f; background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 999px; padding: 5px 10px; }}
        .summary {{ margin: 8px 0 12px; color: #57606a; font-size: 13px; }}
        .table-wrap {{ overflow: auto; border: 1px solid #d0d7de; border-radius: 10px; }}
        table {{ border-collapse: collapse; width: 100%; table-layout: fixed; background: #fff; }}
        th, td {{ border-bottom: 1px solid #d8dee4; padding: 9px 10px; vertical-align: top; word-break: break-word; }}
        th {{ background: #f6f8fa; text-align: left; position: sticky; top: 0; z-index: 1; }}
        tbody tr:hover {{ background: #f6f8fa; }}
        a {{ color: #0969da; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .confirm-btn {{ border: 1px solid #8c959f; color: #57606a; background: #f6f8fa; padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 12px; }}
        .confirm-btn.on {{ border-color: #1f883d; background: #1f883d; color: #fff; }}
        .muted {{ color: #6e7781; }}
  </style>
</head>
<body>
  <h1>Rust Public Unsafe API Report</h1>
    <div class="meta">Source URL: <a href="{source_url_escaped}" target="_blank" rel="noopener noreferrer">{source_url_escaped}</a></div>

    <div class="panel">
        <div class="toolbar">
            <div class="muted">Export data as JSON</div>
            <button id="exportJsonBtn" class="export-btn" type="button">Export JSON</button>
        </div>

        <div class="controls">
            <div class="control-box">
                <label for="moduleSearch">Search Module Path</label>
                <input id="moduleSearch" type="text" placeholder="e.g. crate::sync" />
            </div>
            <div class="control-box">
                <label for="apiSearch">Search API Name</label>
                <input id="apiSearch" type="text" placeholder="e.g. from_raw" />
            </div>
        </div>

        <div class="types">
            <div class="muted" style="margin-bottom:6px;font-weight:600;">Type Filter (multi-select)</div>
            <div id="typeFilters" class="type-list"></div>
        </div>

        <div id="summary" class="summary"></div>

        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th style="width: 60px;">#</th>
                        <th style="width: 220px;">Module Path</th>
                        <th style="width: 180px;">API Name</th>
                        <th style="width: 120px;">Type</th>
                        <th>Full Doc</th>
                        <th>Safety Doc</th>
                        <th style="width: 120px;">Confirmed</th>
                    </tr>
                </thead>
                <tbody id="reportRows">
                    {table_rows}
                </tbody>
            </table>
        </div>
    </div>

  <script>
    const STORAGE_KEY = 'rust_unsafe_confirmed_state_v1';
    const state = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}');
                const REPORT_DATA = {report_data_json};
        const rows = Array.from(document.querySelectorAll('#reportRows tr'));

        const typeCounts = rows.reduce((acc, row) => {{
            const t = row.dataset.type || 'unknown';
            acc[t] = (acc[t] || 0) + 1;
            return acc;
        }}, {{}});

        const typeFilters = document.getElementById('typeFilters');
        const selectedTypes = new Set(Object.keys(typeCounts));

        Object.keys(typeCounts).sort().forEach((type) => {{
            const label = document.createElement('label');
            label.className = 'type-item';
            label.innerHTML = `<input type="checkbox" checked data-type="${{type}}" style="margin-right:6px;">${{type}} (${{typeCounts[type]}})`;
            typeFilters.appendChild(label);
        }});

    function render(btn) {{
      const key = btn.dataset.key;
      const on = !!state[key];
      btn.classList.toggle('on', on);
            btn.textContent = on ? 'Confirmed ✓' : 'Not Confirmed';
    }}

        function applyFilters() {{
            const moduleQuery = (document.getElementById('moduleSearch').value || '').trim().toLowerCase();
            const apiQuery = (document.getElementById('apiSearch').value || '').trim().toLowerCase();

            let visible = 0;
            for (const row of rows) {{
                const type = row.dataset.type || '';
                const moduleText = row.dataset.module || '';
                const apiText = row.dataset.api || '';

                const typeOk = selectedTypes.has(type);
                const moduleOk = !moduleQuery || moduleText.includes(moduleQuery);
                const apiOk = !apiQuery || apiText.includes(apiQuery);

                const show = typeOk && moduleOk && apiOk;
                row.style.display = show ? '' : 'none';
                if (show) visible += 1;
            }}

            document.getElementById('summary').textContent = `Showing ${{visible}} / ${{rows.length}} items`;
        }}

        typeFilters.addEventListener('change', (event) => {{
            const target = event.target;
            if (!(target instanceof HTMLInputElement) || target.type !== 'checkbox') return;
            const t = target.dataset.type;
            if (!t) return;
            if (target.checked) selectedTypes.add(t);
            else selectedTypes.delete(t);
            applyFilters();
        }});

        document.getElementById('moduleSearch').addEventListener('input', applyFilters);
        document.getElementById('apiSearch').addEventListener('input', applyFilters);

        document.getElementById('exportJsonBtn').addEventListener('click', () => {{
            const visibleData = [];
            rows.forEach((row, idx) => {{
                if (row.style.display !== 'none') visibleData.push(REPORT_DATA[idx]);
            }});
            const payload = JSON.stringify(visibleData, null, 2);
            const blob = new Blob([payload], {{ type: 'application/json;charset=utf-8' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const date = new Date().toISOString().replace(/[:.]/g, '-');
            a.href = url;
            a.download = `unsafe_items_export_${{date}}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }});

    document.querySelectorAll('.confirm-btn').forEach(btn => {{
      render(btn);
      btn.addEventListener('click', () => {{
        const key = btn.dataset.key;
        state[key] = !state[key];
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        render(btn);
      }});
    }});

        applyFilters();
  </script>
</body>
</html>
"""


def generate_report(
    output_dir: Path,
    local_dir: Path,
    remote_repo_url: str = "",
    remote_ref: str = "main",
    remote_path_prefix: str = "",
) -> None:
    rust_files = walk_local_rust_files(local_dir)
    if remote_repo_url:
        cleaned_repo = normalize_remote_repo_url(remote_repo_url)
        ref = remote_ref.strip() if remote_ref else "main"
        source_label = f"{cleaned_repo}/tree/{ref}"
    else:
        source_label = str(local_dir.resolve())

    if not rust_files:
        print("No Rust files (.rs) found from the given URL scope.")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.html").write_text(
            "<html><body><h1>No Rust files found</h1></body></html>",
            encoding="utf-8",
        )
        (output_dir / "unsafe_items.json").write_text("[]\n", encoding="utf-8")
        return

    all_items: List[UnsafeItem] = []

    for file_path, _ in rust_files:
        absolute_path = local_dir.joinpath(file_path)
        remote_source_path = resolve_remote_source_path(
            local_dir=local_dir,
            absolute_path=absolute_path,
            local_relative_path=file_path,
            remote_path_prefix=remote_path_prefix,
        )
        try:
            code = absolute_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[WARN] failed to read: {absolute_path} ({exc})", file=sys.stderr)
            continue

        items = parse_unsafe_items(
            code,
            file_path=file_path,
            remote_repo_url=remote_repo_url,
            remote_ref=remote_ref,
            remote_source_path=remote_source_path,
        )
        all_items.extend(items)

    all_items.sort(key=lambda x: (x.module_path, x.item_type, x.api_name, x.source_path))
    for i, item in enumerate(all_items, start=1):
        item.index = i

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "unsafe_items.json"
    html_path = output_dir / "index.html"

    json_path.write_text(
        json.dumps([asdict(item) for item in all_items], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(build_html(all_items, source_url=source_label), encoding="utf-8")

    print(f"Done. Rust files scanned: {len(rust_files)}")
    print(f"Unsafe public items found: {len(all_items)}")
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan local directory and generate a report of public unsafe Rust APIs."
    )
    parser.add_argument("--local-dir", required=True, help="Local directory path to scan offline for .rs files")
    parser.add_argument(
        "--remote-repo-url",
        default="",
        help="Remote repository URL used to build API hyperlinks in HTML (e.g. https://github.com/org/repo)",
    )
    parser.add_argument(
        "--remote-ref",
        default="main",
        help="Remote branch/tag/commit used in API hyperlinks (default: main)",
    )
    parser.add_argument(
        "--remote-path-prefix",
        default="",
        help="Optional path prefix to prepend in remote hyperlinks (e.g. rust/kernel). By default it is auto-derived from git repo root.",
    )
    parser.add_argument(
        "--output-dir",
        default="site",
        help="Output directory for generated report files (default: site)",
    )
    args = parser.parse_args()
    local_dir_path = Path(args.local_dir).expanduser()
    if not local_dir_path.exists() or not local_dir_path.is_dir():
        parser.error("--local-dir must be an existing directory")

    generate_report(
        output_dir=Path(args.output_dir),
        local_dir=local_dir_path,
        remote_repo_url=args.remote_repo_url,
        remote_ref=args.remote_ref,
        remote_path_prefix=args.remote_path_prefix,
    )


if __name__ == "__main__":
    main()
