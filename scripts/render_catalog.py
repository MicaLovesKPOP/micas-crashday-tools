"""Render the public landing page and download index from one release catalog.

Python 3.7+; no network, credentials, private source locations or build side effects.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "MicaLovesKPOP/micas-crashday-tools"
BASE = "https://github.com/" + REPO
GROUPS = (
    ("create", "Create a Crashday map from Blender", "Use Tile Splitter and World Builder together. Start with geometry already in Blender; the current workflow is multi-step."),
    ("import", "Import existing Crashday maps", "Inspect and edit native maps in Blender without modifying the game."),
    ("inspect", "Inspect and troubleshoot assets", "Independent read-only command-line utilities. No Blender dependency."),
)
FIELDS = {"id", "version", "tag", "commit", "public_dir", "license", "asset", "sha256",
          "title", "group", "requirements", "summary"}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def read_catalog(root=ROOT):
    c = json.loads((root / "docs/catalog.json").read_text(encoding="utf-8"))
    require(c["schema"] == "micas-crashday-tools-catalog/v1" and c["repository"] == REPO, "Catalog identity")
    seen, tags = set(), set()
    for m in c["modules"]:
        require(set(m) == FIELDS and all(isinstance(v, str) and v for v in m.values()), "Module fields")
        require(re.fullmatch("[a-z0-9-]+", m["id"]) and m["id"] not in seen, "Duplicate or invalid module ID")
        require(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", m["version"]), "Version")
        require(re.fullmatch(r"[a-z0-9-]+-v[0-9]+(?:\.[0-9]+){1,2}", m["tag"]) and m["tag"].endswith("-v"+m["version"]) and m["tag"] not in tags, "Tag")
        require(re.fullmatch("[a-f0-9]{40}", m["commit"]) and re.fullmatch("[a-f0-9]{64}", m["sha256"]), "Commit or digest")
        require(m["asset"] == "micas-crashday-tools-"+m["tag"]+".zip", "Asset filename")
        require(m["public_dir"] in ("addons/"+m["id"], "utilities/"+m["id"]), "Public directory")
        require(m["group"] in {g[0] for g in GROUPS}, "Unknown group")
        require(m["license"] in {"GPL-2.0-only","GPL-2.0-or-later","GPL-3.0-or-later","MIT"}, "License scope")
        require(all((root / m["public_dir"] / f).is_file() for f in ("README.md","LICENSE","NOTICE.md","CHANGELOG.md")), "Missing module docs")
        for key in ("title","summary","requirements"):
            require(not any(x in m[key] for x in ("|","\n","\r","<",">","[","]")), "Unsafe display field")
        seen.add(m["id"])
        tags.add(m["tag"])
    require(bool(seen), "Empty catalog")
    pair = c["tested_map_creation_pair"]
    by_id = {m["id"]:m for m in c["modules"]}
    require(len(pair["modules"]) == 2 and {m["id"] for m in pair["modules"]} == {"tile-splitter","world-builder"}, "Invalid tested pair")
    require(isinstance(pair["evidence"], str) and bool(pair["evidence"]), "Missing pair evidence")
    for m in pair["modules"]:
        require(set(m) == {"id","version","tag","asset","sha256","commit"}, "Pair fields")
        require(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", m["version"]) and m["tag"] == m["id"]+"-v"+m["version"], "Pair tag")
        require(m["asset"] == "micas-crashday-tools-"+m["tag"]+".zip" and re.fullmatch("[a-f0-9]{64}", m["sha256"]) and re.fullmatch("[a-f0-9]{40}", m["commit"]), "Pair identity")
        latest = by_id[m["id"]]
        if m["version"] == latest["version"]:
            require(all(m[k] == latest[k] for k in m), "Pair differs from same published version")
        # A previously tested pair may legitimately lag a newer standalone release.
        require(tuple(map(int,m["version"].split("."))) <= tuple(map(int,latest["version"].split("."))), "Pair version newer than catalog")
    return c

def download(m):
    return BASE + "/releases/download/" + m["tag"] + "/" + m["asset"]

def release(m):
    return BASE + "/releases/tag/" + m["tag"]

def render_readme(c):
    out = [
        "<!-- Generated from docs/catalog.json by scripts/render_catalog.py. -->",
        "# Mica's Crashday Tools", "",
        "Create, convert, edit and validate worlds and assets for **Crashday: Redline Edition**.", "",
        "Independent open-source Blender add-ons and standalone utilities. Separate versions and installers; original game files stay untouched.", "",
        "**[Downloads](docs/downloads.md) | [Getting started](docs/index.md) | [Compatibility](docs/compatibility.md) | [Roadmap](docs/roadmap.md) | [Get help]("+BASE+"/issues)**", "",
        "## What do you want to do?", "",
    ]
    for key, title, intro in GROUPS:
        out.extend(["### "+title, "", intro, ""])
        for m in c["modules"]:
            if m["group"] != key:
                continue
            out.extend(["**"+m["title"]+"** — "+m["summary"],
                        "", "[Guide]("+m["public_dir"]+"/) · [Download v"+m["version"]+"]("+download(m)+") · [Release notes]("+release(m)+")", ""])
    out.extend([
        "## Before you install", "",
        "Download the individual release ZIP, not GitHub's repository-wide source archive. "
        "See [installation and compatibility](docs/compatibility.md). Blender 2.80, 2.83, 3.4.1 and 4.5 LTS were exercised on the underlying add-ons; individual guides explain exact coverage. CLI tools require Python 3.7+.", "",
        "The current map-building workflow is offline and multi-step. Fully automated preparation is planned, not shipped. "
        "Advanced native collision, custom checkpoint behavior and gameplay have documented limits. No original game assets are included.", "",
        "## Contribute", "",
        "[Contributing](CONTRIBUTING.md) · [Architecture](docs/architecture.md) · [Security](SECURITY.md) · [License scopes](LICENSES.md)", "",
        "Independent community project, unaffiliated with the game's rights holders.", ""
    ])
    return "\n".join(out)

def render_downloads(c):
    out = ["<!-- Generated from docs/catalog.json by scripts/render_catalog.py. -->",
           "# Downloads", "", "[Home](../README.md) · [Getting started](index.md) · [Compatibility](compatibility.md)", "",
           "Choose the installer for your task. Every module is independently versioned; the repository-wide Latest release is not the latest version of each tool.", "",
           "| Tool | Version | Requirements | Download | Source / notes |", "|---|---|---|---|---|"]
    for key, _, _ in GROUPS:
        for m in c["modules"]:
            if m["group"] == key:
                source = BASE+"/tree/"+m["tag"]+"/"+m["public_dir"]
                out.append("| "+m["title"]+" | "+m["version"]+" | "+m["requirements"]+" | [ZIP]("+download(m)+") | [Source]("+source+") · [Notes]("+release(m)+") |")
    pair = c["tested_map_creation_pair"]
    out.extend(["", "## Map creation", "",
                "Tested combination: " + " + ".join("[" + ("Tile Splitter" if m["id"] == "tile-splitter" else "World Builder") + " v" + m["version"] + "](" + download(m) + ")" for m in pair["modules"]) + ". " + pair["evidence"], "",
                "Install both individual ZIPs. A combined convenience kit may be offered later.", "",
                "## Verify your download", "",
                "Each release includes SHA256SUMS.txt; compare its hash with your downloaded ZIP. The [machine-readable catalog](catalog.json) also records the exact published ZIP SHA-256, tag, source commit and license for every module.", "",
                "For command-line tools, extract the ZIP and follow the module guide. For add-ons, use Blender Preferences to install the ZIP itself. Do not unpack files into the game folder.", ""])
    return "\n".join(out)

def outputs(root=ROOT):
    c = read_catalog(root)
    return {root/"README.md":render_readme(c), root/"docs/downloads.md":render_downloads(c)}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="Fail if checked-in pages differ; write nothing")
    args = p.parse_args()
    for path, content in outputs().items():
        if args.check:
            require(path.read_text(encoding="utf-8") == content, "Regenerate "+str(path.relative_to(ROOT)))
        else:
            path.write_bytes(content.encode("utf-8"))
    print("Public catalog pages " + ("verified" if args.check else "generated"))

if __name__ == "__main__":
    main()
