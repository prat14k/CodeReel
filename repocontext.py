"""Turn a repository into a compact digest that a plain chat model can read.

Claude Code CLI reads a repo with its own file tools. An OpenAI-compatible model
— a local MLX model, Ollama, LM Studio — has no tools, so we have to do the
reading ourselves and hand it the result as text. That is all this module does:

    ctx = scan(repo)
    text = digest(ctx, budget_chars=60_000)   # feed to the model
    ctx.images                                # real assets, for the video

Pure stdlib. Never writes to the repo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ------------------------------------------------------------------ policy

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor", "Pods",
    "build", ".build", "dist", "out", "target", "bin", "obj", "DerivedData",
    "__pycache__", ".venv", "venv", "env", ".env", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".cache", ".gradle",
    ".idea", ".vscode", "coverage", "htmlcov", ".terraform", "site-packages",
    ".dart_tool", ".bundle", "Pods", ".cargo", ".swiftpm", "zig-cache",
}

IGNORE_FILES = {
    ".DS_Store", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "Cargo.lock", "Gemfile.lock", "go.sum",
    "composer.lock", "uv.lock", ".gitignore", ".gitattributes", "LICENSE",
    "LICENSE.txt", "LICENSE.md", "COPYING", "THIRD_PARTY_NOTICES.md",
}

# Extensions we will read as text. Anything else is listed by name only.
TEXT_EXT = {
    ".md", ".markdown", ".rst", ".txt", ".adoc", ".org",
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts",
    ".swift", ".go", ".rs", ".rb", ".php", ".java", ".kt", ".kts", ".scala",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".plist", ".gradle", ".properties", ".env.example",
    ".sql", ".graphql", ".proto", ".tf", ".dockerfile", ".makefile", ".cmake",
    ".r", ".jl", ".lua", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj",
    ".txt", ".tex", ".bib", ".ipynb", ".R", ".mdx",
}

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".icns", ".bmp", ".avif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}

LANG = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".swift": "Swift", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".m": "Objective-C", ".mm": "Objective-C++", ".sh": "Shell",
    ".bash": "Shell", ".zsh": "Shell", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte", ".sql": "SQL",
    ".r": "R", ".jl": "Julia", ".lua": "Lua", ".dart": "Dart", ".ex": "Elixir",
    ".hs": "Haskell", ".clj": "Clojure", ".plist": "Plist", ".xml": "XML",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".md": "Markdown",
}

# Manifests carry the project's own description of itself — read these first.
MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "cargo.toml", "go.mod", "gemfile", "composer.json", "build.gradle",
    "build.gradle.kts", "pom.xml", "package.swift", "podspec", "*.csproj",
    "mix.exs", "pubspec.yaml", "cmakelists.txt", "makefile", "dockerfile",
    "docker-compose.yml", "compose.yaml", "flake.nix", "justfile", "taskfile.yml",
}

# Entry points: where the program actually starts.
ENTRY_NAMES = {
    "main.py", "app.py", "server.py", "__main__.py", "cli.py", "manage.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
    "app.js", "app.ts", "main.go", "main.rs", "lib.rs", "main.swift",
    "appdelegate.swift", "program.cs", "main.java", "index.php", "main.rb",
    "application.rb", "main.c", "main.cpp",
}

DOC_NAMES = {
    "readme.md", "readme.rst", "readme.txt", "readme", "architecture.md",
    "docs.md", "contributing.md", "changelog.md", "api.md", "design.md",
    "how-it-works.md", "usage.md", "guide.md", "manual.md",
}

SCREENSHOT_HINT = re.compile(
    r"(screenshot|screen[-_ ]?shot|screen\d|shot\d|demo|preview|hero|banner|"
    r"cover|thumbnail|example|sample|walkthrough|showcase|mock|ui[-_ ]?\d|"
    r"result|output|dashboard|app[-_ ]?shot)",
    re.I,
)
LOGO_HINT = re.compile(r"(logo|icon|appicon|app[-_ ]?icon|brand|mark|favicon|wordmark)", re.I)
# Anything that belongs to a product this repo merely integrates with.
VENDOR_HINT = re.compile(
    r"(claude|anthropic|openai|gemini|gpt|llama|meta|google|microsoft|apple|"
    r"amazon|aws|azure|nvidia|github|gitlab|docker|kubernetes|vercel|netlify|"
    r"react|vue|angular|svelte|nextjs|tailwind|nodejs|python|swift|kotlin|"
    r"rust|golang|django|flask|fastapi|pytorch|tensorflow|huggingface|"
    r"hey?gen|hyperframes|voxcpm|openbmb)",
    re.I,
)


@dataclass
class FileInfo:
    path: str          # repo-relative, posix separators
    size: int
    lines: int
    lang: str
    kind: str          # readme | manifest | entry | doc | source | other
    score: float = 0.0
    text: str = ""     # filled only for the files we actually read


@dataclass
class ImageInfo:
    path: str
    size: int
    width: int = 0
    height: int = 0
    vector: bool = False

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass
class RepoContext:
    root: str
    name: str
    files: list[FileInfo] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    tree: str = ""
    languages: dict[str, int] = field(default_factory=dict)   # lang -> lines
    total_lines: int = 0
    truncated: bool = False

    # ---- derived

    @property
    def logo(self) -> str:
        """The app's own logo, or "" — never a vendor mark, never build output."""
        return pick_logo(self)

    def screenshots(self) -> list[ImageInfo]:
        return rank_screenshots(self)

    def stats(self) -> list[dict]:
        """Big numbers worth putting on screen. Only what the repo proves."""
        out = []
        if self.total_lines:
            out.append({"value": human_int(self.total_lines), "label": "lines of code"})
        top = [l for l, _ in sorted(self.languages.items(), key=lambda kv: -kv[1])[:2]]
        if top:
            out.append({"value": top[0], "label": "primary language"})
        if self.files:
            out.append({"value": human_int(len(self.files)), "label": "source files"})
        return out

    def to_json(self) -> dict:
        return {
            "name": self.name, "root": self.root,
            "files": len(self.files), "lines": self.total_lines,
            "languages": self.languages,
            "images": [{"path": i.path, "w": i.width, "h": i.height} for i in self.images],
            "videos": self.videos,
        }


# ------------------------------------------------------------------ helpers

def human_int(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        v = n / 1000
        return f"{v:.0f}k" if v >= 10 else f"{v:.1f}k".replace(".0k", "k")
    return str(n)


def _norm(rel: str) -> str:
    return rel.replace(os.sep, "/")


def _looks_binary(blob: bytes) -> bool:
    if b"\x00" in blob[:8192]:
        return True
    try:
        blob[:8192].decode("utf-8")
        return False
    except UnicodeDecodeError:
        # tolerate a few bad bytes, reject genuinely binary content
        bad = sum(1 for b in blob[:8192] if b > 0x7F)
        return bad > len(blob[:8192]) * 0.30


def _classify(name_lower: str) -> str:
    if name_lower.startswith("readme") or name_lower in DOC_NAMES:
        return "readme" if name_lower.startswith("readme") else "doc"
    if name_lower in MANIFEST_NAMES or name_lower.endswith((".csproj", ".podspec")):
        return "manifest"
    if name_lower in ENTRY_NAMES:
        return "entry"
    return "source"


def _score(fi: FileInfo, depth: int) -> float:
    """Higher is more worth spending digest budget on."""
    kind_weight = {"readme": 100, "manifest": 80, "entry": 70, "doc": 45, "source": 20}
    s = kind_weight.get(fi.kind, 10)
    s -= depth * 3                      # shallower is more central
    s -= min(fi.size / 20_000, 12)      # huge files crowd the budget
    if fi.kind == "source":
        s += min(fi.lines / 40, 18)     # a substantial source file beats a stub
        if fi.lang in ("Python", "TypeScript", "JavaScript", "Swift", "Go", "Rust"):
            s += 6
    return s


def _image_size(path: Path) -> tuple[int, int, bool]:
    """Pixel size via sips (always present on macOS). SVG is read directly."""
    if path.suffix.lower() == ".svg":
        try:
            head = path.read_text(errors="ignore")[:1200]
            w = re.search(r'\bwidth\s*=\s*["\'](\d+(?:\.\d+)?)', head)
            h = re.search(r'\bheight\s*=\s*["\'](\d+(?:\.\d+)?)', head)
            if w and h:
                return int(float(w.group(1))), int(float(h.group(1))), True
            vb = re.search(r'viewBox\s*=\s*["\']\s*[\d.-]+\s+[\d.-]+\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)', head)
            if vb:
                return int(float(vb.group(1))), int(float(vb.group(2))), True
        except OSError:
            pass
        return 0, 0, True
    try:
        r = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True, text=True, timeout=6,
        )
        w = re.search(r"pixelWidth:\s*(\d+)", r.stdout)
        h = re.search(r"pixelHeight:\s*(\d+)", r.stdout)
        if w and h:
            return int(w.group(1)), int(h.group(1)), False
    except (OSError, subprocess.SubprocessError):
        pass
    return 0, 0, False


# ------------------------------------------------------------------ scan

def scan(repo: Path, max_files: int = 40_000, max_image_probe: int = 400) -> RepoContext:
    repo = Path(repo).expanduser().resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"not a folder: {repo}")
    ctx = RepoContext(root=str(repo), name=repo.name)

    seen = 0
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in IGNORE_DIRS and not d.startswith("."))
        here = Path(dirpath)
        try:
            depth = len(here.relative_to(repo).parts)
        except ValueError:
            continue
        if depth > 8:
            dirnames[:] = []
            continue

        for fn in sorted(filenames):
            if seen >= max_files:
                ctx.truncated = True
                break
            if fn in IGNORE_FILES or fn.startswith("."):
                continue
            p = here / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            seen += 1
            ext = p.suffix.lower()
            rel = _norm(str(p.relative_to(repo)))

            if ext in IMAGE_EXT:
                ctx.images.append(ImageInfo(path=rel, size=size))
                continue
            if ext in VIDEO_EXT:
                ctx.videos.append(rel)
                continue
            if ext not in TEXT_EXT or size > 900_000:
                continue

            lang = LANG.get(ext, ext.lstrip(".").upper() or "Text")
            fi = FileInfo(path=rel, size=size, lines=0, lang=lang,
                          kind=_classify(fn.lower()))
            fi.score = _score(fi, depth)
            ctx.files.append(fi)

    # Lines + language mix need the file contents; read the small ones only.
    for fi in ctx.files:
        if fi.size > 400_000:
            continue
        try:
            blob = (repo / fi.path).read_bytes()
        except OSError:
            continue
        if _looks_binary(blob):
            continue
        fi.lines = blob.count(b"\n") + 1
        if fi.kind in ("source", "entry"):
            ctx.languages[fi.lang] = ctx.languages.get(fi.lang, 0) + fi.lines
            ctx.total_lines += fi.lines

    for img in ctx.images[:max_image_probe]:
        w, h, vec = _image_size(repo / img.path)
        img.width, img.height, img.vector = w, h, vec

    ctx.files.sort(key=lambda f: -f.score)
    ctx.tree = _build_tree(repo, ctx)
    return ctx


def _build_tree(repo: Path, ctx: RepoContext, max_entries: int = 260, depth: int = 5) -> str:
    """A readable tree of the shallow, interesting part of the repo."""
    lines: list[str] = []
    count = 0

    def walk(d: Path, prefix: str, level: int) -> None:
        nonlocal count
        if level > depth or count >= max_entries:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        entries = [e for e in entries
                   if not e.name.startswith(".")
                   and e.name not in IGNORE_DIRS
                   and e.name not in IGNORE_FILES]
        for e in entries:
            if count >= max_entries:
                lines.append(f"{prefix}…")
                return
            if e.is_dir():
                lines.append(f"{prefix}{e.name}/")
                count += 1
                walk(e, prefix + "  ", level + 1)
            else:
                lines.append(f"{prefix}{e.name}")
                count += 1

    walk(repo, "", 1)
    return "\n".join(lines)


# ------------------------------------------------------------------ digest

def _read_text(repo: Path, fi: FileInfo, max_bytes: int) -> str:
    try:
        blob = (repo / fi.path).read_bytes()[:max_bytes]
    except OSError:
        return ""
    if _looks_binary(blob):
        return ""
    text = blob.decode("utf-8", errors="replace")
    if fi.path.endswith(".ipynb"):
        text = _slim_notebook(text)
    return text


def _slim_notebook(text: str) -> str:
    try:
        nb = json.loads(text)
    except json.JSONDecodeError:
        return text
    out = []
    for c in nb.get("cells", [])[:40]:
        src = "".join(c.get("source", []))
        out.append(f"# [{c.get('cell_type')}]\n{src}")
    return "\n".join(out)


def _budget_for(fi: FileInfo) -> int:
    if fi.kind in ("readme", "doc"):
        return 14_000
    if fi.kind == "manifest":
        return 8_000
    if fi.kind == "entry":
        return 16_000
    return 7_000


def digest(ctx: RepoContext, budget_chars: int = 60_000, max_files_read: int = 24) -> str:
    """Assemble the text a tool-less model gets to see. Fits inside budget_chars."""
    repo = Path(ctx.root)
    parts: list[str] = []
    used = 0

    head = [
        f"# Repository: {ctx.name}",
        f"Root: {ctx.root}",
        "",
        "## Overview",
        f"- {len(ctx.files)} text files, {human_int(ctx.total_lines)} lines of code",
    ]
    if ctx.languages:
        total = sum(ctx.languages.values()) or 1
        mix = ", ".join(f"{lang} {100 * n // total}%"
                        for lang, n in sorted(ctx.languages.items(), key=lambda kv: -kv[1])[:5])
        head.append(f"- Languages: {mix}")
    kinds = {f.kind for f in ctx.files}
    head.append("- Contains: " + ", ".join(sorted(kinds)))
    if ctx.videos:
        head.append(f"- {len(ctx.videos)} video file(s)")
    if ctx.images:
        head.append(f"- {len(ctx.images)} image file(s)")
    parts.append("\n".join(head))
    used += len(parts[0])

    if ctx.tree:
        block = "## File tree\n" + ctx.tree
        parts.append(block)
        used += len(block)

    # Read the highest-scoring files until the budget is gone.
    read = 0
    for fi in ctx.files:
        if read >= max_files_read or used > budget_chars * 0.92:
            break
        if fi.size > 400_000:
            continue
        room = min(_budget_for(fi), budget_chars - used - 400)
        if room < 500:
            break
        text = _read_text(repo, fi, room * 4)
        if not text.strip():
            continue
        if len(text) > room:
            text = text[:room] + f"\n… [{fi.path} truncated — {fi.lines} lines total]"
        block = (f"## {fi.path}  ({human_int(fi.size)}B, {fi.lines} lines, {fi.lang})\n"
                 f"```\n{text}\n```")
        parts.append(block)
        used += len(block)
        read += 1

    if ctx.images:
        lines = ["## Images in the repo (repo-relative paths)"]
        for img in sorted(ctx.images, key=lambda i: -i.pixels)[:40]:
            dim = f"{img.width}×{img.height}" if img.width else "vector" if img.vector else "?"
            lines.append(f"- {img.path}  {dim}  {human_int(img.size)}B")
        parts.append("\n".join(lines))

    listed = [f.path for f in ctx.files[read:read + 60]]
    if listed:
        parts.append("## Other files (names only)\n" + "\n".join(f"- {p}" for p in listed))

    text = "\n\n".join(parts)
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n\n… [digest truncated]"
    return text


# ------------------------------------------------------------------ assets

def _asset_penalty(rel: str) -> float:
    """Reject vendor marks and build output — they are not this app's branding."""
    p = Path(rel)
    if IGNORE_DIRS.intersection(p.parts):
        return 1e9
    name = p.name.lower()
    pen = 0.0
    if VENDOR_HINT.search(name):
        pen += 5000
    if VENDOR_HINT.search("/".join(p.parts[:-1])):
        pen += 300
    return pen


def pick_logo(ctx: RepoContext) -> str:
    """Best guess at the app's own logo. "" when nothing convincing exists."""
    repo = Path(ctx.root)
    best, best_score = "", -1e9
    for img in ctx.images:
        p = Path(img.path)
        name = p.name.lower()
        if img.size < 200:
            continue
        if img.size > 3_000_000 and not img.vector:
            continue
        if img.width and img.height:
            ratio = img.width / max(img.height, 1)
            if ratio > 2.6 or ratio < 0.38:
                continue            # a banner or a strip, not a mark
            if img.pixels < 64 * 64:
                continue            # a favicon at 16px is not a logo
        if p.suffix.lower() == ".icns":
            score = 60.0
        elif LOGO_HINT.search(name):
            score = 90.0
        elif p.suffix.lower() == ".svg" and len(p.parts) <= 3:
            score = 45.0
        elif name in ("apple-touch-icon.png", "icon.png", "appicon.png"):
            score = 70.0
        else:
            continue
        if img.vector:
            score += 12
        score += max(0, 10 - len(p.parts) * 3)
        score -= _asset_penalty(img.path)
        if score > best_score:
            best, best_score = img.path, score
    return best if best_score > 0 else ""


def rank_screenshots(ctx: RepoContext) -> list[ImageInfo]:
    """Real screenshots of this app, best first. Build output excluded."""
    out: list[tuple[float, ImageInfo]] = []
    for img in ctx.images:
        p = Path(img.path)
        if img.size < 800:
            continue
        if p.suffix.lower() in (".icns", ".bmp", ".tiff"):
            continue
        score = 0.0
        if SCREENSHOT_HINT.search(p.name):
            score += 70
        if SCREENSHOT_HINT.search("/".join(p.parts[:-1])):
            score += 30
        dirs = {d.lower() for d in p.parts[:-1]}
        if dirs & {"docs", "doc", "screenshots", "screens", "images", "img",
                   "assets", "media", "gallery", ".github", "preview"}:
            score += 22
        if img.vector:
            score += 6
        if img.pixels:
            if img.pixels >= 1280 * 720:
                score += 26        # a real UI shot is at least 720p
            elif img.pixels >= 800 * 500:
                score += 12
            elif img.pixels < 400 * 250:
                score -= 25        # too small to fill a frame
        else:
            score += 4
        if len(p.parts) > 4:
            score -= 8
        score -= _asset_penalty(img.path)
        if score > 0:
            out.append((score, img))
    out.sort(key=lambda t: -t[0])
    return [i for _, i in out]


def screenshot_for(ctx: RepoContext, used: set[str], hint: str = "") -> str:
    """Pick an unused screenshot, preferring one whose filename matches `hint`."""
    cands = [i for i in rank_screenshots(ctx) if i.path not in used]
    if not cands:
        return ""
    if hint:
        words = {w for w in re.findall(r"[a-z]{4,}", hint.lower())}
        for img in cands:
            name = Path(img.path).stem.lower()
            if words & {w for w in re.findall(r"[a-z]{4,}", name)}:
                return img.path
    return cands[0].path


# ------------------------------------------------------------------ snippet

def code_snippet(ctx: RepoContext, rel: str, max_lines: int = 26) -> tuple[str, str]:
    """A presentable slice of a file: its most interesting block, not the header.

    Returns (code, language). "" when the file is unreadable.
    """
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        return "", ""
    repo = Path(ctx.root).resolve()
    try:
        full = (repo / rel).resolve()
        full.relative_to(repo)
    except (ValueError, OSError):
        return "", ""
    if not full.is_file() or full.suffix.lower() not in TEXT_EXT:
        return "", ""
    try:
        blob = full.read_bytes()[:200_000]
    except OSError:
        return "", ""
    if _looks_binary(blob):
        return "", ""
    text = blob.decode("utf-8", errors="replace")
    lang = LANG.get(full.suffix.lower(), "")

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines).rstrip(), lang

    # Prefer a window that starts at a real declaration and has the densest code.
    starts = [i for i, ln in enumerate(lines)
              if re.match(r"\s*(def |class |func |fn |pub fn |async def |"
                          r"function |export |const \w+ = \(|@app\.|@router\.|"
                          r"struct |enum |impl |interface |type \w+ =)", ln)]
    if not starts:
        starts = [0]
    best_i, best_score = starts[0], -1.0
    for i in starts:
        window = lines[i:i + max_lines]
        if len(window) < max_lines * 0.5:
            continue
        score = sum(1 for ln in window if ln.strip() and not ln.strip().startswith("#"))
        score += 6 if i == 0 else 0
        if score > best_score:
            best_i, best_score = i, score

    window = lines[best_i:best_i + max_lines]
    # trim leading blanks, keep the shape readable
    while window and not window[0].strip():
        window.pop(0)
    out = "\n".join(window).rstrip()
    if best_i > 0:
        out = f"# … line {best_i + 1}\n" + out
    if best_i + max_lines < len(lines):
        out += f"\n# … {len(lines) - best_i - max_lines} more lines"
    return out, lang


def guess_code_file(ctx: RepoContext, hint: str = "") -> str:
    """Pick a file worth showing on screen when the model did not name one."""
    pool = [f for f in ctx.files if f.kind in ("entry", "source") and f.lines >= 12]
    if not pool:
        return ""
    if hint:
        words = {w for w in re.findall(r"[a-z]{4,}", hint.lower())}
        for fi in pool:
            if words & {w for w in re.findall(r"[a-z]{4,}", Path(fi.path).stem.lower())}:
                return fi.path
    for fi in pool:
        if fi.kind == "entry":
            return fi.path
    return pool[0].path


def tech_stack(ctx: RepoContext) -> list[str]:
    """Names this repo actually depends on — read from its manifests, not guessed."""
    repo = Path(ctx.root)
    found: list[str] = []
    for fi in ctx.files:
        if fi.kind != "manifest":
            continue
        try:
            text = (repo / fi.path).read_text(errors="ignore")[:60_000]
        except OSError:
            continue
        name = Path(fi.path).name.lower()
        if name == "package.json":
            try:
                pkg = json.loads(text)
            except json.JSONDecodeError:
                continue
            deps = list(pkg.get("dependencies", {})) + list(pkg.get("devDependencies", {}))
            found += [_pretty_dep(d) for d in deps]
        elif name in ("requirements.txt", "pyproject.toml", "setup.py"):
            for m in re.finditer(r'^\s*["\']?([A-Za-z][A-Za-z0-9_.-]{1,30})["\']?\s*[<>=~!\[]',
                                 text, re.M):
                found.append(_pretty_dep(m.group(1)))
            for m in re.finditer(r'^\s*([A-Za-z][A-Za-z0-9_.-]{1,30})\s*$', text, re.M):
                found.append(_pretty_dep(m.group(1)))
        elif name == "cargo.toml":
            for m in re.finditer(r'^\s*([A-Za-z][A-Za-z0-9_-]{1,30})\s*=', text, re.M):
                found.append(_pretty_dep(m.group(1)))
        elif name == "go.mod":
            for m in re.finditer(r'^\s+([\w./-]+)\s+v[\d.]+', text, re.M):
                found.append(m.group(1).split("/")[-1])
        elif name in ("package.swift",):
            for m in re.finditer(r'\.package\([^)]*?"([^"]+)"', text):
                found.append(m.group(1).split("/")[-1].removesuffix(".git"))
        if len(found) >= 40:
            break

    # Repos that install through a README command have no manifest to read.
    if not found:
        for fi in ctx.files:
            if fi.kind != "readme":
                continue
            try:
                text = (repo / fi.path).read_text(errors="ignore")[:40_000]
            except OSError:
                continue
            for m in re.finditer(
                    r"^\s*(?:uv\s+)?(?:pip3?|npm|yarn|pnpm|bun|brew|cargo|go)\s+"
                    r"(?:install|add|get)\s+([^\n#]+)", text, re.M):
                for token in m.group(1).split():
                    if token.startswith("-"):
                        continue
                    found.append(_pretty_dep(token))
            break

    noise = {"the", "and", "for", "with", "this", "that", "from", "name", "version",
             "description", "authors", "license", "readme", "edition", "requires",
             "python", "include", "exclude", "packages", "install", "build", "test"}
    out, seen = [], set()
    for d in found:
        key = d.lower()
        if key in seen or key in noise or len(key) < 2:
            continue
        seen.add(key)
        out.append(d)
    return out[:14]


def _pretty_dep(raw: str) -> str:
    d = raw.strip().strip("\"'").split("=")[0].split(">")[0].split("<")[0].split("[")[0]
    d = d.split("/")[-1].removesuffix(".git").removesuffix(".py")
    return d.strip()


if __name__ == "__main__":  # quick eyeball: python repocontext.py <repo>
    import sys
    r = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    c = scan(r)
    print(json.dumps(c.to_json(), indent=2))
    print("\nlogo:", c.logo or "(none)")
    print("screenshots:", [i.path for i in c.screenshots()[:6]] or "(none)")
    print("stack:", tech_stack(c))
    d = digest(c)
    print(f"\ndigest: {len(d)} chars")
    print(d[:1500])
