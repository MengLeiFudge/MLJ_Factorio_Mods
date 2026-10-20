# PROJECT KNOWLEDGE BASE

## OVERVIEW

Factorio Lua mod workspace with multiple standalone mods plus one shared packer.
Each mod keeps authored source under `<mod>/src/`, and `pack_mods.py` packages
those sources into loadable Factorio archives under `ModZips/`.

## STRUCTURE

```text
mlj_factorio_mods/
├── pack_mods.py                # discovers <mod>/src/info.json and packs all mods
├── publish_mods.py             # explicit Mod Portal release script; never called by pack_mods.py
├── ModZips/                    # packaged artifacts only; do not edit as source
├── tools/blueprint_lab/        # offline blueprint analysis/generation tooling
├── tools/update_factorio_mods.py # parallel local Mod Portal updater
├── references/mods/            # local third-party reference mods; ignored, read-only
├── DynamicInventory/           # runtime/settings inventory resizing mod
├── expend-toolbar/             # runtime/data/settings custom toolbar mod
├── py_quick_start/             # runtime/settings starter-items mod
├── quality-cycler/             # runtime/settings blueprint and upgrade planner quality cycling mod
├── section-autocraft/          # runtime/data/settings logistics-section autocraft mod
└── ups_saving_quality_ships/   # runtime/data Space Age platform quality mod
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Pack or validate all mods | `pack_mods.py` | Source of truth for discovery, ignored dirs, entrypoint names, zip naming, and opening `ModZips/` |
| Publish releases to Mod Portal | `publish_mods.py` | Explicit release-only tool; compares local `src/info.json` versions with Mod Portal releases and uploads only with `--execute` |
| Offline blueprint analysis/generation | `tools/blueprint_lab/` | Decode/re-encode blueprint strings, scan local blueprint corpora, record layout metrics, and generate seed blueprints outside any mod package |
| Update locally installed mods | `tools/update_factorio_mods.py` | Closed-game utility that selects current-version-compatible releases, downloads in parallel, verifies packages, and removes superseded zips |
| Third-party reference mods | `references/mods/` | Local read-only reference code; ignored by Git and not a package input |
| Runtime inventory logic | `DynamicInventory/` | Runtime/settings mod; no `data.lua` |
| Custom toolbar logic | `expend-toolbar/` | Runtime/data/settings mod; compact runtime modules live directly under `src/` as `names.lua`, `stock.lua`, `panel.lua`, and `runtime.lua` |
| Starter item logic | `py_quick_start/` | Runtime/settings mod; no `data.lua` |
| Blueprint quality cycling | `quality-cycler/` | Runtime/settings mod; blueprint, blueprint book, and upgrade planner quality controls |
| Section Autocraft logic | `section-autocraft/` | Lua source directly under `src/` |
| Platform/cargo quality logic | `ups_saving_quality_ships/` | Event-driven runtime modules under `src/scripts/` |

## FACTORIO AUTHORING REFERENCES

- Official modding tutorial: `https://wiki.factorio.com/Tutorial:Modding_tutorial/Gangsir`
- Latest Factorio API docs: `https://lua-api.factorio.com/latest/index.html`
- Mod settings tutorial: `https://wiki.factorio.com/Tutorial:Mod_settings`
- Prototype docs: `https://lua-api.factorio.com/latest/index-prototype.html`
- Runtime docs: `https://lua-api.factorio.com/latest/index-runtime.html`
- Auxiliary docs: `https://lua-api.factorio.com/latest/index-auxiliary.html`
- Data lifecycle docs: `https://lua-api.factorio.com/latest/auxiliary/data-lifecycle.html`

Treat the official docs as authority. The notes below are local reminders, not a
replacement for the current Factorio API.

## FACTORIO LOAD ORDER AND FILE SHAPE

- Factorio sorts mods by dependency first, then by name where dependency order
  does not force a relationship. If mod `a` depends on `c`, `c` loads before
  `a`; an unrelated mod `b` may still sort before that dependency chain.
- Stage files are loaded across all enabled mods by stage order:
  `settings.lua`, `settings-updates.lua`, `settings-final-fixes.lua`,
  `data.lua`, `data-updates.lua`, `data-final-fixes.lua`.
- Runtime code belongs in `control.lua`; prototype edits belong in `data*.lua`;
  settings definitions belong in `settings*.lua`.
- Standard Factorio mod file shape is:

```text
<mod-folder>/
├── info.json
├── settings.lua
├── settings-updates.lua
├── settings-final-fixes.lua
├── data.lua
├── data-updates.lua
├── data-final-fixes.lua
├── control.lua
└── locale/
    └── <language>/
        └── locale.cfg
```

This workspace stores that shape under `<mod>/src/` and lets `pack_mods.py`
copy the contents into the archive root.

## CONVENTIONS

- This workspace stores mod metadata at `<mod>/src/info.json`, not at the mod
  root. `pack_mods.py` only discovers projects with that layout.
- Every mod root must have both `README.md` and `AGENTS.md`. `README.md` is
  player-facing or maintainer-facing documentation; `AGENTS.md` is the
  execution contract for that mod.
- Mod `README.md` files are player-facing documents. The goal is not to be
  short; the goal is that a player can understand what the mod does, how to use
  it, and how to judge whether behavior is expected. Keep implementation logic
  practical, but keep examples, tables, and step-by-step explanations when they
  make the mod easier to understand.
- Mod `README.md` files must make the Chinese section easy to find. Add a simple
  portable Markdown line near the top, before the English title:
  `> 下拉以查看中文说明。`. Do not use GitHub-only alert syntax such as
  `[!TIP]`.
- If a mod README has a player-facing slogan, quote, or tagline, keep it at the
  very top of the file before the Chinese-section hint.
- Mod `README.md` files must use this bilingual structure after any preserved
  top slogan and the Chinese-section hint: English section first, then the
  Chinese section. The fixed section order is `Overview`, `Features`, `Usage`,
  `Acknowledgments`, then `中文说明`, `概览`, `功能简介`, `使用说明`, `致谢`.
- The README overview must be short, precise, and aligned with `src/info.json`
  plus the mod locale description when one exists.
- README paragraphs, list items, and table cells must not be manually wrapped
  across multiple lines. Markdown structural line breaks for headings, blank
  lines, separate list items, tables, code blocks, and nested headings inside
  `Usage` / `使用说明` are allowed. Use normal heading hierarchy: `##` sections
  should use `###` for direct child sections, and only use deeper heading levels
  when the content genuinely needs deeper nesting.
- Every mod README must include at least one concrete player-facing example.
  For complex mods, keep or add the tables, examples, and step-by-step
  explanations needed for players to understand expected behavior; do not remove
  useful examples merely to make the file shorter or fit the four top-level
  sections.
- README acknowledgments may include multiple concrete sources of help such as
  idea origin, testing help, reference projects, or documentation help. The last
  acknowledgment bullet in each language must thank all players who use the mod.
- The final line in each README language section must link to both
  `http://mods.factorio.com/mod/<info.name>/discussion` and the GitHub mod
  directory under
  `https://github.com/MengLeiFudge/MLJ_Factorio_Mods/tree/master/<mod-directory>`.
- Zip outputs must be `{info.name}_{info.version}.zip`, and the archive root
  must be `{info.name}_{info.version}/` rather than loose files.
- Runtime persistence uses `storage`, not legacy `global`.
- Locale files stay under `src/locale/<lang>/...`; do not create AGENTS files
  inside locale trees.
- Mod changelogs are runtime package files and must live at
  `<mod>/src/changelog.txt`. Do not keep or add root-level `<mod>/changelog.txt`
  fallbacks; if a mod does not satisfy the layout, fix the mod layout.
- This workspace does not maintain test files or test directories. Use focused source/diff review and explicitly authorized packaging or build checks instead.
- Offline repository tools belong under `tools/`. They are not Factorio runtime
  mod files and must not be copied into any `<mod>/src/` tree.
- Third-party reference mods belong under `references/mods/`. Keep them out of
  Git and do not treat them as package inputs, authored source, or build
  dependencies unless a future task explicitly promotes a specific file into an
  owned mod.
- One-off investigation scripts are temporary. Put them under `.codex/` while working, then archive or remove them before handoff; do not promote them into a test suite.
- Bilingual content is normal in docs, locale, and some comments; preserve
  meaning when touching mixed Chinese/English text.
- In WSL, `pack_mods.py` should open `ModZips/` with Windows Explorer through
  `/mnt/c/Windows/explorer.exe` after converting the path with `wslpath -w`.
- Standard debug deployment rule: after a mod is successfully packaged,
  `pack_mods.py` checks `%AppData%\Factorio\mods` for `{info.name}_*.zip`. If
  a zip exists, skip folder deployment for that mod. If no zip exists, copy the
  unpacked `{info.name}_{info.version}/` folder for the current package.
- Mod Portal publishing is a separate explicit release action. Do not call
  `publish_mods.py` from `pack_mods.py`, automatic packaging, or local debug deployment.
- `publish_mods.py` defaults to dry-run. Real uploads require explicit `--execute` plus a token from `FACTORIO_MOD_PORTAL_TOKEN` or the ignored local `publish_mods.config.json`; the environment variable takes precedence.
- Never commit Factorio API keys, bearer tokens, `.env` files, `publish_mods.config.json`, or publish logs that include credentials. Keep `publish_mods.config.example.json` token-free.
- First-time Mod Portal creation must be explicit with `--create`; otherwise a
  missing remote mod is an error, not an automatic publish.
- Before running a real `publish_mods.py --execute`, verify that the matching
  `ModZips/{info.name}_{info.version}.zip` was freshly produced and that the
  user has explicitly asked to publish.

## ANTI-PATTERNS

- Do not mix Factorio stages: `game` and runtime objects belong in `control.lua`;
  `data` and prototype work belong in `data*.lua`.
- Do not edit `ModZips/`, `obj/`, or `__pycache__/` as source; they are outputs
  or caches.
- Do not add test files or test directories at the repository root or inside any mod tree.
- Do not move `info.json` out of `src/`; the packer will stop discovering that
  mod.
- Do not assume README commands are backed by manifests in this checkout; verify
  local tooling files before claiming a build step exists.
- Do not push unless explicitly approved by the user.

## COMMANDS

```bash
python3 pack_mods.py
```

## BUILD AND DEBUG DEPLOYMENT

- Run `python3 pack_mods.py` to package every discovered mod.
- Confirm the target artifact name under `ModZips/` and, when the Windows
  Factorio mods directory exists, under `%AppData%\Factorio\mods`.
- `pack_mods.py` also performs debug folder deployment for every successfully
  packaged mod. It first copies the generated zip to `%AppData%\Factorio\mods`,
  then applies this folder rule: if `%AppData%\Factorio\mods` already contains
  `{info.name}_*.zip`, skip copying the folder; otherwise copy the unpacked
  `{info.name}_{info.version}/` folder for the current package.
- The folder deployed to `%AppData%\Factorio\mods` must be the archive root,
  for example `{info.name}_{info.version}/`; do not deploy raw `src/` directly.

## MOD PORTAL RELEASES

- Run `python3 publish_mods.py --dry-run` to compare local mod versions with the Mod Portal without uploading.
- Run `python3 publish_mods.py --mod <info.name> --dry-run` to inspect a single mod before release.
- Only run `python3 publish_mods.py --mod <info.name> --execute` when the user explicitly asks to publish that mod and a token is available through the environment or ignored local config.
- Use `--create` only for first-time Mod Portal publication of a missing mod.
- `publish_mods.py` does not build zips. Run `python3 pack_mods.py` first when a fresh archive is needed, then confirm the expected `ModZips/` artifact exists.
- `--config-file` can select another local JSON config; never point it at a tracked credential file.
- `--require-clean` is available when the release should fail on any dirty Git worktree state; without it, `--execute` prints dirty paths as a warning.

## GIT PRACTICES

- Commit messages in Chinese, conventional style: `功能：`, `修复：`, `重构：`,
  `杂项：`.
- Keep commits atomic: one logical change per commit.
- When running `git add` or `git commit` in this workspace, prefer the host
  Windows Git executable: `'/mnt/c/Program Files/Git/cmd/git.exe'`.
- All Git operations must run serially. Do not run `git add`, `git commit`,
  `git rebase`, `git stash`, `git checkout`, `git merge`, or any other Git
  command in parallel.
- Do not push unless explicitly approved by the user.

## CODEX WORKFLOW CONFIRMATION

- Read the nearest `AGENTS.md` before editing and obey the most specific file.
- Inspect the current code and real workspace state before making claims.
- When a discussion establishes a durable workflow, documentation, packaging,
  release, README, or authoring convention, record it in the appropriate
  long-lived repository document before handoff. Use root or nearest
  `AGENTS.md` for execution contracts and contributor workflow rules; use
  `README.md` for player-facing or maintainer-facing public documentation.
  Drafts under `.codex/drafts/` are only discussion notes and must not be the
  only place where an agreed convention lives.
- Prefer direct execution for clear requests; ask only when a missing decision is
  risky or cannot be inferred.
- Before deleting files, moving files, changing system configuration, or doing
  other high-risk operations, request explicit confirmation.
- Keep edits scoped to the requested behavior and existing project patterns.
- Use `apply_patch` for manual file edits.
- Run relevant verification before saying work is complete, fixed, or passing.
- Report exact commands and real outcomes, including skipped or failed
  verification.

## NOTES

- `pack_mods.py` ignores `.git`, `.idea`, `.vscode`, `.vs`, `__pycache__`,
  `bin`, `obj`, `tests`, `references`, `AGENTS.md`, `README.md`, plus `.zip`
  and `.psd` files.
- There is no repo-wide CI or test suite in this checkout. Use focused source/diff review and `python3 pack_mods.py` when packaging verification is explicitly required.
- If a change may affect save compatibility, migration behavior, event legality,
  or Factorio API usage, re-check the official Factorio docs before editing.
