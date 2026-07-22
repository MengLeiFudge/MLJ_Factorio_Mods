local constants = require("constants")

local autocraft = {}

local AUTOCRAFT_MAX_CRAFT_BATCH_SIZE = 10000
local AUTOCRAFT_MAX_MISSING_CRAFTS = 100
local AUTOCRAFT_MISSING_SECTION_COOLDOWN_TICKS = 60
local AUTOCRAFT_NO_CRAFTABLE_RETRY_TICKS = 180
local AUTOCRAFT_PERFORMANCE_PROFILE_ENABLED = false
local DEFAULT_QUALITY_NAME = "normal"

local performance_profile_state = {
  next_log_tick = nil,
  scopes = {},
}

local function starts_with(value, prefix)
  return string.sub(value, 1, #prefix) == prefix
end

local function reset_performance_profile_state()
  performance_profile_state.scopes = {}
  performance_profile_state.next_log_tick = game and (game.tick + 60) or nil
end

local function is_performance_debug_enabled()
  return AUTOCRAFT_PERFORMANCE_PROFILE_ENABLED
end

local function build_profile_detail_text(details)
  if not details then
    return ""
  end

  local keys = {}
  for key in pairs(details) do
    keys[#keys + 1] = key
  end
  table.sort(keys)

  local parts = {}
  for _, key in ipairs(keys) do
    parts[#parts + 1] = " " .. key .. "=" .. tostring(details[key])
  end
  return table.concat(parts)
end

local function flush_performance_profile()
  if not is_performance_debug_enabled() then
    reset_performance_profile_state()
    return
  end

  if not performance_profile_state.next_log_tick then
    performance_profile_state.next_log_tick = game.tick + 60
    return
  end

  if game.tick < performance_profile_state.next_log_tick then
    return
  end

  local scope_names = {}
  for scope_name in pairs(performance_profile_state.scopes) do
    scope_names[#scope_names + 1] = scope_name
  end
  table.sort(scope_names)

  for _, scope_name in ipairs(scope_names) do
    local scope = performance_profile_state.scopes[scope_name]
    if scope.calls > 0 then
      local average = game.create_profiler(true)
      average.add(scope.total)
      average.divide(scope.calls)
      log({
        "",
        "[section-autocraft-profile] tick=",
        tostring(game.tick),
        " scope=",
        scope_name,
        " calls=",
        tostring(scope.calls),
        build_profile_detail_text(scope.details),
        " avg=",
        average,
        " total=",
        scope.total,
      })
    end
  end

  performance_profile_state.scopes = {}
  performance_profile_state.next_log_tick = game.tick + 60
end

function autocraft.start_profile()
  if not is_performance_debug_enabled() then
    return nil
  end

  return game.create_profiler()
end

function autocraft.record_profile(scope_name, profiler, details)
  if not profiler then
    return
  end

  profiler.stop()
  local scope = performance_profile_state.scopes[scope_name]
  if not scope then
    scope = {
      calls = 0,
      details = {},
      total = game.create_profiler(true),
    }
    performance_profile_state.scopes[scope_name] = scope
  end

  scope.calls = scope.calls + 1
  scope.total.add(profiler)
  if details then
    for key, value in pairs(details) do
      scope.details[key] = (scope.details[key] or 0) + value
    end
  end

  flush_performance_profile()
end

local function get_player_data(player)
  storage.data = storage.data or {}
  storage.missing_section_players = storage.missing_section_players or {}
  local data = storage.data[player.index] or {
    enabled = constants.AUTOCRAFT_DEFAULT_ENABLED,
    last_no_craftable_material_signature = nil,
    last_no_craftable_missing_requests = nil,
    missing_section_index = nil,
    missing_section_name = nil,
    next_no_craftable_retry_tick = nil,
    request_cache_dirty = true,
    requested_items_cache = nil,
    section_status_dirty = true,
    section_status_snapshot = {},
  }
  if data.request_cache_dirty == nil then
    data.request_cache_dirty = true
  end
  if data.section_status_dirty == nil then
    data.section_status_dirty = true
  end
  data.section_status_snapshot = data.section_status_snapshot or {}
  storage.data[player.index] = data
  return data
end

local function clear_no_craftable_state(data)
  data.next_no_craftable_retry_tick = nil
  data.last_no_craftable_missing_requests = nil
  data.last_no_craftable_material_signature = nil
end

local function mark_sections_dirty(player)
  local data = get_player_data(player)
  data.request_cache_dirty = true
  data.requested_items_cache = nil
  data.section_status_dirty = true
  clear_no_craftable_state(data)
end

local function get_configured_prefix(player)
  return player.mod_settings[constants.AUTOCRAFT_PREFIX_SETTING].value or ""
end

local function get_match_mode(player)
  return player.mod_settings[constants.AUTOCRAFT_MATCH_MODE_SETTING].value
end

local function get_display_prefix(player)
  local configured_prefix = get_configured_prefix(player)
  if configured_prefix == "" then
    return constants.AUTOCRAFT_DEFAULT_PREFIX_RICH_TEXT
  end
  return configured_prefix
end

local function get_missing_materials_section_name(player)
  local display_prefix = get_display_prefix(player)
  if player.locale and starts_with(player.locale, "zh") then
    return "[自动]" .. display_prefix .. player.name .. "-缺失材料"
  end
  return "[Auto]" .. display_prefix .. player.name .. "-Missing materials"
end

local function build_section_match_context(player)
  local data = get_player_data(player)
  return {
    enabled = data.enabled,
    configured_prefix = get_configured_prefix(player),
    current_missing_section_name = get_missing_materials_section_name(player),
    match_mode = get_match_mode(player),
    player_name = player.name,
    stored_missing_section_name = data.missing_section_name,
  }
end

local function is_missing_materials_section(section, context)
  return section.is_manual
    and (
      section.group == context.current_missing_section_name
      or (context.stored_missing_section_name and section.group == context.stored_missing_section_name)
    )
end

local function find_missing_materials_section(player)
  local profiler = autocraft.start_profile()
  local scanned_sections = 0
  local logistic_point = player.get_requester_point()
  if not logistic_point or not logistic_point.valid then
    autocraft.record_profile("find_missing_materials_section", profiler, { no_logistic_point = 1 })
    return nil
  end

  local context = build_section_match_context(player)
  local data = get_player_data(player)
  local cached_section = data.missing_section_index and logistic_point.sections[data.missing_section_index] or nil
  if cached_section and cached_section.valid and is_missing_materials_section(cached_section, context) then
    autocraft.record_profile("find_missing_materials_section", profiler, {
      cached = 1,
      found = 1,
    })
    return cached_section
  end

  for _, section in pairs(logistic_point.sections) do
    scanned_sections = scanned_sections + 1
    if is_missing_materials_section(section, context) then
      data.missing_section_index = section.index
      autocraft.record_profile("find_missing_materials_section", profiler, {
        found = 1,
        scanned_sections = scanned_sections,
      })
      return section
    end
  end

  autocraft.record_profile("find_missing_materials_section", profiler, {
    missing = 1,
    scanned_sections = scanned_sections,
  })
  data.missing_section_index = nil
  return nil
end

local function ensure_missing_materials_section(player)
  local logistic_point = player.get_requester_point()
  if not logistic_point then
    return nil
  end

  local data = get_player_data(player)
  local section_name = get_missing_materials_section_name(player)
  local section = find_missing_materials_section(player)

  if section then
    if section.group ~= section_name then
      section.group = section_name
    end
    section.active = true
    data.missing_section_index = section.index
    data.missing_section_name = section_name
    storage.missing_section_players[player.index] = true
    return section
  end

  section = logistic_point.add_section(section_name)
  if section then
    section.active = true
    data.missing_section_index = section.index
    data.missing_section_name = section_name
    storage.missing_section_players[player.index] = true
  end

  return section
end

local function remove_missing_materials_section(player)
  local logistic_point = player.get_requester_point()
  local data = get_player_data(player)
  if not data.missing_section_index and not data.missing_section_name then
    storage.missing_section_players[player.index] = nil
    return
  end

  if not logistic_point then
    data.missing_section_index = nil
    data.missing_section_name = nil
    return
  end

  local section = find_missing_materials_section(player)
  if section and section.valid then
    logistic_point.remove_section(section.index)
  end

  data.missing_section_index = nil
  data.missing_section_name = nil
  storage.missing_section_players[player.index] = nil
end

local function normalise_quality_name(quality)
  if type(quality) == "string" then
    return quality
  end

  if quality and quality.name then
    return quality.name
  end

  return DEFAULT_QUALITY_NAME
end

local function make_item_key(item_name, quality)
  return item_name .. "\n" .. normalise_quality_name(quality)
end

local function is_default_quality(quality)
  return normalise_quality_name(quality) == DEFAULT_QUALITY_NAME
end

local function item_count_filter(item_name, quality)
  return {
    name = item_name,
    quality = normalise_quality_name(quality),
  }
end

local function item_with_quality_id(item_name, quality)
  return {
    name = item_name,
    quality = normalise_quality_name(quality),
  }
end

local function logistic_signal_filter(item_name, quality)
  return {
    type = "item",
    name = item_name,
    quality = normalise_quality_name(quality),
  }
end

local function parse_logistic_filter_item_request(filter)
  if not filter.min or filter.min <= 0 then
    return nil
  end

  if type(filter.value) == "string" then
    return {
      name = filter.value,
      quality = DEFAULT_QUALITY_NAME,
      min = filter.min,
    }
  end

  if filter.value and filter.value.type == "item" and filter.value.name then
    return {
      name = filter.value.name,
      quality = normalise_quality_name(filter.value.quality),
      min = filter.min,
    }
  end

  return nil
end

local function build_missing_material_slot(request)
  return {
    value = logistic_signal_filter(request.name, request.quality),
    min = request.count,
  }
end

local function count_item_partial_space(main_inventory, item_name, quality_name)
  local partial_space = 0
  for slot_index = 1, #main_inventory do
    local stack = main_inventory[slot_index]
    if (
      stack.valid_for_read
      and stack.name == item_name
      and normalise_quality_name(stack.quality) == quality_name
    ) then
      partial_space = partial_space + math.max(0, stack.prototype.stack_size - stack.count)
    end
  end

  return partial_space
end

local function cap_requests_to_main_inventory_capacity(player, request_list)
  local main_inventory = player.get_main_inventory()
  if not main_inventory or not main_inventory.valid then
    return request_list
  end

  local remaining_empty_stacks = main_inventory.count_empty_stacks(false, false)
  local capped_requests = {}

  -- 多种缺料会竞争同一批空格，所以这里按当前排序顺序共享分配空栈容量。
  for _, request in ipairs(request_list) do
    local prototype = prototypes.item[request.name]
    if not prototype then
      capped_requests[#capped_requests + 1] = request
    else
      local stack_size = prototype.stack_size
      local quality_name = normalise_quality_name(request.quality)
      local current_count = main_inventory.get_item_count(item_count_filter(request.name, quality_name))
      local partial_space = count_item_partial_space(main_inventory, request.name, quality_name)
      local max_target_count = current_count + partial_space + remaining_empty_stacks * stack_size
      local capped_count = math.min(request.count, max_target_count)
      capped_count = math.max(capped_count, current_count)

      if capped_count > 0 then
        capped_requests[#capped_requests + 1] = {
          name = request.name,
          quality = quality_name,
          count = capped_count,
        }
      end

      local extra_reserved = math.max(0, capped_count - current_count)
      local extra_from_empty = math.max(0, extra_reserved - partial_space)
      local empty_stacks_used = stack_size > 0 and math.ceil(extra_from_empty / stack_size) or 0
      remaining_empty_stacks = math.max(0, remaining_empty_stacks - empty_stacks_used)
    end
  end

  return capped_requests
end

local function write_missing_materials_section(player, requests)
  local request_list = {}
  for _, request in pairs(requests) do
    if request.count > 0 then
      request_list[#request_list + 1] = {
        name = request.name,
        quality = normalise_quality_name(request.quality),
        count = math.ceil(request.count),
      }
    end
  end

  if #request_list == 0 then
    remove_missing_materials_section(player)
    return
  end

  table.sort(request_list, function(a, b)
    if a.name == b.name then
      return a.quality < b.quality
    end
    return a.name < b.name
  end)

  request_list = cap_requests_to_main_inventory_capacity(player, request_list)

  local section = ensure_missing_materials_section(player)
  if not section or not section.valid then
    return
  end

  section.active = true
  for slot_index, request in ipairs(request_list) do
    section.set_slot(build_missing_material_slot(request), slot_index)
  end

  for slot_index = #request_list + 1, #section.filters do
    section.clear_slot(slot_index)
  end
end

function autocraft.pre_compute_recipes()
  local cache = {}

  for _, recipe in pairs(prototypes.get_recipe_filtered({ { filter = "has-product-item" } })) do
    for _, product in pairs(recipe.products) do
      local recipes = cache[product.name]
      if not recipes then
        recipes = {}
        cache[product.name] = recipes
      end

      recipes[recipe.name] = true
    end
  end

  return cache
end

function autocraft.is_enabled(player)
  return get_player_data(player).enabled
end

function autocraft.set_enabled(player, enabled)
  local data = get_player_data(player)
  local next_enabled = enabled and true or false
  if data.enabled ~= next_enabled then
    data.enabled = next_enabled
    data.section_status_dirty = true
    clear_no_craftable_state(data)
  end
end

function autocraft.mark_sections_dirty(player)
  mark_sections_dirty(player)
end

local function section_has_autocraft_ability(context, section)
  local section_name = section.group or ""

  if context.match_mode == constants.AUTOCRAFT_MATCH_MODE_FULL then
    return true
  end

  if context.match_mode == constants.AUTOCRAFT_MATCH_MODE_PREFIX then
    return context.configured_prefix ~= "" and starts_with(section_name, context.configured_prefix)
  end

  if context.match_mode == constants.AUTOCRAFT_MATCH_MODE_PLAYER_NAME then
    return starts_with(section_name, context.player_name)
  end

  if context.match_mode == constants.AUTOCRAFT_MATCH_MODE_PREFIX_AND_PLAYER_NAME then
    return context.configured_prefix ~= "" and starts_with(section_name, context.configured_prefix .. context.player_name)
  end

  return false
end

local function should_include_section(context, section)
  if not context.enabled then
    return false
  end

  if is_missing_materials_section(section, context) then
    return false
  end

  return section.active and section_has_autocraft_ability(context, section)
end

local function get_section_message_name(section_name)
  if section_name and section_name ~= "" then
    return section_name
  end

  return { "autocraft-message.autocraft-toggle-status-unnamed-group" }
end

local function build_section_status_snapshot(player)
  local profiler = autocraft.start_profile()
  local snapshot = {}
  local included_sections = 0
  local scanned_sections = 0
  local logistic_point = player.get_requester_point()
  if not logistic_point or not logistic_point.valid then
    autocraft.record_profile("build_section_status_snapshot", profiler, { no_logistic_point = 1 })
    return snapshot
  end

  local context = build_section_match_context(player)

  -- 这里保存“当前真正生效的自动手搓分组”，后续统一通过快照 diff 决定是否提示。
  for _, section in pairs(logistic_point.sections) do
    scanned_sections = scanned_sections + 1
    if should_include_section(context, section) then
      included_sections = included_sections + 1
      snapshot[section.index] = {
        name = section.group or "",
      }
    end
  end

  autocraft.record_profile("build_section_status_snapshot", profiler, {
    included_sections = included_sections,
    scanned_sections = scanned_sections,
  })
  return snapshot
end

local function build_section_status_lines(previous_snapshot, next_snapshot)
  local changed_sections = {}
  local indexes = {}

  for section_index in pairs(previous_snapshot) do
    indexes[section_index] = true
  end
  for section_index in pairs(next_snapshot) do
    indexes[section_index] = true
  end

  for section_index in pairs(indexes) do
    local previous_entry = previous_snapshot[section_index]
    local next_entry = next_snapshot[section_index]
    local previous_enabled = previous_entry ~= nil
    local next_enabled = next_entry ~= nil

    if previous_enabled ~= next_enabled then
      local entry = next_entry or previous_entry
      changed_sections[#changed_sections + 1] = {
        name = entry and entry.name or "",
        enabled = next_enabled,
      }
    end
  end

  table.sort(changed_sections, function(a, b)
    if a.name ~= b.name then
      return a.name < b.name
    end

    if a.enabled ~= b.enabled then
      return a.enabled and not b.enabled
    end

    return false
  end)

  local section_lines = {}
  for _, change in ipairs(changed_sections) do
    local action_key = change.enabled and "autocraft-toggle-status-enabled" or "autocraft-toggle-status-disabled"
    section_lines[#section_lines + 1] = {
      "autocraft-message.autocraft-toggle-status-line",
      get_section_message_name(change.name),
      { "autocraft-message." .. action_key },
    }
  end

  return section_lines
end

local function notify_section_status_lines(player, section_lines)
  local message = { "" }
  for index, line in ipairs(section_lines) do
    if index > 1 then
      message[#message + 1] = "\n"
    end
    message[#message + 1] = line
  end

  player.print(message)
end

local function sync_section_status_notifications(player, trigger_mode)
  local data = get_player_data(player)
  local previous_snapshot = data.section_status_snapshot or {}
  -- 30 tick 兜底只在已知编组状态变化后重建快照，避免全匹配模式反复扫所有编组。
  if not data.section_status_dirty and trigger_mode ~= "shortcut" then
    return
  end

  local next_snapshot = build_section_status_snapshot(player)
  data.section_status_snapshot = next_snapshot
  data.section_status_dirty = false

  if trigger_mode == nil then
    return
  end

  local section_lines = build_section_status_lines(previous_snapshot, next_snapshot)
  if trigger_mode == "shortcut" then
    if #section_lines == 0 then
      player.print({ "autocraft-message.autocraft-toggle-status-empty" })
      return
    end

    notify_section_status_lines(player, section_lines)
    return
  end

  if trigger_mode == "logistics" then
    for _, section_line in ipairs(section_lines) do
      notify_section_status_lines(player, { section_line })
    end
  end
end

autocraft.sync_section_status_notifications = sync_section_status_notifications

local function build_requested_items(player)
  local profiler = autocraft.start_profile()
  local requested_items = {}
  local included_sections = 0
  local scanned_filters = 0
  local scanned_sections = 0
  local logistic_point = player.get_requester_point()
  if not logistic_point or not logistic_point.valid then
    autocraft.record_profile("build_requested_items", profiler, { no_logistic_point = 1 })
    return requested_items
  end

  local context = build_section_match_context(player)

  -- 先筛出本次应参与自动手搓的物流分组，再把同类物品的最小保有量汇总成一个总需求。
  for _, section in pairs(logistic_point.sections) do
    scanned_sections = scanned_sections + 1
    if should_include_section(context, section) then
      included_sections = included_sections + 1
      for _, filter in pairs(section.filters) do
        scanned_filters = scanned_filters + 1
        if filter.min and filter.min > 0 then
          local item_request = parse_logistic_filter_item_request(filter)
          if item_request then
            local item_key = make_item_key(item_request.name, item_request.quality)
            local existing_request = requested_items[item_key]
            if existing_request then
              existing_request.min = existing_request.min + item_request.min
            else
              requested_items[item_key] = item_request
            end
          end
        end
      end
    end
  end

  autocraft.record_profile("build_requested_items", profiler, {
    included_sections = included_sections,
    requested_items = table_size(requested_items),
    scanned_filters = scanned_filters,
    scanned_sections = scanned_sections,
  })
  return requested_items
end

local function get_requested_items(player)
  local data = get_player_data(player)
  -- 物流编组请求只在编组/匹配规则变化时重建；背包变化会频繁触发，只复用这份缓存。
  if not data.request_cache_dirty and data.requested_items_cache then
    return data.requested_items_cache
  end

  local requested_items = build_requested_items(player)
  data.requested_items_cache = requested_items
  data.request_cache_dirty = false
  return requested_items
end

local function get_module_queue_index(player)
  local data = storage.data and storage.data[player.index] or nil
  if not data or not data.active_recipe_name or not data.active_queue_index or not player.crafting_queue then
    return nil
  end

  local queue_item = player.crafting_queue[data.active_queue_index]
  if not queue_item or queue_item.prerequisite then
    return nil
  end

  local recipe_name = type(queue_item.recipe) == "string" and queue_item.recipe or queue_item.recipe.name
  if recipe_name ~= data.active_recipe_name then
    return nil
  end

  return data.active_queue_index, queue_item
end

function autocraft.cancel_active_crafting(player)
  local data = storage.data and storage.data[player.index] or nil
  if not data then
    remove_missing_materials_section(player)
    return
  end

  local queue_index, queue_item = get_module_queue_index(player)
  if queue_index and queue_item then
    player.cancel_crafting({ index = queue_index, count = queue_item.count })
  end

  data.active_item_name = nil
  data.active_queue_index = nil
  data.active_recipe_name = nil
  remove_missing_materials_section(player)
end

function autocraft.clear_active_state(player)
  local data = storage.data and storage.data[player.index] or nil
  if not data then
    remove_missing_materials_section(player)
    return
  end

  data.active_item_name = nil
  data.active_queue_index = nil
  data.active_recipe_name = nil
  remove_missing_materials_section(player)
end

local function get_sorted_recipe_names(recipes)
  if recipes.sorted_names then
    return recipes.sorted_names
  end

  local recipe_names = {}
  for recipe_name in pairs(recipes) do
    if recipe_name ~= "sorted_names" then
      recipe_names[#recipe_names + 1] = recipe_name
    end
  end

  table.sort(recipe_names)
  recipes.sorted_names = recipe_names
  return recipe_names
end

local function add_recipe_pick_stat(stats, name, count)
  if not stats then
    return
  end

  stats[name] = (stats[name] or 0) + (count or 1)
end

local function get_recipe_pick_stats_details(stats, item_request_count, recipe_found)
  return {
    cached_pick_attempts = stats.cached_pick_attempts or 0,
    cached_pick_hits = stats.cached_pick_hits or 0,
    cached_pick_misses = stats.cached_pick_misses or 0,
    get_craftable_count_calls = stats.get_craftable_count_calls or 0,
    item_checks = stats.item_checks or 0,
    item_requests = item_request_count,
    recipe_checks = stats.recipe_checks or 0,
    recipe_found = recipe_found and 1 or 0,
  }
end

local function is_recipe_craftable(player, recipe_name, quality_name, stats)
  if not is_default_quality(quality_name) then
    return false
  end

  add_recipe_pick_stat(stats, "get_craftable_count_calls")
  return player.get_craftable_count(recipe_name) > 0
end

local function recipe_has_only_item_inputs_and_outputs(recipe)
  for _, ingredient in pairs(recipe.ingredients) do
    if ingredient.type ~= "item" then
      return false
    end
  end

  for _, product in pairs(recipe.products) do
    if product.type ~= "item" then
      return false
    end
  end

  return true
end

local function recipe_matches_player_hand_crafting_category(player, recipe)
  local character = player.character
  local character_prototype = character and character.prototype
  local crafting_categories = character_prototype and character_prototype.crafting_categories
  if not crafting_categories then
    return false
  end

  for category in pairs(crafting_categories) do
    if recipe.has_category(category) then
      return true
    end
  end

  return false
end

local function is_recipe_hand_craftable_without_materials(player, recipe)
  if not recipe or recipe.hidden or not recipe.enabled then
    return false
  end

  if recipe.prototype and recipe.prototype.hidden_from_player_crafting then
    return false
  end

  if player.force.get_hand_crafting_disabled_for_recipe(recipe.name) then
    return false
  end

  if not recipe_has_only_item_inputs_and_outputs(recipe) then
    return false
  end

  return recipe_matches_player_hand_crafting_category(player, recipe)
end

local function recipe_for_item(player, item_name, quality_name, stats)
  add_recipe_pick_stat(stats, "item_checks")
  local recipes = storage.recipes and storage.recipes[item_name]
  if not recipes then
    return nil
  end

  for _, recipe_name in ipairs(get_sorted_recipe_names(recipes)) do
    add_recipe_pick_stat(stats, "recipe_checks")
    local recipe = player.force.recipes[recipe_name]
    local can_craft = recipe and not recipe.hidden and recipe.enabled
      and is_recipe_craftable(player, recipe_name, quality_name, stats)

    if can_craft then
      return recipe_name
    end
  end

  return nil
end

local function recipe_for_hand_craftable_item(player, item_name, quality_name)
  if not is_default_quality(quality_name) then
    return nil
  end

  local recipes = storage.recipes and storage.recipes[item_name]
  if not recipes then
    return nil
  end

  for _, recipe_name in ipairs(get_sorted_recipe_names(recipes)) do
    local recipe = player.force.recipes[recipe_name]
    if is_recipe_hand_craftable_without_materials(player, recipe) then
      return recipe_name
    end
  end

  return nil
end

local function get_crafting_queue_item_counts(player)
  local crafting_queue = player.crafting_queue
  if not crafting_queue then
    return {}
  end

  local data = storage.data and storage.data[player.index]
  local active_recipe = data and data.active_recipe_name

  local queued_counts = {}
  for _, queue_item in pairs(crafting_queue) do
    if not queue_item.prerequisite then
      local recipe_name = type(queue_item.recipe) == "string" and queue_item.recipe or queue_item.recipe.name

      -- Exclude the currently active crafting item to prevent double-counting:
      -- the queue still contains the item being crafted, but it hasn't been
      -- produced yet, so counting it as "available" inflates the supply.
      if active_recipe and recipe_name == active_recipe then
        goto continue_queue
      end

      local recipe = player.force.recipes[recipe_name]

      if recipe then
        for _, product in pairs(recipe.products) do
          if product.type == "item" then
            local recipe_quality_name = normalise_quality_name(queue_item.quality)
            local item_key = make_item_key(product.name, recipe_quality_name)
            queued_counts[item_key] = (queued_counts[item_key] or 0) + queue_item.count * (product.amount or 1)
          end
        end
      end
    end
    ::continue_queue::
  end

  return queued_counts
end

local function build_missing_material_availability_signature(player, missing_requests)
  if not missing_requests then
    return nil
  end

  local logistic_point = player.get_requester_point()
  local logistic_network = nil
  if logistic_point and logistic_point.valid then
    local candidate_network = logistic_point.logistic_network
    if candidate_network and candidate_network.valid then
      logistic_network = candidate_network
    end
  end

  local request_keys = {}
  for item_key in pairs(missing_requests) do
    request_keys[#request_keys + 1] = item_key
  end
  table.sort(request_keys)

  local parts = {}
  for _, item_key in ipairs(request_keys) do
    local request = missing_requests[item_key]
    local quality_name = normalise_quality_name(request.quality)
    local inventory_count = player.get_item_count(item_count_filter(request.name, quality_name))
    local network_count = logistic_network
      and logistic_network.get_item_count(item_with_quality_id(request.name, quality_name))
      or 0
    parts[#parts + 1] = item_key .. "=" .. tostring(inventory_count + network_count)
  end
  return table.concat(parts, ";")
end

local function get_item_requests(player, crafting_complete, completed_item_name, completed_quality_name)
  local profiler = autocraft.start_profile()
  local item_requests = {}
  local requested_items = get_requested_items(player)
  local logistic_point = player.get_requester_point()
  local logistic_network = nil
  local requested_item_count = 0
  local inventory_checks = 0
  local network_checks = 0
  if logistic_point and logistic_point.valid then
    local candidate_network = logistic_point.logistic_network
    if candidate_network and candidate_network.valid then
      logistic_network = candidate_network
    end
  end
  local queued_counts = get_crafting_queue_item_counts(player)

  -- 实际手搓缺口要同时扣掉玩家已持有、当前物流网络已有，以及已经排进手搓队列的成品数量。
  for item_key, request in pairs(requested_items) do
    requested_item_count = requested_item_count + 1
    local item_name = request.name
    local quality_name = normalise_quality_name(request.quality)
    local min = request.min
    local recently_completed_count = 0
    if (
      not crafting_complete
      and completed_item_name == item_name
      and normalise_quality_name(completed_quality_name) == quality_name
    ) then
      recently_completed_count = 1
    end

    inventory_checks = inventory_checks + 1
    local inventory_count = player.get_item_count(item_count_filter(item_name, quality_name)) + recently_completed_count
    local logistic_network_count = 0
    if logistic_network then
      network_checks = network_checks + 1
      logistic_network_count = logistic_network.get_item_count(item_with_quality_id(item_name, quality_name))
    end
    local queued_count = queued_counts[item_key] or 0
    local available = inventory_count + logistic_network_count + queued_count
    local missing = min - available

    if missing > 0 then
      item_requests[#item_requests + 1] = {
        name = item_name,
        quality = quality_name,
        min = min,
        available = available,
        missing = missing,
        ratio = available / min,
      }
    end
  end

  autocraft.record_profile("get_item_requests", profiler, {
    inventory_checks = inventory_checks,
    item_requests = #item_requests,
    network_checks = network_checks,
    requested_items = requested_item_count,
  })
  return item_requests
end

local item_id_to_name

local function get_hand_item_name(player)
  if player.cursor_stack and player.cursor_stack.valid_for_read then
    return player.cursor_stack.name
  end

  if player.cursor_ghost then
    return item_id_to_name(player.cursor_ghost.name)
  end

  return nil
end

local function get_hand_quality_name(player)
  if player.cursor_stack and player.cursor_stack.valid_for_read then
    return normalise_quality_name(player.cursor_stack.quality)
  end

  if player.cursor_ghost and player.cursor_ghost.quality then
    return normalise_quality_name(player.cursor_ghost.quality)
  end

  return DEFAULT_QUALITY_NAME
end

item_id_to_name = function(item)
  if type(item) == "string" then
    return item
  end

  return item.name
end

local function get_recipe_output_amount(recipe, item_name)
  for _, product in pairs(recipe.products) do
    if product.type == "item" and product.name == item_name then
      if product.amount then
        return product.amount
      end
      if product.amount_min then
        return product.amount_min
      end
    end
  end

  return 1
end

local function get_craft_count(player, recipe_name, recipe, item_request)
  local crafts_needed = math.ceil(item_request.missing / get_recipe_output_amount(recipe, item_request.name))
  if not is_default_quality(item_request.quality) then
    return 0
  end

  local craftable_count = player.get_craftable_count(recipe_name)

  return math.min(crafts_needed, craftable_count, AUTOCRAFT_MAX_CRAFT_BATCH_SIZE)
end

local function snapshot_backpack(player)
  local inv = player.get_main_inventory()
  if not inv or not inv.valid then return {} end
  local bp = {}
  for _, item in pairs(inv.get_contents()) do
    local key = make_item_key(item.name, normalise_quality_name(item.quality))
    bp[key] = (bp[key] or 0) + item.count
  end
  return bp
end

local function resolve_ingredient(player, item_name, quality_name, need_count, requests, backpack, visiting)
  if need_count <= 0 then return end
  quality_name = normalise_quality_name(quality_name)
  local item_key = make_item_key(item_name, quality_name)

  if visiting[item_key] then return end
  visiting[item_key] = true

  -- Deduct backpack: use what you have, request only the deficit
  local in_bp = backpack[item_key] or 0
  local consumed = math.min(in_bp, need_count)
  backpack[item_key] = in_bp - consumed
  local deficit = need_count - consumed
  if deficit <= 0 then visiting[item_key] = nil; return end

  -- Can hand-craft? → recurse into sub-ingredients (degrade)
  local hand_recipe_name = recipe_for_hand_craftable_item(player, item_name, quality_name)
  if not hand_recipe_name then
    -- Cannot hand-craft → request directly
    local ex = requests[item_key]
    if ex then ex.count = ex.count + deficit
    else requests[item_key] = { name = item_name, quality = quality_name, count = deficit } end
    visiting[item_key] = nil
    return
  end

  local hand_recipe = player.force.recipes[hand_recipe_name]
  if not hand_recipe then
    local ex = requests[item_key]
    if ex then ex.count = ex.count + deficit
    else requests[item_key] = { name = item_name, quality = quality_name, count = deficit } end
    visiting[item_key] = nil
    return
  end

  local output_per_craft = get_recipe_output_amount(hand_recipe, item_name)
  local crafts_needed = math.min(AUTOCRAFT_MAX_MISSING_CRAFTS, math.ceil(deficit / output_per_craft))

  for _, ingredient in pairs(hand_recipe.ingredients) do
    if ingredient.type == "item" then
      resolve_ingredient(player, ingredient.name, quality_name, ingredient.amount * crafts_needed, requests, backpack, visiting)
    end
  end

  visiting[item_key] = nil
end

local function update_missing_materials_section(player, target_item_name, target_quality_name, target_recipe_name, target_missing_count)
  local profiler = autocraft.start_profile()
  if not autocraft.is_enabled(player) then
    remove_missing_materials_section(player)
    autocraft.record_profile("update_missing_materials_section", profiler, { disabled = 1 })
    return nil
  end

  storage.missing_section_cooldowns = storage.missing_section_cooldowns or {}
  if game.tick < (storage.missing_section_cooldowns[player.index] or 0) then
    autocraft.record_profile("update_missing_materials_section", profiler, { cooldown = 1 })
    return nil
  end

  if not target_item_name or not target_recipe_name or not target_missing_count or target_missing_count <= 0 then
    remove_missing_materials_section(player)
    autocraft.record_profile("update_missing_materials_section", profiler, { no_target = 1 })
    return nil
  end

  local recipe = player.force.recipes[target_recipe_name]
  if not recipe then
    remove_missing_materials_section(player)
    autocraft.record_profile("update_missing_materials_section", profiler, { no_recipe = 1 })
    return nil
  end

  target_quality_name = normalise_quality_name(target_quality_name)
  local crafts_needed = math.min(AUTOCRAFT_MAX_MISSING_CRAFTS, math.ceil(target_missing_count / get_recipe_output_amount(recipe, target_item_name)))
  local requests = {}
  local backpack = snapshot_backpack(player)
  local visiting = {}

  for _, ingredient in pairs(recipe.ingredients) do
    if ingredient.type == "item" then
      resolve_ingredient(player, ingredient.name, target_quality_name, ingredient.amount * crafts_needed, requests, backpack, visiting)
    end
  end

  if next(requests) == nil then
    remove_missing_materials_section(player)
    autocraft.record_profile("update_missing_materials_section", profiler, { no_missing_materials = 1 })
    return nil
  end

  write_missing_materials_section(player, requests)
  storage.missing_section_cooldowns[player.index] = game.tick + AUTOCRAFT_MISSING_SECTION_COOLDOWN_TICKS
  autocraft.record_profile("update_missing_materials_section", profiler, { missing_request_kinds = table_size(requests) })
  return requests
end

local function sort_item_requests(item_requests)
  table.sort(item_requests, function(a, b)
    if a.ratio ~= b.ratio then
      return a.ratio < b.ratio
    end

    if a.missing ~= b.missing then
      return a.missing > b.missing
    end

    return a.name < b.name
  end)
end

local function find_item_request(item_requests, item_name, quality_name)
  if not item_name then
    return nil
  end

  quality_name = normalise_quality_name(quality_name)
  for _, item_request in ipairs(item_requests) do
    if item_request.name == item_name and normalise_quality_name(item_request.quality) == quality_name then
      return item_request
    end
  end

  return nil
end

local function pick_recipe_for_item_request(player, item_requests, item_name, quality_name, recipe_picker, stats)
  local item_request = find_item_request(item_requests, item_name, quality_name)
  if not item_request then
    return nil
  end

  local recipe_name = recipe_picker(player, item_name, item_request.quality, stats)
  if recipe_name then
    return item_request, recipe_name
  end

  return nil
end

local function pick_cached_recipe_from_requests(player, item_requests, cached_item_name, cached_quality_name, cached_recipe_name, stats)
  if not cached_item_name or not cached_quality_name or not cached_recipe_name then
    return nil
  end

  add_recipe_pick_stat(stats, "cached_pick_attempts")
  local item_request = find_item_request(item_requests, cached_item_name, cached_quality_name)
  if not item_request then
    add_recipe_pick_stat(stats, "cached_pick_misses")
    return nil
  end

  local recipes = storage.recipes and storage.recipes[cached_item_name]
  if not recipes or not recipes[cached_recipe_name] then
    add_recipe_pick_stat(stats, "cached_pick_misses")
    return nil
  end

  add_recipe_pick_stat(stats, "item_checks")
  add_recipe_pick_stat(stats, "recipe_checks")
  local recipe = player.force.recipes[cached_recipe_name]
  local can_craft = recipe and not recipe.hidden and recipe.enabled
    and is_recipe_craftable(player, cached_recipe_name, cached_quality_name, stats)
  if can_craft then
    add_recipe_pick_stat(stats, "cached_pick_hits")
    return item_request, cached_recipe_name
  end

  add_recipe_pick_stat(stats, "cached_pick_misses")
  return nil
end

local function pick_recipe_from_requests(player, item_requests, recipe_picker, stats)
  for _, item_request in ipairs(item_requests) do
    local recipe_name = recipe_picker(player, item_request.name, item_request.quality, stats)
    if recipe_name then
      return item_request, recipe_name
    end
  end

  return nil
end

function autocraft.do_crafting(player, crafting_complete, completed_item_name, completed_quality_name)
  local profiler = autocraft.start_profile()
  if crafting_complete == nil then
    crafting_complete = true
  end

  if not autocraft.is_enabled(player) then
    autocraft.record_profile("do_crafting", profiler, { disabled = 1 })
    return
  end

  local is_eligible = player.connected
    and player.controller_type == defines.controllers.character
    and player.ticks_to_respawn == nil

  if not is_eligible then
    remove_missing_materials_section(player)
    autocraft.record_profile("do_crafting", profiler, { ineligible = 1 })
    return
  end

  local allowed_queue_length = crafting_complete and 0 or 1
  if player.crafting_queue and #player.crafting_queue > allowed_queue_length then
    autocraft.record_profile("do_crafting", profiler, {
      queue_busy = 1,
      queue_length = #player.crafting_queue,
    })
    return
  end

  local data = get_player_data(player)
  if (
    crafting_complete
    and data.next_no_craftable_retry_tick
    and game.tick < data.next_no_craftable_retry_tick
  ) then
    autocraft.record_profile("do_crafting", profiler, {
      no_craftable_retry_wait = 1,
      retry_ticks_remaining = data.next_no_craftable_retry_tick - game.tick,
    })
    return
  end
  data.next_no_craftable_retry_tick = nil

  if (
    crafting_complete
    and data.last_no_craftable_missing_requests
    and data.last_no_craftable_material_signature
  ) then
    local material_signature =
      build_missing_material_availability_signature(player, data.last_no_craftable_missing_requests)
    if material_signature == data.last_no_craftable_material_signature then
      data.next_no_craftable_retry_tick = game.tick + AUTOCRAFT_NO_CRAFTABLE_RETRY_TICKS
      autocraft.record_profile("do_crafting", profiler, {
        no_craftable_materials_unchanged = 1,
      })
      return
    end
  end

  local completed_quality_name = normalise_quality_name(completed_quality_name)
  local get_requests_profiler = autocraft.start_profile()
  local item_requests = get_item_requests(player, crafting_complete, completed_item_name, completed_quality_name)
  autocraft.record_profile("do_crafting.get_item_requests", get_requests_profiler, {
    item_requests = #item_requests,
  })
  if #item_requests == 0 then
    clear_no_craftable_state(data)
    remove_missing_materials_section(player)
    autocraft.record_profile("do_crafting", profiler, { no_item_requests = 1 })
    return
  end

  local sort_profiler = autocraft.start_profile()
  sort_item_requests(item_requests)
  autocraft.record_profile("do_crafting.sort_item_requests", sort_profiler, {
    item_requests = #item_requests,
  })

  local hand_item_name = get_hand_item_name(player)
  local hand_quality_name = get_hand_quality_name(player)
  local pick_profiler = autocraft.start_profile()
  local recipe_pick_stats = {}
  local item_request, recipe_name =
    pick_recipe_for_item_request(player, item_requests, hand_item_name, hand_quality_name, recipe_for_item, recipe_pick_stats)
  if (
    not recipe_name
    and not crafting_complete
    and completed_item_name == data.last_craftable_item_name
    and completed_quality_name == data.last_craftable_quality_name
  ) then
    item_request, recipe_name = pick_cached_recipe_from_requests(
      player,
      item_requests,
      data.last_craftable_item_name,
      data.last_craftable_quality_name,
      data.last_craftable_recipe_name,
      recipe_pick_stats
    )
  end
  if not recipe_name then
    item_request, recipe_name = pick_recipe_from_requests(player, item_requests, recipe_for_item, recipe_pick_stats)
  end
  autocraft.record_profile(
    "do_crafting.pick_craftable_recipe",
    pick_profiler,
    get_recipe_pick_stats_details(recipe_pick_stats, #item_requests, recipe_name)
  )
  local item_name = item_request and item_request.name or nil
  local quality_name = item_request and normalise_quality_name(item_request.quality) or DEFAULT_QUALITY_NAME
  if recipe_name then
    data.next_no_craftable_retry_tick = nil
    data.last_no_craftable_missing_requests = nil
    data.last_no_craftable_material_signature = nil
    local recipe = player.force.recipes[recipe_name]
    local craft_count = recipe and get_craft_count(player, recipe_name, recipe, item_request) or 0
    if craft_count <= 0 then
      autocraft.record_profile("do_crafting", profiler, {
        craft_count_zero = 1,
        item_requests = #item_requests,
      })
      return
    end

    data.active_item_name = item_name
    data.active_quality_name = quality_name
    data.active_queue_index = player.crafting_queue and (#player.crafting_queue + 1) or 1
    data.active_recipe_name = recipe_name
    data.last_craftable_item_name = item_name
    data.last_craftable_quality_name = quality_name
    data.last_craftable_recipe_name = recipe_name
    storage.data[player.index] = data
    local begin_crafting_profiler = autocraft.start_profile()
    player.begin_crafting({ count = craft_count, recipe = recipe_name, silent = true })
    autocraft.record_profile("do_crafting.begin_crafting_api", begin_crafting_profiler, {
      begin_crafting = craft_count,
    })
    autocraft.record_profile("do_crafting", profiler, {
      begin_crafting = craft_count,
      item_requests = #item_requests,
    })
    return
  end

  local target_item_request, target_recipe_name =
    pick_recipe_for_item_request(player, item_requests, hand_item_name, hand_quality_name, recipe_for_hand_craftable_item)
  if not target_recipe_name then
    target_item_request, target_recipe_name =
      pick_recipe_from_requests(player, item_requests, recipe_for_hand_craftable_item)
  end
  data.next_no_craftable_retry_tick = game.tick + AUTOCRAFT_NO_CRAFTABLE_RETRY_TICKS
  local target_item_name = target_item_request and target_item_request.name or nil
  local target_quality_name = target_item_request and target_item_request.quality or DEFAULT_QUALITY_NAME
  local missing_requests = update_missing_materials_section(
    player,
    target_item_name,
    target_quality_name,
    target_recipe_name,
    target_item_request and target_item_request.missing or nil
  )
  data.last_no_craftable_missing_requests = missing_requests
  data.last_no_craftable_material_signature = build_missing_material_availability_signature(
    player,
    missing_requests
  )

  autocraft.record_profile("do_crafting", profiler, {
    begin_crafting = 0,
    item_requests = #item_requests,
  })
end

function autocraft.keep_missing_materials_section_enabled(player)
  local profiler = autocraft.start_profile()
  local section = find_missing_materials_section(player)
  if section and section.valid then
    section.active = true
  end
  autocraft.record_profile("keep_missing_materials_section_enabled", profiler, {
    found = section and section.valid and 1 or 0,
  })
end

return autocraft
