"""LaTeX resume → plaintext candidate profile.

The awesomecv template uses a handful of commands we strip with regex.
The goal is a clean paragraph-style text file the scorer can feed to the LLM,
not a pixel-perfect conversion.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import candidate_name, resume_dir, resume_profile_path

SECTIONS = [
    "summary.tex",
    "selected-impact.tex",
    "skills.tex",
    "experience.tex",
    "education.tex",
    "certifications.tex",
    "languages-tools.tex",
]


def _strip_latex(s: str) -> str:
    """Best-effort LaTeX → plaintext for awesome-cv source files."""
    # Drop comments
    s = re.sub(r"(?m)(?<!\\)%.*$", "", s)

    # Common wrapper envs → keep inner content
    for env in ("cvparagraph", "cventries", "cvitems", "cvskills", "justify"):
        s = re.sub(rf"\\begin{{{env}}}", "", s)
        s = re.sub(rf"\\end{{{env}}}", "", s)

    # \cvsection{X} → "## X"
    s = re.sub(r"\\cvsection\{([^}]*)\}", r"\n## \1\n", s)

    # \cventry {title}{org}{loc}{dates}{body}
    def _entry(m: re.Match[str]) -> str:
        args = _collect_braced_groups(s, m.end() - 1, n=5)
        if not args:
            return ""
        title, org, loc, dates, body = args
        head = " — ".join(x for x in (title, org, loc, dates) if x)
        return f"\n### {head}\n{body}\n"

    s = re.sub(r"\\cventry", _entry, s)

    # \cvskill {category}{value} → "- category: value"
    s = re.sub(r"\\cvskill\s*\{([^}]*)\}\s*\{([^}]*)\}", r"- \1: \2", s)

    # \item {...}  →  "- ..."
    s = re.sub(r"\\item\s*\{([^}]*)\}", r"- \1", s)
    s = re.sub(r"\\item\s+", r"- ", s)

    # \textbf{X}, \textit{X}, \emph{X} → X
    s = re.sub(r"\\(?:textbf|textit|emph)\{([^}]*)\}", r"\1", s)

    # \input{...} and other trailing commands we don't care about
    s = re.sub(r"\\input\{[^}]*\}", "", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", "", s)

    # Escaped ampersands, percents
    s = s.replace("\\&", "&").replace("\\%", "%").replace("~", " ")

    # Collapse whitespace
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _collect_braced_groups(full: str, start: int, n: int) -> list[str] | None:
    """Return the next n {...} groups starting at index `start`, allowing nesting.

    Used only as a fallback for \\cventry — in practice the regex above catches
    it, but we keep the machinery simple and robust.
    """
    out: list[str] = []
    i = start
    while len(out) < n and i < len(full):
        while i < len(full) and full[i].isspace():
            i += 1
        if i >= len(full) or full[i] != "{":
            return None
        depth = 0
        j = i
        while j < len(full):
            if full[j] == "{":
                depth += 1
            elif full[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(full):
            return None
        out.append(full[i + 1 : j])
        i = j + 1
    return out if len(out) == n else None


def build_profile() -> str:
    """Read the awesomecv sources and write data/resume_profile.txt.

    Returns the plaintext content.
    """
    parts: list[str] = []
    rdir = resume_dir()
    for name in SECTIONS:
        p = rdir / name
        if p.exists():
            parts.append(_strip_latex(p.read_text()))

    text = "\n\n".join(x for x in parts if x)
    # Add a small header the LLM can anchor on (name read from profile.yaml)
    header = f"# Candidate Profile — {candidate_name()}\n"
    final = header + "\n" + text + "\n"

    out_path = resume_profile_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final)
    return final


def load_profile() -> str:
    """Return the cached plaintext profile, building it if needed."""
    path = resume_profile_path()
    if not path.exists():
        return build_profile()
    return path.read_text()
