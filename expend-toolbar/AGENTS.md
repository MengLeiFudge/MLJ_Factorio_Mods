# Expend Toolbar Notes

## OVERVIEW

This mod is a Lua-first Factorio 2.1 mod that lets players create movable custom toolbars for items, tools, inventory counts, and remote-view access.

## WHERE TO LOOK

- Metadata: `expend-toolbar/src/info.json`
- Runtime entry: `expend-toolbar/src/control.lua`
- Data stage: `expend-toolbar/src/data.lua`, `expend-toolbar/src/data-final-fixes.lua`
- Settings stage: `expend-toolbar/src/settings.lua`
- Shared names and prototype ids: `expend-toolbar/src/names.lua`
- Runtime event registration: `expend-toolbar/src/runtime.lua`
- GUI drawing and toolbar state operations: `expend-toolbar/src/panel.lua`
- Inventory and quality counting: `expend-toolbar/src/stock.lua`
- Packaged resources: `expend-toolbar/src/_graphics/`, `expend-toolbar/src/locale/`

## CONVENTIONS

- Treat `expend-toolbar` as the only current mod identity. Keep `src/info.json` `name`, `names.mod`, shortcut names, sprite names, and `__expend-toolbar__` resource paths aligned.
- Do not reintroduce the old `toolbars-mod` identity or old Toolbars OOP identity.
- Keep authored files under `src/`; root `README.md` and `AGENTS.md` are documentation and must not be copied into runtime package contents.
- This mod intentionally does not keep compatibility with the previous Toolbars-style OOP runtime. Do not restore `core/`, `factorio/`, `gui/`, `lang/`, `model/`, `player/`, `control/`, `data/`, or `settings/` runtime subtrees.
- Keep runtime logic compact and process-oriented. The expected custom Lua files are `names.lua`, `stock.lua`, `panel.lua`, and `runtime.lua`, plus Factorio entry files.
- Personal-view logistic networks are side tooltip data, not part of the slot count overlay. Normal tooltip count rows label player-owned counts as `Player` / `玩家` and side logistic or remote supply with the current `LuaSurface.name`. Remote planet logistic networks are main count data, and remote tooltip rows show only the current surface name.
- Each toolbar starts with one page. Players may add or delete pages, and the maximum page count is the per-user `columns` value so the narrowest bottom tab is one slot wide.
- Page switching uses bottom self-drawn tab buttons, not Factorio `tabbed-pane`, overflow paging, drop-down page pickers, or header arrows.
- Bottom page tabs show page titles when there is enough width and page numbers when space is tight. Tooltips must keep the full page title.
- Normal item slot tooltips should use `LuaGuiElement.elem_tooltip` with `item-with-quality` so Factorio keeps the vanilla item tooltip. Append only non-zero labelled quality count rows in low-to-high quality order in the custom `tooltip`; do not replace vanilla prototype details with hand-built `name` / `type` / `stack` / `place` text. Blueprint-like tool slots use only vanilla `elem_tooltip = { type = "item", name = tool_name }`; do not append saved label, entity count, book index, book size, export state, record link, or quality-control hint text to blueprint-like tooltips. Tooltip cache keys for normal items must include the current count signature, controller type, locale, and hint setting so hover refreshes do not reshape a visible tooltip with late side-count rows.
- Slot payloads must preserve special tools when possible: normal items use `name` plus `grade`, exported blueprint-like stacks use `LuaItemStack.export_stack()` plus lightweight `preview_icons` / `default_icons`, and pure cursor records store lightweight `LuaRecord` metadata, preview icons, and a blueprint-library source/path link. Preview icon copies must preserve `BlueprintSignalIcon.signal.quality`. Treat preview coordinates as the tool icon's white dashed inner frame, not the whole 40x40 slot. The base blueprint icon is close to the whole slot and can use 16x16 preview signal cells: one signal at x=12,y=12; two signals at x=8,y=12 and x=24,y=12; three or four signals as a 2x2 grid starting at x=8,y=4. The blueprint-book icon must use only the lower-left main-book frame, not the full slot or the decorative books in the upper-right; use smaller 11x11 preview signal cells inside that frame. Blueprint preview quality markers must be small lower-left markers derived from each preview signal cell, not standalone large slot icons or independent guessed offsets. Prefer `player.cursor_stack` over `player.cursor_record` so real blueprint items are exported as stacks. Do not store `LuaRecord.export_record()` output in the slot for pure cursor records; resolve the link against `player.blueprints` or `game.blueprints` only when copying back to the cursor, then fall back to the matching tool item if the link is gone or import fails. Blueprint-like toolbar copies must mark `player.cursor_stack_temporary = true` after restoring the cursor stack so clearing the cursor does not create inventory copies; do not represent blueprint-like slots as inventory-counted item stacks.
- Slot quality adjustment and the main slot quality overlay apply only to inventory-counted normal item slots. Blueprint-like tools have no toolbar stack count or slot quality concept; only their preview signals may carry quality markers.
- Slot clicks must respect controller context: normal non-remote views try to lift a real item stack from the player's own inventories first, while remote view falls back to a ghost or matching tool item.
- Toolbar pages use the per-user `columns` value as their configured width. Rows grow when the last slot is occupied and shrink when the tail rows are no longer needed.
- Guard quality reads from `prototypes.quality[...]` because removed quality mods or stale item records can contain quality names that no longer exist.
- Keep player-facing behavior documented in this directory's `README.md` when controls, settings, visible counts, remote view, or tooltip behavior changes.

## ANTI-PATTERNS

- Do not hard-code `toolbars-mod_*` prototype names or `__toolbars-mod__` asset paths.
- Do not count personal-view side inventories in the slot count overlay unless the player-facing behavior is intentionally changed and the README is updated.
- Do not reintroduce an inner `src/src/` tree. `src/` is already the Factorio package root; Lua modules should be nested directly below it by logical domain.
- Do not reintroduce one-class-per-file component trees for toolbar buttons, slots, events, or inventory wrappers.
- Do not add tests or investigation scripts inside `src/`. Keep temporary investigation scripts under `.codex/` and archive or remove them before handoff.

## VERIFICATION

- Run `python3 pack_mods.py` after source or package-identity changes and confirm `ModZips/expend-toolbar_1.0.0.zip` is produced.
- For refresh bugs, inspect `stock.lua`, `panel.lua`, and `runtime.lua`. Player inventory/controller/surface events should call the immediate `refresh_now` path; the dirty repaint path is reserved for polling-only cases such as remote view or enabled logistic tooltip data.
