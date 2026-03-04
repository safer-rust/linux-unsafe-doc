## Linux Unsafe Doc Generator

这个目录提供一个完整脚本，用于（仅离线模式）：

1. 指定本地已 clone 仓库目录。
2. 递归扫描该目录中的 Rust 文件（`.rs`）以及子目录。
3. 分析所有 Rust 文件中的公开 `unsafe` 项，包含以下四类：
   - `function`: `pub unsafe fn`（可外部调用）
   - `method`: `impl` 中的 `pub unsafe fn`（可外部调用）
   - `trait`: `pub unsafe trait`（可外部实现）
   - `trait_method`: `pub unsafe trait` 内定义的 `unsafe fn`（API 名称格式为 `TraitName::MethodName`）
4. 提取每个项对应的完整注释块（`full_doc`，包含描述、Examples、Safety 等全部文档注释内容）。
5. 额外提取仅 `# Safety` 段（`safety_doc`）作为独立字段。
6. 生成可在 GitHub Pages 展示的前端页面（HTML 表格 + 确认按钮）。
7. 页面支持 `Type` 多选筛选（显示每种类型数量）、`Module Path` 与 `API Name` 模糊搜索。
8. 页面支持导出 JSON（下载当前筛选结果）。

### 文件说明

- `unsafe_doc_generator.py`: 主脚本
- 运行后默认生成：
  - `site/index.html`
  - `site/unsafe_items.json`

### 使用方法

在当前目录执行（离线扫描）：

```bash
python3 unsafe_doc_generator.py \
	--local-dir "/path/to/local/repo/subdir" \
	--remote-repo-url "https://github.com/<owner>/<repo>" \
	--remote-ref "<branch-or-tag>" \
	--remote-path-prefix "<optional/path/prefix>" \
	--output-dir site
```

示例（你的场景）：

```bash
python3 unsafe_doc_generator.py \
	--local-dir "/home/chenyl/projects/linux/rust/kernel" \
	--remote-repo-url "https://github.com/Rust-for-Linux/linux" \
	--remote-ref "rust-next" \
	--remote-path-prefix "rust/kernel" \
	--output-dir linux_kernel
```

参数说明：

- `--local-dir`：本地扫描目录（必填）。
- `--remote-repo-url` + `--remote-ref`：用于在页面中把 API 名称渲染成可点击超链接，跳转到远程仓库对应文件与行号（`.../blob/<ref>/<path>#L<line>`）。
- `--remote-path-prefix`：可选，手动指定远程路径前缀（例如 `rust/kernel`）。默认会自动从本地 Git 仓库根目录推导路径。
- `--output-dir`：生成报告目录。

### 生成页面字段

`index.html` 中表格字段：

- 序号
- module 路径
- API 名称（`trait` 类型显示 trait 名；若提供 `--remote-repo-url`，该列为可点击超链接）
- 类型（`function | method | trait | trait_method`）
- full doc
- safety doc（仅 `# Safety` 段）
- `Confirmed` 按钮（状态保存在浏览器 `localStorage`；默认显示 `Not Confirmed`，点击后变为 `Confirmed ✓`）

`unsafe_items.json` 中每条记录字段：

- `index`
- `module_path`
- `api_name`
- `item_type`
- `full_doc`
- `safety_doc`
- `source_path`
- `source_line`
- `api_link`

页面交互功能：

- 顶部 `Source URL` 显示远程仓库链接（若提供 `--remote-repo-url`）。
- `Type` 支持多选筛选，并显示每个类型计数。
- `Module Path`、`API Name` 支持部分关键词模糊搜索。
- 支持一键导出当前筛选后的数据为 JSON 文件。

### GitHub 展示建议

如果你要在 GitHub 上展示页面，建议开启 GitHub Pages 并将 `site/` 目录内容发布（例如发布 `main` 分支的 `/site`）。
