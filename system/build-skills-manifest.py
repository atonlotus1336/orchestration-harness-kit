#!/usr/bin/env python3
"""
Build master skills + agents manifest from all SKILL.md / agent .md files.

Outputs:
  D:/MyVault/.claude/SKILLS.json       — machine-readable manifest
  D:/MyVault/.claude/MASTER-INDEX.md   — human-readable routing index for LLMs

Run:
  python3 /mnt/d/MyVault/.claude/scripts/build-skills-manifest.py
  python3 D:/MyVault/.claude/scripts/build-skills-manifest.py  (Git Bash)
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — support both WSL (/mnt/g/) and Git Bash (G:/) invocation
# ---------------------------------------------------------------------------

def resolve_vault() -> Path:
    candidates = [
        Path("/mnt/d/MyVault/.claude"),
        Path("D:/MyVault/.claude"),
        Path("G:\\MyVault\\.claude"),
    ]
    for p in candidates:
        if p.exists():
            return p
    # fallback: relative to script location
    return Path(__file__).parent.parent

VAULT = resolve_vault()
SKILLS_DIR = VAULT / "skills"
AGENTS_DIR = VAULT / "agents"
OUT_JSON   = VAULT / "SKILLS.json"
OUT_INDEX  = VAULT / "MASTER-INDEX.md"
OUT_AGENTS = VAULT / "AGENT-INDEX.md"

# ---------------------------------------------------------------------------
# Category taxonomy
# ---------------------------------------------------------------------------

CATEGORY_NAMES = {
    "gsd-workflow":        "GSD Workflow",
    "codex-workflow":      "Codex Workflow",
    "image-gen":           "Image & ComfyUI",
    "business":            "Business & Strategy",
    "media-video":         "Media & Video",
    "knowledge-mgmt":      "Knowledge Management",
    "animation-frontend":  "Animation & Frontend",
    "research":            "Research & Academic",
    "debug":               "Debug & Testing",
    "ui-ux":               "UI/UX Design",
    "character-narrative": "Character & Narrative",
    "creative-ideation":   "Creative Ideation",
    "general":             "General Purpose",
}

CATEGORY_USE_FOR = {
    "gsd-workflow":        "GSD project phases, planning, execution, milestones",
    "codex-workflow":      "Codex-namespaced GSD workflow variants",
    "image-gen":           "ComfyUI image/video generation pipelines",
    "business":            "Business strategy, sales, finance, HR, legal",
    "media-video":         "YouTube, video editing, captions, transcripts",
    "knowledge-mgmt":      "Obsidian, Readwise, notes, knowledge bases",
    "animation-frontend":  "Anime.js, Three.js, GSAP, Remotion, CSS animations",
    "research":            "Deep research, academic papers, literature review",
    "debug":               "Systematic debugging, forensics, testing",
    "ui-ux":               "UI design, Tailwind, UX review, design systems",
    "character-narrative": "Character voices, personas, visual novel / Ren'Py",
    "creative-ideation":   "Brainstorming, councils, adversarial ideation",
    "general":             "Cross-cutting, catch-all utilities",
}

# ---------------------------------------------------------------------------
# Business & Strategy sub-buckets — the council's "C-Suite Spine & Satellites"
# (winning proposal E). Each business skill is filed under one sub-bucket so the
# ~70 repatriated C-suite skills don't land as one undifferentiated wall.
# ---------------------------------------------------------------------------

BIZ_SUBCAT_NAMES = {
    "advisors":      "C-Suite Advisors",
    "reviews":       "Plan Interrogations & Reviews",
    "orchestration": "C-Suite Orchestration & Protocols",
    "gtm":           "GTM, Revenue & Customer Operators",
    "org":           "Org, People, Legal & Vendor Operators",
    "skillsets":     "Business Skill-Set Bundles",
}

# Per-sub-bucket "next hop" pointer rendered under each heading so an LLM that
# landed on one sub-bucket knows where the related work lives.
BIZ_SUBCAT_LEADS = {
    "advisors":      "↳ leads to: **Plan Interrogations & Reviews** (pressure-test the advice) · **C-Suite Orchestration** (route a whole decision)",
    "reviews":       "↳ leads to: **C-Suite Advisors** (get the counsel a review demands) · **GTM / Org Operators** (execute the fix)",
    "orchestration": "↳ leads to: **C-Suite Advisors** + **Reviews** (the seats it convenes) · Creative Ideation `/brainstorm-council`",
    "gtm":           "↳ leads to: **C-Suite Advisors** (CFO / CMO / CRO strategy behind the number) · **Reviews** (stress-test the deal)",
    "org":           "↳ leads to: **C-Suite Advisors** (CHRO / GC / COO strategy) · **Reviews** (stress-test the policy)",
    "skillsets":     "↳ leads to: the individual **Advisors / Reviews / Operators** below (bundles fan out into single skills)",
}

# Sub-bucket routing vocab (shared by business_subcat). Kept as data so adding a
# new role/operator is a one-line edit, not a code change.
REVIEW_VERBS = frozenset({
    "challenge", "grill-me", "stress-test", "cross-eval", "hard-call",
    "postmortem", "post-mortem", "scenario-war-room", "deal-desk",
})
ORCH_SET = frozenset({
    "boardroom", "board-meeting", "board-deck-builder", "company-os",
    "context-engine", "agent-protocol", "founder-mode", "office-hours",
    "onboard", "cs-onboard", "decide", "decision-logger", "freeze",
    "execute", "brief", "strategic-alignment", "chief-of-staff", "founder-coach",
})
GTM_NOUNS = ("revenue", "commercial", "pricing", "deal", "sales", "rfp", "channel",
    "competitive", "customer", "partnership", "capacity", "forecast")
ORG_NOUNS = ("culture", "change-management", "org-health", "process-map",
    "internal-comms", "contract", "proposal", "procurement", "vendor",
    "ma-playbook", "intl-expansion", "weekly-synth", "daily-review", "recruit",
    "onboarding")

# Edge B — counsel→execution handoffs: an advisor/role links to the operator
# skills it should spawn to act on its advice. Targets are gated against the live
# catalog at scan time, so a name that doesn't exist on disk is silently dropped
# (no phantom handles — the auditor-confabulation lesson).
COUNSEL_TO_EXEC = {
    "cfo":  ["commercial-forecaster", "pricing-strategist", "revenue-operations"],
    "cro":  ["revenue-operations", "deal-desk", "commercial-forecaster"],
    "cmo":  ["competitive-intel", "channel-economics"],
    "cco":  ["customer-success-manager", "partnerships-architect"],
    "ciso": ["contract-and-proposal-writer", "vendor-management", "procurement-optimizer"],
    "gc":   ["contract-and-proposal-writer", "vendor-management", "procurement-optimizer"],
    "cto":  ["improve-codebase-architecture", "unity-csharp-skill"],
    "vpe":  ["improve-codebase-architecture", "unity-csharp-skill"],
}
# Edge C — orchestration hub→seats it convenes.
ORCH_HUBS = {
    "chief-of-staff": ["ceo-advisor", "decision-logger", "boardroom"],
    "founder-mode":   ["ceo-advisor", "cfo-advisor", "decision-logger"],
    "boardroom":      ["board-meeting", "board-deck-builder", "ceo-advisor"],
    "board-meeting":  ["boardroom", "board-deck-builder"],
    "company-os":     ["chief-of-staff", "decision-logger", "strategic-alignment"],
}


def categorize(name: str, description: str) -> str:
    n = name.lower()
    d = description.lower()[:400]

    if n.startswith("codex-gsd-"):
        return "codex-workflow"
    if n.startswith("gsd-") and "ui" not in n:
        return "gsd-workflow"
    if n.startswith("comfyui-"):
        return "image-gen"
    if any(n.startswith(p) for p in ("business-growth", "business-operations", "c-level", "commercial-skills")):
        return "business"
    if "youtube" in n or n in ("video-assembly", "video-publisher", "video-transcript",
                                "captions", "subtitles", "transcript", "realistic-ugc-video"):
        return "media-video"
    if (
        n.startswith("obsidian-")
        or n.startswith("readwise-")
        or any(x in n for x in ("retrieval", "memory", "rag", "mcp"))
    ):
        return "knowledge-mgmt"
    if n in ("animejs", "three", "gsap", "lottie", "remotion", "waapi", "typegpu",
             "css-animations", "pixel-studio"):
        return "animation-frontend"
    if "research" in n or "academic" in n:
        return "research"
    if "debug" in n or "forensic" in n:
        return "debug"
    if any(x in n for x in ("ui-", "-ui", "tailwind", "ux", "design", "hyperframes")):
        return "ui-ux"
    if any(x in n for x in ("voice", "character", "persona", "canon", "narrative")):
        return "character-narrative"
    if any(x in n for x in ("brainstorm", "council", "ideation", "thinking")):
        return "creative-ideation"
    if "claudex" in n or (n.startswith("codex-") and not n.startswith("codex-gsd-")):
        return "codex-workflow"

    # === Council "C-Suite Spine" taxonomy (winning proposal E) — only reaches skills that
    # fell through every rule above (the old "General Purpose" pile). Routes the ~70 mis-filed
    # C-suite/business skills to Business & Strategy, reading -> Knowledge Mgmt, media ->
    # Media & Video, code -> Debug; the genuine remainder falls through to "general". ===
    if n.endswith("-advisor"):
        return "business"
    if n.endswith("-review") or n in ("challenge", "grill-me", "stress-test", "cross-eval",
            "hard-call", "postmortem", "post-mortem", "scenario-war-room", "deal-desk"):
        return "business"
    if n.startswith("cs:") or n.startswith("/cs:") or n in ("boardroom", "board-meeting",
            "board-deck-builder", "company-os", "context-engine", "agent-protocol", "founder-mode",
            "office-hours", "onboard", "cs-onboard", "decide", "decision-logger", "freeze",
            "execute", "brief", "strategic-alignment", "chief-of-staff", "founder-coach"):
        return "business"
    if any(x in n for x in ("revenue", "commercial", "pricing", "deal", "sales", "rfp", "channel",
            "competitive", "customer", "partnership", "capacity", "culture", "change-management",
            "org-health", "process-map", "internal-comms", "contract", "proposal", "procurement",
            "vendor", "ma-playbook", "intl-expansion", "weekly-synth", "daily-review")):
        return "business"
    if any(x in n for x in ("reader", "reading", "book-review", "highlight", "recap", "triage",
            "inbox", "quiz", "surprise", "feed", "now-reading", "knowledge-ops", "defuddle",
            "marginalia", "excalidraw", "json-canvas", "canvas", "diagram")):
        return "knowledge-mgmt"
    if n == "yt" or any(x in n for x in ("youtube", "transcript", "project-manager",
            "contribute-catalog", "nano-banana")):
        return "media-video"
    if any(x in n for x in ("codebase", "architecture", "unity", "csharp", "deepseek")):
        return "debug"

    # description fallback
    if "animation" in d or "three.js" in d or "anime.js" in d:
        return "animation-frontend"
    if "brainstorm" in d or "adversarial" in d:
        return "creative-ideation"
    return "general"


def business_subcat(name: str, description: str) -> str:
    """File a Business & Strategy skill under one of the council's sub-buckets.
    Only ever called for skills already categorized 'business'."""
    n = name.lower()
    # Plugin bundles first — the namespaced skill-sets (business-growth-*,
    # commercial-skills:*, c-level-*, business-operations-*, cs-*-orchestrator).
    if (any(n.startswith(p) for p in ("business-growth", "business-operations",
            "c-level", "commercial-skills")) or n.startswith("cs-") or ":" in n):
        return "skillsets"
    if n.endswith("-advisor"):
        return "advisors"
    if n.endswith("-review") or n in REVIEW_VERBS:
        return "reviews"
    if n.startswith("cs:") or n.startswith("/cs:") or n in ORCH_SET:
        return "orchestration"
    if any(x in n for x in ORG_NOUNS):
        return "org"
    if any(x in n for x in GTM_NOUNS):
        return "gtm"
    return "gtm"  # default operator bucket


def derive_related(name: str, existing: list, all_names: set) -> list:
    """Council Edge A/B/C connectors. Only links targets that actually exist in
    the live catalog (all_names) so the index never points at a phantom handle."""
    n = name.lower()
    out = []
    for r in existing:           # keep frontmatter-declared links, gated to real handles
        rl = r.lower()
        if rl in all_names and rl != n and rl not in out:
            out.append(r)

    def add(t: str):
        if t in all_names and t != n and t not in out:
            out.append(t)

    # Edge A — advisor <-> review role pair
    if n.endswith("-advisor"):
        add(n[:-len("-advisor")] + "-review")
    if n.endswith("-review"):
        add(n[:-len("-review")] + "-advisor")
    # Edge B — counsel -> execution operators
    role = n[:-len("-advisor")] if n.endswith("-advisor") else n
    for k, targets in COUNSEL_TO_EXEC.items():
        if role == k or n == k or n.startswith(k + "-"):
            for t in targets:
                add(t)
    # Edge C — orchestration hub -> seats it convenes
    for hub, seats in ORCH_HUBS.items():
        if n == hub:
            for s in seats:
                add(s)
    return out[:6]


# ---------------------------------------------------------------------------
# Tag extraction
# ---------------------------------------------------------------------------

TAG_VOCAB = {
    # Workflow
    "planning", "execution", "debug", "review", "analysis", "automation",
    "testing", "deployment", "research", "writing", "brainstorm", "ideation",
    "orchestration", "pipeline", "phase", "milestone", "parallel", "wave",
    "inline", "quick", "subagent", "agent", "verify",
    # Tech
    "python", "typescript", "javascript", "react", "flutter", "blender",
    "unity", "renpy", "firebase", "docker", "git", "llm", "rag", "mcp",
    "api", "webhook", "chromadb", "vector", "embedding", "openai", "claude",
    "comfyui", "stable-diffusion", "lora", "controlnet",
    # Domain
    "narrative", "character", "animation", "video", "audio", "image",
    "business", "sales", "marketing", "finance", "legal", "hr",
    "security", "performance", "data", "workflow", "creative",
    "design", "ui", "ux", "frontend", "backend", "obsidian", "readwise",
    "academic", "paper", "systematic", "authority", "evidence", "retrieval",
    "memory",
}


def extract_tags(name: str, description: str, related: list) -> list:
    combined = (name.replace("-", " ") + " " + description[:500]).lower()
    words = re.findall(r'\b[\w-]+\b', combined)
    tags = []
    seen = set()
    for w in words:
        if w in TAG_VOCAB and w not in seen:
            seen.add(w)
            tags.append(w)
        if len(tags) >= 6:
            break
    return tags


# ---------------------------------------------------------------------------
# Harness compatibility
# ---------------------------------------------------------------------------

# Tools that exist in Claude Code + Codex but NOT in Hermes
CC_CODEX_ONLY_TOOLS = frozenset({
    "TodoWrite", "AskUserQuestion", "NotebookEdit", "Task", "Agent", "Skill",
})

def detect_harness_compat(allowed_tools: list) -> list:
    """
    Returns which harnesses can fully run this skill based on allowed-tools.
    "all"              → no CC-only tools declared; works in Hermes/Codex/Claude Code
    "claude-code+codex" → uses TodoWrite/AskUserQuestion/Agent/etc.; Hermes excluded
    """
    if not allowed_tools:
        return ["all"]
    if set(allowed_tools) & CC_CODEX_ONLY_TOOLS:
        return ["claude-code", "codex"]
    return ["all"]


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    m = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    fm = {}

    # name
    nm = re.search(r'^name:\s*(.+)$', block, re.M)
    if nm:
        fm["name"] = nm.group(1).strip().strip('"\'')

    # description — handle multi-line quoted or plain
    dm = re.search(r'^description:\s*"(.*?)"(?:\s*\n|\Z)', block, re.DOTALL)
    if dm:
        fm["description"] = re.sub(r'\s+', ' ', dm.group(1)).strip()
    else:
        dm2 = re.search(r'^description:\s*(.+?)(?=\n\w|\n---|\Z)', block, re.DOTALL | re.M)
        if dm2:
            fm["description"] = re.sub(r'\s+', ' ', dm2.group(1)).strip().strip('"\'')

    # argument-hint
    am = re.search(r'^argument-hint:\s*(.+)$', block, re.M)
    if am:
        fm["argument-hint"] = am.group(1).strip().strip('"\'')

    # allowed-tools (YAML list)
    at_section = re.search(r'^allowed-tools:\s*\n((?:\s*-\s*.+\n?)+)', block, re.M)
    if at_section:
        fm["allowed-tools"] = [i.strip() for i in re.findall(r'-\s*(.+)', at_section.group(1))]
    else:
        fm["allowed-tools"] = []

    # related_skills (YAML list)
    rs = re.search(r'related_skills:\s*\n((?:\s*-\s*.+\n?)+)', block)
    if rs:
        fm["related_skills"] = [i.strip() for i in re.findall(r'-\s*(.+)', rs.group(1))]
    else:
        fm["related_skills"] = []

    # status
    sm = re.search(r'^\s*status:\s*(\w+)', block, re.M)
    if sm:
        fm["status"] = sm.group(1)

    return fm


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def scan_skills() -> list:
    records = []
    if not SKILLS_DIR.exists():
        print(f"  [WARN] skills dir not found: {SKILLS_DIR}", file=sys.stderr)
        return records

    for skill_file in sorted(SKILLS_DIR.rglob("SKILL.md")):
        try:
            text = skill_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        fm = parse_frontmatter(text)
        # INVOCATION HANDLE = what the harness actually registers, so a small LLM can
        # copy an invocable name from the index:
        #   - FLAT skill (skills/<name>/SKILL.md): registered by DIRECTORY name. Its
        #     frontmatter `name:` may be a stale alias (e.g. dir `claudex` -> name
        #     `claudex-vanilla`; dir `codex-gsd-autonomous` -> name `codex-gsd:autonomous`)
        #     which is NOT invocable. Use the dir name.
        #   - NESTED plugin skill (skills/<plugin>/skills/<name>/): keep the namespaced
        #     frontmatter name (plugin:skill), which IS the invocation handle.
        if skill_file.parent.parent == SKILLS_DIR:
            name = skill_file.parent.name
        else:
            name = fm.get("name") or skill_file.parent.name
        description = fm.get("description", "")
        related = fm.get("related_skills", [])
        allowed_tools = fm.get("allowed-tools", [])

        records.append({
            "name": name,
            "kind": "skill",
            "path": skill_file.relative_to(VAULT).as_posix(),
            "category": categorize(name, description),
            "tags": extract_tags(name, description, related),
            "description": description[:300],
            "argument_hint": fm.get("argument-hint", ""),
            "allowed_tools": allowed_tools,
            "harness_compat": detect_harness_compat(allowed_tools),
            "related_skills": related,
            "status": fm.get("status", "active"),
        })

    # de-dup by invocation handle (same skill reached via two paths -> keep first)
    seen, deduped = set(), []
    for r in records:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        deduped.append(r)

    # Second pass (needs the full handle set): council Edge A/B/C connectors +
    # Business & Strategy sub-buckets.
    all_names = {r["name"].lower() for r in deduped}
    for r in deduped:
        r["related_skills"] = derive_related(r["name"], r.get("related_skills", []), all_names)
        if r["category"] == "business":
            r["subcat"] = business_subcat(r["name"], r["description"])
    return deduped


def scan_agents() -> list:
    records = []
    if not AGENTS_DIR.exists():
        print(f"  [WARN] agents dir not found: {AGENTS_DIR}", file=sys.stderr)
        return records

    for agent_file in sorted(AGENTS_DIR.glob("*.md")):
        # skip non-agent meta files
        if agent_file.stem.upper() in ("AGENT", "EXECUTIVE-BRIEF", "PULL_REQUEST_TEMPLATE", "QUICKSTART", "README"):
            continue
        try:
            text = agent_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        fm = parse_frontmatter(text)
        # skip non-agent docs — playbooks/examples (nexus-*, workflow-* walkthroughs)
        # that live in agents/ but have no YAML frontmatter, so no name/description.
        if not fm.get("name") and not fm.get("description"):
            continue
        name = fm.get("name") or agent_file.stem
        description = fm.get("description", "")

        records.append({
            "name": name,
            "kind": "agent",
            "path": agent_file.relative_to(VAULT).as_posix(),
            "category": categorize(name, description),
            "tags": extract_tags(name, description, []),
            "description": description[:300],
            "status": fm.get("status", "active"),
        })

    return records


# ---------------------------------------------------------------------------
# MASTER-INDEX.md generator
# ---------------------------------------------------------------------------

def generate_index(skills: list, agents: list) -> str:
    today = date.today().isoformat()
    lines = []

    lines += [
        "# Skills & Agents Master Index",
        f"Generated: {today} | Skills: {len(skills)} | Agents: {len(agents)}",
        "",
        "## Unified Retrieval Prompt",
        "",
        "Apply the authority constitution, then route before reading. For skills,",
        "use this index by category/tag matching, then load one selected skill. Use",
        "semantic fallback only if the index is ambiguous. For documents, use",
        "Obsidian for exact notes and `rag-orchestrator` for semantic search,",
        "reading only cited chunks or the smallest cited file section. For memories",
        "use Memoryweaver; for relationship questions use GraphRAG or Graphify.",
        "Use the methodology charter for reasoning/build process after routing.",
        "Evidence beats narration and memory. Never crawl the vault or skill tree",
        "when an index or backend can route the task.",
        "",
        "Authority constitution: `G:\\MyVault\\.claude\\AUTHORITY-CONSTITUTION.md`",
        "Methodology charter: `G:\\MyVault\\.claude\\METHODOLOGY-CHARTER.md`",
        "Reasoning-effort convention: `G:\\MyVault\\.claude\\REASONING-EFFORT.md`",
        "Full retrieval playbook: `G:\\MyVault\\.claude\\RETRIEVAL-PLAYBOOK.md`",
        "Skill-set coordination (which sets hand off to which; where a new skill belongs): `G:\\MyVault\\.claude\\SKILL-INTEGRATION.md` — this index finds a skill, that file chains skill-sets.",
        "",
        "## Routing Pipeline (for LLMs)",
        "",
        "**Step 1 - Category**: Identify domain from taxonomy below",
        "**Step 2 - Scan**: Navigate to that category section, match by tags + description",
        "**Step 3 - Method**: use the methodology charter for process guidance when the task is non-trivial",
        "**Step 4 - Semantic fallback**: query the skill index/local RAG path if available",
        "**Step 5 - Invoke**:",
        "  - Skill: `Skill(skill: \"name\")` or `/name` slash command",
        "  - Agent: `Agent(subagent_type: \"name\", prompt: \"...\")`",
        "",
        "## Quick keyword -> category (cheap first hop for small models)",
        "",
        "| If the task mentions... | Go to category |",
        "|---|---|",
        "| plan / phase / roadmap / milestone / execute / verify | GSD Workflow (or Codex Workflow — see note) |",
        "| transcript / caption / subtitle / youtube / video text | Media & Video |",
        "| brainstorm / council / grill / ideate | Creative Ideation |",
        "| CFO/CTO/board / pricing / deal / sales / HR / legal / proposal | Business & Strategy |",
        "| obsidian / readwise / notes / knowledge base | Knowledge Management |",
        "| comfyui / image-gen / video-gen / lora / voice | Image & ComfyUI |",
        "| anime.js / three.js / gsap / remotion / css / tailwind / UI / UX | Animation & Frontend or UI/UX Design |",
        "| deep research / academic / paper / literature | Research & Academic |",
        "| debug / forensics / test | Debug & Testing |",
        "| character / voice / persona / ren'py | Character & Narrative |",
        "| anything else / cross-cutting | General Purpose |",
        "",
        "> **GSD vs Codex tie-breaker:** default to `gsd-*`. Use the `codex-gsd-*` twin ONLY when explicitly offloading work to Codex or preserving Claude's context (same artifacts, Codex as the engine).",
        "",
        "> **YouTube / transcript tie-breaker:** Media & Video holds 12 overlapping TranscriptAPI skills with near-identical triggers. For any \"youtube\" or \"transcript\" phrasing (pasted links/IDs, \"transcript of\", \"what does this video say\", summarize/quote/translate a video), **default to `youtube-full`** (superset: transcripts + search + channels + playlists). Only diverge for a pure playlist (`youtube-playlist`), channel browse (`youtube-channels`), or discovery search (`youtube-search`). **Fetch keylessly first:** local `yt-dlp` and `youtube-transcript-api` are installed (run `python`) and need no API key — use them before the transcriptapi.com key path.",
        "",
        "---",
        "",
        "## Category Taxonomy",
        "",
        "| ID | Name | Use When |",
        "|----|------|----------|",
    ]
    for cat_id, cat_name in CATEGORY_NAMES.items():
        lines.append(f"| `{cat_id}` | {cat_name} | {CATEGORY_USE_FOR.get(cat_id, '')} |")

    lines += ["", "---", "", "## Skills by Category", ""]

    by_cat = defaultdict(list)
    for s in skills:
        by_cat[s["category"]].append(s)

    def render_skill(s):
        tags_str = " ".join(f"`{t}`" for t in s["tags"][:5])
        hint = f" `{s['argument_hint']}`" if s.get("argument_hint") else ""
        compat = s.get("harness_compat", ["all"])
        compat_badge = " `[CC+Cx]`" if compat != ["all"] else ""
        desc = s["description"][:110].rstrip()
        if len(s["description"]) > 110:
            desc += "…"
        lines.append(f"- **`{s['name']}`**{hint}{compat_badge} {tags_str}")
        if desc:
            lines.append(f"  {desc}")
        rel = s.get("related_skills", [])
        if rel:
            lines.append("  ↳ related: " + ", ".join(f"`{r}`" for r in rel[:5]))

    for cat_id, cat_name in CATEGORY_NAMES.items():
        cat_skills = sorted(by_cat.get(cat_id, []), key=lambda x: x["name"])
        if not cat_skills:
            continue
        lines.append(f"### {cat_name} ({len(cat_skills)})")
        lines.append("")
        if cat_id == "business":
            # Sub-bucket the C-Suite Spine so ~70 skills don't render as one wall.
            sub = defaultdict(list)
            for s in cat_skills:
                sub[s.get("subcat", "gtm")].append(s)
            for sub_id, sub_name in BIZ_SUBCAT_NAMES.items():
                sub_skills = sorted(sub.get(sub_id, []), key=lambda x: x["name"])
                if not sub_skills:
                    continue
                lines.append(f"#### {sub_name} ({len(sub_skills)})")
                lines.append(f"_{BIZ_SUBCAT_LEADS[sub_id]}_")
                lines.append("")
                for s in sub_skills:
                    render_skill(s)
                lines.append("")
        else:
            if cat_id == "media-video":
                lines.append("> **Canonical for youtube/transcript phrasing → `youtube-full`.** The other transcript skills (`captions`, `subtitles`, `transcript`, `video-transcript`, `transcriptapi`, `yt`, `youtube-api`, `youtube-data`) are variants hitting the same backend — kept available, but `youtube-full` wins ties. Pick a specialist only for a pure playlist / channel / search task. Transcript fetch works **keyless** via installed `yt-dlp` / `youtube-transcript-api`; use that before the transcriptapi.com key path.")
                lines.append("")
            for s in cat_skills:
                render_skill(s)
            lines.append("")

    lines += [
        "---", "",
        f"## Agents ({len(agents)}) — in a separate file",
        "",
        "To keep this skill index small for weak models, the agent list lives in "
        "`G:\\MyVault\\.claude\\AGENT-INDEX.md` (read on demand). Invoke with "
        "`Agent(subagent_type: \"name\")`.",
        "",
        "---",
        "",
        f"*Regenerate: `python3 /mnt/d/MyVault/.claude/scripts/build-skills-manifest.py`*",
    ]

    return "\n".join(lines)


def generate_agent_index(agents: list) -> str:
    """Standalone AGENT-INDEX.md — split out of MASTER-INDEX so a skill-pick pass
    never has to load the 221-agent list."""
    today = date.today().isoformat()
    lines = [
        "# Agent Index",
        f"Generated: {today} | Agents: {len(agents)}",
        "",
        "Companion to `MASTER-INDEX.md` (skills). Invoke with "
        "`Agent(subagent_type: \"name\")` — name matches the frontmatter `name:` field.",
        "",
        "---",
        "",
    ]
    by_cat_a = defaultdict(list)
    for a in agents:
        by_cat_a[a["category"]].append(a)
    for cat_id, cat_name in CATEGORY_NAMES.items():
        cat_agents = sorted(by_cat_a.get(cat_id, []), key=lambda x: x["name"])
        if not cat_agents:
            continue
        lines.append(f"### {cat_name} ({len(cat_agents)})")
        for a in cat_agents:
            desc = a["description"][:100].rstrip()
            lines.append(f"- **`{a['name']}`** — {desc}")
        lines.append("")
    lines += ["---", "",
              f"*Regenerate: `python3 /mnt/d/MyVault/.claude/scripts/build-skills-manifest.py`*"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Vault: {VAULT}", file=sys.stderr)

    print("Scanning skills...", file=sys.stderr)
    skills = scan_skills()
    print(f"  {len(skills)} skills found", file=sys.stderr)

    print("Scanning agents...", file=sys.stderr)
    agents = scan_agents()
    print(f"  {len(agents)} agents found", file=sys.stderr)

    manifest = {"generated": date.today().isoformat(), "skills": skills, "agents": agents}
    OUT_JSON.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT_JSON}", file=sys.stderr)

    index_text = generate_index(skills, agents)
    OUT_INDEX.write_text(index_text, encoding="utf-8")
    print(f"Wrote {OUT_INDEX}", file=sys.stderr)

    OUT_AGENTS.write_text(generate_agent_index(agents), encoding="utf-8")
    print(f"Wrote {OUT_AGENTS}", file=sys.stderr)

    print(f"\nDone: {len(skills)} skills, {len(agents)} agents indexed.", file=sys.stderr)
    print(f"Index: {OUT_INDEX}", file=sys.stderr)


if __name__ == "__main__":
    main()
