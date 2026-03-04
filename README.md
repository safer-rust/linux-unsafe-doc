## Linux Unsafe Doc Generator

This directory provides a complete script in offline-only mode:

1. Select a local cloned repository directory.
2. Recursively scan all Rust files (`.rs`) under that directory.
3. Analyze public unsafe items in four categories:
   - `function`: `pub unsafe fn`
   - `method`: `pub unsafe fn` inside `impl`
   - `trait`: `pub unsafe trait`
   - `trait_method`: `unsafe fn` defined inside a `pub unsafe trait` (no `pub` required; API name format is `TraitName::MethodName`)
4. Extract full documentation comments for each item as `full_doc` (description, examples, safety section, etc.).
5. Extract the `# Safety` section only as a separate field `safety_doc`.
6. Generate a frontend report page suitable for GitHub Pages (HTML table + confirmation button).
7. Provide Type multi-select filtering (with per-type counts) and fuzzy search for Module Path and API Name.
8. Support JSON export from the webpage (exports currently filtered rows).

### Files

- `unsafe_doc_generator.py`: main script
- Default outputs after running:
  - `site/index.html`
  - `site/unsafe_items.json`

### Usage

Run in this directory (offline scan):

```bash
python3 unsafe_doc_generator.py \
  --local-dir "/path/to/local/repo/subdir" \
  --remote-repo-url "https://github.com/<owner>/<repo>" \
  --remote-ref "<branch-or-tag>" \
  --remote-path-prefix "<optional/path/prefix>" \
  --output-dir site
```

Example:

```bash
python3 unsafe_doc_generator.py \
  --local-dir "/home/chenyl/projects/linux/rust/kernel" \
  --remote-repo-url "https://github.com/Rust-for-Linux/linux" \
  --remote-ref "rust-next" \
  --remote-path-prefix "rust/kernel" \
  --output-dir linux_kernel
```

### Arguments

- `--local-dir`: local scan directory (required).
- `--remote-repo-url` + `--remote-ref`: used to generate clickable API links to remote repository lines (`.../blob/<ref>/<path>#L<line>`).
- `--remote-path-prefix`: optional manual remote path prefix (e.g. `rust/kernel`). If omitted, the script tries to infer it from local git repository root.
- `--output-dir`: output directory.

### Report Fields

Fields shown in `index.html`:

- index
- module path
- API name (clickable when remote repo URL is provided)
- type (`function | method | trait | trait_method`)
- full doc
- safety doc (only `# Safety` section)
- `Confirmed` button (state stored in browser `localStorage`; default is `Not Confirmed`, clicked state is `Confirmed ✓`)

Fields in `unsafe_items.json`:

- `index`
- `module_path`
- `api_name`
- `item_type`
- `full_doc`
- `safety_doc`
- `source_path`
- `source_line`
- `api_link`

Note: `source_url` is not included.

### Web Interactions

- Top `Source URL` displays remote repository URL when `--remote-repo-url` is provided.
- Multi-select Type filter with per-type counts.
- Fuzzy search for Module Path and API Name.
- One-click JSON export for currently filtered rows.

### GitHub Display Suggestion

To publish the generated page on GitHub, enable GitHub Pages and publish the contents of your output directory (for example, `/docs` on your default branch).
