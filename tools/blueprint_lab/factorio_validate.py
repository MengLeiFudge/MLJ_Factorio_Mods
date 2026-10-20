from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


DEFAULT_FACTORIO_EXE = Path("/mnt/d/Steam/steamapps/common/Factorio/bin/x64/factorio.exe")
DEFAULT_USER_DATA = Path("/mnt/c/Users/MLJ/AppData/Roaming/Factorio")
DEFAULT_SCENARIO_NAME = "blueprint_lab_validation"
POWERSHELL_EXE = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
TASKKILL_EXE = Path("/mnt/c/Windows/System32/taskkill.exe")


def lua_long_string(value: str) -> str:
    if "]]" in value:
        raise ValueError("blueprint string cannot be embedded as a Lua long string")
    return f"[[{value}]]"


def render_control_lua(
    blueprint_string: str,
    *,
    input_probe: str = "both",
    runtime_audit_wait_ticks: int = 120,
    sustained_input_interval_ticks: int = 0,
    throughput_window_ticks: int = 0,
    throughput_target_item: str = "",
    throughput_probe_x: float | None = None,
) -> str:
    return f"""local blueprint_string = {lua_long_string(blueprint_string)}
local input_probe_mode = {lua_long_string(input_probe)}
local runtime_audit_wait_ticks = {int(runtime_audit_wait_ticks)}
local sustained_input_interval_ticks = {int(sustained_input_interval_ticks)}
local throughput_window_ticks = {int(throughput_window_ticks)}
local throughput_target_item_name = {lua_long_string(throughput_target_item)}
local throughput_probe_x = {str(float(throughput_probe_x)) if throughput_probe_x is not None else "nil"}
local next_sustained_input_tick = nil
local sustained_input_cycles = 0
local sustained_input_inserted = 0
local sustained_input_failures = 0
local next_throughput_window_tick = nil
local throughput_start_tick = nil
local throughput_last_tick = nil
local throughput_window_count = 0
local throughput_removed_product_items = {{}}
local throughput_removed_input_items = {{}}
local throughput_removed_target_items = 0
local throughput_removed_target_by_line = {{}}
local throughput_current_product_items = {{}}
local throughput_current_input_items = {{}}
local throughput_current_target_items = 0
local throughput_current_target_by_line = {{}}
local throughput_current_start_tick = nil
local throughput_current_max_x = nil
local throughput_current_probe_x = nil
local throughput_current_belt_count = 0
local throughput_current_line_count = 0

local function validation_fail(message)
  log("BLUEPRINT_LAB_VALIDATION fail " .. tostring(message))
  error(message, 0)
end

local function sorted_count_string(counts)
  local keys = {{}}
  for key, _ in pairs(counts) do
    keys[#keys + 1] = key
  end
  table.sort(keys)
  local parts = {{}}
  for _, key in pairs(keys) do
    parts[#parts + 1] = tostring(key) .. ":" .. tostring(counts[key])
  end
  return table.concat(parts, ",")
end

local function add_count(counts, key)
  local count_key = tostring(key)
  counts[count_key] = (counts[count_key] or 0) + 1
end

local function add_amount(counts, key, amount)
  local count_key = tostring(key)
  counts[count_key] = (counts[count_key] or 0) + (amount or 0)
end

local function throughput_line_key(entity, line_index)
  return "x=" .. tostring(entity.position.x) .. "/y=" .. tostring(entity.position.y) .. "/line=" .. tostring(line_index)
end

local function entity_status_name(status)
  if status == nil then
    return "nil"
  end
  for name, value in pairs(defines.entity_status) do
    if value == status then
      return name
    end
  end
  return tostring(status)
end

local collect_recipe_input_items
local collect_recipe_product_items
local sorted_keys

local function audit_recipe_machines(surface)
  local recipe_machine_count = 0
  local recipe_counts = {{}}
  local status_counts = {{}}
  local crafting_speed_positive = 0
  local products_finished_total = 0
  local electric_connected_count = 0
  local electric_checked_count = 0
  local module_item_total = 0
  local output_item_total = 0
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_recipe, recipe = pcall(function()
      return entity.get_recipe()
    end)
    if ok_recipe and recipe ~= nil then
      recipe_machine_count = recipe_machine_count + 1
      add_count(recipe_counts, recipe.name)
      add_count(status_counts, entity_status_name(entity.status))
      if entity.crafting_speed ~= nil and entity.crafting_speed > 0 then
        crafting_speed_positive = crafting_speed_positive + 1
      end
      if entity.products_finished ~= nil then
        products_finished_total = products_finished_total + entity.products_finished
      end
      local ok_connected, connected = pcall(function()
        return entity.is_connected_to_electric_network()
      end)
      if ok_connected then
        electric_checked_count = electric_checked_count + 1
        if connected then
          electric_connected_count = electric_connected_count + 1
        end
      end
      local module_inventory = entity.get_module_inventory()
      if module_inventory ~= nil then
        module_item_total = module_item_total + module_inventory.get_item_count()
      end
      local output_inventory = entity.get_output_inventory()
      if output_inventory ~= nil then
        output_item_total = output_item_total + output_inventory.get_item_count()
      end
    end
  end
  log("BLUEPRINT_LAB_VALIDATION recipe_machine_audit machines=" .. tostring(recipe_machine_count) .. " recipes=" .. sorted_count_string(recipe_counts) .. " status=" .. sorted_count_string(status_counts))
  log("BLUEPRINT_LAB_VALIDATION recipe_machine_runtime crafting_speed_positive=" .. tostring(crafting_speed_positive) .. " electric_connected=" .. tostring(electric_connected_count) .. "/" .. tostring(electric_checked_count) .. " products_finished=" .. tostring(products_finished_total) .. " module_items=" .. tostring(module_item_total) .. " output_items=" .. tostring(output_item_total))
end

local function audit_recipe_machine_output_items(surface)
  local output_counts = {{}}
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_recipe, recipe = pcall(function()
      return entity.get_recipe()
    end)
    if ok_recipe and recipe ~= nil then
      local output_inventory = entity.get_output_inventory()
      if output_inventory ~= nil then
        for _, product in pairs(recipe.products or {{}}) do
          if product.name ~= nil and product.type ~= "fluid" then
            local count = output_inventory.get_item_count(product.name)
            if count ~= nil and count > 0 then
              output_counts[product.name] = (output_counts[product.name] or 0) + count
            end
          end
        end
      end
    end
  end
  log("BLUEPRINT_LAB_VALIDATION recipe_machine_output_items items=" .. sorted_count_string(output_counts))
end

local function recipe_machine_key(entity)
  if entity == nil then
    return nil
  end
  return entity.unit_number or tostring(entity.position.x) .. ":" .. tostring(entity.position.y)
end

local function recipe_machines_by_key(surface)
  local recipe_machines = {{}}
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_recipe, recipe = pcall(function()
      return entity.get_recipe()
    end)
    if ok_recipe and recipe ~= nil then
      recipe_machines[recipe_machine_key(entity)] = entity
    end
  end
  return recipe_machines
end

local function recipe_machine_at_position(surface, recipe_machines, position)
  if position == nil then
    return nil
  end
  local entities = surface.find_entities_filtered{{
    force = "player",
    area = {{
      {{position.x - 0.1, position.y - 0.1}},
      {{position.x + 0.1, position.y + 0.1}},
    }},
  }}
  for _, entity in pairs(entities) do
    if recipe_machines[recipe_machine_key(entity)] ~= nil then
      return entity
    end
  end
  return nil
end

local function nearest_recipe_machine_to_position(recipe_machines, position)
  if position == nil then
    return nil, nil
  end
  local best_machine = nil
  local best_distance = nil
  for _, machine in pairs(recipe_machines) do
    local distance = math.abs(machine.position.x - position.x) + math.abs(machine.position.y - position.y)
    if best_distance == nil or distance < best_distance then
      best_machine = machine
      best_distance = distance
    end
  end
  return best_machine, best_distance
end

local function transport_line_entity_at_position(surface, position)
  if position == nil then
    return nil
  end
  local entities = surface.find_entities_filtered{{
    force = "player",
    area = {{
      {{position.x - 0.1, position.y - 0.1}},
      {{position.x + 0.1, position.y + 0.1}},
    }},
  }}
  for _, entity in pairs(entities) do
    local ok_line_count, max_line_index = pcall(function()
      return entity.get_max_transport_line_index()
    end)
    if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
      return entity
    end
  end
  return nil
end

local function audit_output_unloading(surface)
  local recipe_machines = recipe_machines_by_key(surface)
  local input_items = collect_recipe_input_items(surface)
  local product_items = collect_recipe_product_items(surface)
  local machine_count = 0
  local machine_output_items = {{}}
  local machine_samples = {{}}
  for _, machine in pairs(recipe_machines) do
    machine_count = machine_count + 1
    local output_inventory = machine.get_output_inventory()
    if output_inventory ~= nil then
      local sample_parts = {{}}
      for _, item_name in pairs(sorted_keys(product_items)) do
        local count = output_inventory.get_item_count(item_name)
        if count ~= nil and count > 0 then
          add_amount(machine_output_items, item_name, count)
          sample_parts[#sample_parts + 1] = item_name .. ":" .. tostring(count)
        end
      end
      if #machine_samples < 8 and #sample_parts > 0 then
        machine_samples[#machine_samples + 1] = tostring(machine.name) .. "@x" .. tostring(machine.position.x) .. "y" .. tostring(machine.position.y) .. "=" .. table.concat(sample_parts, ",")
      end
    end
  end

  local output_inserter_count = 0
  local direct_pickup_count = 0
  local geometry_pickup_count = 0
  local direct_drop_count = 0
  local geometry_drop_count = 0
  local drop_belt_keys = {{}}
  local drop_belt_count = 0
  local drop_line_count = 0
  local held_items = {{}}
  local drop_line_product_items = {{}}
  local drop_line_input_items = {{}}
  local inserter_samples = {{}}
  local invalid_output_inserter_count = 0
  local invalid_output_inserter_samples = {{}}
  for _, inserter in pairs(surface.find_entities_filtered{{force = "player"}}) do
    if inserter.type ~= "inserter" then
      goto continue_output_inserter
    end
    local ok_pickup_target, pickup_target = pcall(function()
      return inserter.pickup_target
    end)
    if not ok_pickup_target then
      pickup_target = nil
    end
    local used_geometry_pickup = false
    if pickup_target == nil then
      used_geometry_pickup = true
      pickup_target = recipe_machine_at_position(surface, recipe_machines, inserter.pickup_position)
    end
    if pickup_target == nil or recipe_machines[recipe_machine_key(pickup_target)] == nil then
      local ok_drop_target, rejected_drop_target = pcall(function()
        return inserter.drop_target
      end)
      if not ok_drop_target then
        rejected_drop_target = nil
      end
      if rejected_drop_target == nil then
        rejected_drop_target = transport_line_entity_at_position(surface, inserter.drop_position)
      end
      local nearest_machine, nearest_distance = nearest_recipe_machine_to_position(recipe_machines, inserter.pickup_position)
      if rejected_drop_target ~= nil and nearest_machine ~= nil and nearest_distance ~= nil and nearest_distance <= 3.0 then
        local ok_line_count, max_line_index = pcall(function()
          return rejected_drop_target.get_max_transport_line_index()
        end)
        if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
          invalid_output_inserter_count = invalid_output_inserter_count + 1
          if #invalid_output_inserter_samples < 8 then
            local pickup_target_name = "nil"
            if pickup_target ~= nil and pickup_target.name ~= nil then
              pickup_target_name = tostring(pickup_target.name)
            end
            invalid_output_inserter_samples[#invalid_output_inserter_samples + 1] =
              tostring(inserter.name)
              .. "@x" .. tostring(inserter.position.x) .. "y" .. tostring(inserter.position.y)
              .. " pickup=x" .. tostring(inserter.pickup_position.x) .. "y" .. tostring(inserter.pickup_position.y)
              .. " pickup_target=" .. pickup_target_name
              .. " nearest=" .. tostring(nearest_machine.name) .. "@x" .. tostring(nearest_machine.position.x) .. "y" .. tostring(nearest_machine.position.y)
              .. " distance=" .. tostring(nearest_distance)
              .. " drop=" .. tostring(rejected_drop_target.name) .. "@x" .. tostring(rejected_drop_target.position.x) .. "y" .. tostring(rejected_drop_target.position.y)
          end
        end
      end
      goto continue_output_inserter
    end
    output_inserter_count = output_inserter_count + 1
    if used_geometry_pickup then
      geometry_pickup_count = geometry_pickup_count + 1
    else
      direct_pickup_count = direct_pickup_count + 1
    end

    local ok_held_stack, held_stack = pcall(function()
      return inserter.held_stack
    end)
    if ok_held_stack and held_stack ~= nil and held_stack.valid_for_read then
      add_amount(held_items, held_stack.name, held_stack.count)
    end

    local ok_drop_target, drop_target = pcall(function()
      return inserter.drop_target
    end)
    if not ok_drop_target then
      drop_target = nil
    end
    local used_geometry_drop = false
    if drop_target == nil then
      used_geometry_drop = true
      drop_target = transport_line_entity_at_position(surface, inserter.drop_position)
    end
    if drop_target ~= nil then
      local ok_line_count, max_line_index = pcall(function()
        return drop_target.get_max_transport_line_index()
      end)
      if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
        if used_geometry_drop then
          geometry_drop_count = geometry_drop_count + 1
        else
          direct_drop_count = direct_drop_count + 1
        end
        local belt_key = drop_target.unit_number or tostring(drop_target.position.x) .. ":" .. tostring(drop_target.position.y)
        if not drop_belt_keys[belt_key] then
          drop_belt_keys[belt_key] = true
          drop_belt_count = drop_belt_count + 1
        end
        for line_index = 1, max_line_index do
          local line = drop_target.get_transport_line(line_index)
          if line ~= nil then
            drop_line_count = drop_line_count + 1
            for item_name, _ in pairs(product_items) do
              local ok_count, item_count = pcall(function()
                return line.get_item_count(item_name)
              end)
              if ok_count and item_count ~= nil and item_count > 0 then
                add_amount(drop_line_product_items, item_name, item_count)
              end
            end
            for item_name, _ in pairs(input_items) do
              local ok_count, item_count = pcall(function()
                return line.get_item_count(item_name)
              end)
              if ok_count and item_count ~= nil and item_count > 0 then
                add_amount(drop_line_input_items, item_name, item_count)
              end
            end
          end
        end
        if #inserter_samples < 8 then
          inserter_samples[#inserter_samples + 1] = tostring(inserter.name) .. "@x" .. tostring(inserter.position.x) .. "y" .. tostring(inserter.position.y) .. "->" .. tostring(drop_target.name) .. "@x" .. tostring(drop_target.position.x) .. "y" .. tostring(drop_target.position.y)
        end
      end
    end
    ::continue_output_inserter::
  end

  log("BLUEPRINT_LAB_VALIDATION output_unload_audit machines=" .. tostring(machine_count) .. " machine_output_items=" .. sorted_count_string(machine_output_items) .. " output_inserters=" .. tostring(output_inserter_count) .. " pickup_targets=" .. tostring(direct_pickup_count) .. " geometry_pickups=" .. tostring(geometry_pickup_count) .. " drop_targets=" .. tostring(direct_drop_count) .. " geometry_drops=" .. tostring(geometry_drop_count) .. " drop_belts=" .. tostring(drop_belt_count) .. " drop_lines=" .. tostring(drop_line_count) .. " held_items=" .. sorted_count_string(held_items) .. " drop_line_product_items=" .. sorted_count_string(drop_line_product_items) .. " drop_line_input_items=" .. sorted_count_string(drop_line_input_items))
  log("BLUEPRINT_LAB_VALIDATION output_unload_samples machines=" .. table.concat(machine_samples, "|") .. " inserters=" .. table.concat(inserter_samples, "|"))
  log("BLUEPRINT_LAB_VALIDATION invalid_output_inserters=" .. tostring(invalid_output_inserter_count) .. " samples=" .. table.concat(invalid_output_inserter_samples, "|"))
end

collect_recipe_input_items = function(surface)
  local input_items = {{}}
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_recipe, recipe = pcall(function()
      return entity.get_recipe()
    end)
    if ok_recipe and recipe ~= nil then
      for _, ingredient in pairs(recipe.ingredients or {{}}) do
        if ingredient.name ~= nil and ingredient.type ~= "fluid" then
          input_items[ingredient.name] = true
        end
      end
    end
  end
  return input_items
end

collect_recipe_product_items = function(surface)
  local product_items = {{}}
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_recipe, recipe = pcall(function()
      return entity.get_recipe()
    end)
    if ok_recipe and recipe ~= nil then
      for _, product in pairs(recipe.products or {{}}) do
        if product.name ~= nil and product.type ~= "fluid" then
          product_items[product.name] = true
        end
      end
    end
  end
  return product_items
end

sorted_keys = function(values)
  local keys = {{}}
  for key, _ in pairs(values) do
    keys[#keys + 1] = key
  end
  table.sort(keys)
  return keys
end

local function audit_transport_items(surface)
  local input_items = collect_recipe_input_items(surface)
  local product_items = collect_recipe_product_items(surface)
  local item_counts = {{}}
  local belt_count = 0
  local line_count = 0
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_line_count, max_line_index = pcall(function()
      return entity.get_max_transport_line_index()
    end)
    if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
      belt_count = belt_count + 1
      for line_index = 1, max_line_index do
        local line = entity.get_transport_line(line_index)
        if line ~= nil then
          line_count = line_count + 1
          for item_name, _ in pairs(input_items) do
            local ok_count, item_count = pcall(function()
              return line.get_item_count(item_name)
            end)
            if ok_count and item_count ~= nil and item_count > 0 then
              item_counts[item_name] = (item_counts[item_name] or 0) + item_count
            end
          end
          for item_name, _ in pairs(product_items) do
            local ok_count, item_count = pcall(function()
              return line.get_item_count(item_name)
            end)
            if ok_count and item_count ~= nil and item_count > 0 then
              item_counts[item_name] = (item_counts[item_name] or 0) + item_count
            end
          end
        end
      end
    end
  end
  log("BLUEPRINT_LAB_VALIDATION transport_item_audit belts=" .. tostring(belt_count) .. " lines=" .. tostring(line_count) .. " items=" .. sorted_count_string(item_counts))
end

local function audit_transport_item_extents(surface)
  local tracked_items = {{}}
  for item_name, _ in pairs(collect_recipe_input_items(surface)) do
    tracked_items[item_name] = true
  end
  for item_name, _ in pairs(collect_recipe_product_items(surface)) do
    tracked_items[item_name] = true
  end

  local extents = {{}}
  local y_counts = {{}}
  local samples = {{}}
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_line_count, max_line_index = pcall(function()
      return entity.get_max_transport_line_index()
    end)
    if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
      for line_index = 1, max_line_index do
        local line = entity.get_transport_line(line_index)
        if line ~= nil then
          for item_name, _ in pairs(tracked_items) do
            local ok_count, item_count = pcall(function()
              return line.get_item_count(item_name)
            end)
            if ok_count and item_count ~= nil and item_count > 0 then
              local info = extents[item_name]
              if info == nil then
                info = {{count = 0, min_x = entity.position.x, max_x = entity.position.x, min_y = entity.position.y, max_y = entity.position.y}}
                extents[item_name] = info
              end
              info.count = info.count + item_count
              if entity.position.x < info.min_x then info.min_x = entity.position.x end
              if entity.position.x > info.max_x then info.max_x = entity.position.x end
              if entity.position.y < info.min_y then info.min_y = entity.position.y end
              if entity.position.y > info.max_y then info.max_y = entity.position.y end

              local y_key = item_name .. "@" .. tostring(entity.position.y)
              y_counts[y_key] = (y_counts[y_key] or 0) + item_count
              if samples[item_name] == nil then
                samples[item_name] = {{}}
              end
              if #samples[item_name] < 6 then
                samples[item_name][#samples[item_name] + 1] = tostring(entity.name) .. "@x" .. tostring(entity.position.x) .. "y" .. tostring(entity.position.y) .. "l" .. tostring(line_index) .. ":" .. tostring(item_count)
              end
            end
          end
        end
      end
    end
  end

  local item_names = sorted_keys(extents)
  if #item_names == 0 then
    log("BLUEPRINT_LAB_VALIDATION transport_item_extents items=")
    return
  end

  local parts = {{}}
  for _, item_name in pairs(item_names) do
    local info = extents[item_name]
    parts[#parts + 1] = item_name .. ":count=" .. tostring(info.count) .. ":x=" .. tostring(info.min_x) .. ".." .. tostring(info.max_x) .. ":y=" .. tostring(info.min_y) .. ".." .. tostring(info.max_y)
  end
  log("BLUEPRINT_LAB_VALIDATION transport_item_extents items=" .. table.concat(parts, ";"))

  local y_keys = sorted_keys(y_counts)
  local y_parts = {{}}
  for _, y_key in pairs(y_keys) do
    y_parts[#y_parts + 1] = y_key .. ":" .. tostring(y_counts[y_key])
  end
  log("BLUEPRINT_LAB_VALIDATION transport_item_y_distribution items=" .. table.concat(y_parts, ","))

  local sample_parts = {{}}
  for _, item_name in pairs(item_names) do
    sample_parts[#sample_parts + 1] = item_name .. "=" .. table.concat(samples[item_name] or {{}}, "|")
  end
  log("BLUEPRINT_LAB_VALIDATION transport_item_samples items=" .. table.concat(sample_parts, ";"))
end

local function audit_right_boundary_transport_items(surface)
  local input_items = collect_recipe_input_items(surface)
  local product_items = collect_recipe_product_items(surface)
  local max_x = throughput_probe_x
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_line_count, max_line_index = pcall(function()
      return entity.get_max_transport_line_index()
    end)
    if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
      if throughput_probe_x == nil and (max_x == nil or entity.position.x > max_x) then
        max_x = entity.position.x
      end
    end
  end
  local item_counts = {{}}
  local item_samples = {{}}
  local input_item_count = 0
  local product_item_count = 0
  local belt_count = 0
  local line_count = 0
  if max_x ~= nil then
    for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
      local ok_line_count, max_line_index = pcall(function()
        return entity.get_max_transport_line_index()
      end)
      if ok_line_count and max_line_index ~= nil and max_line_index > 0 and math.abs(entity.position.x - max_x) <= 0.1 then
        belt_count = belt_count + 1
        for line_index = 1, max_line_index do
          local line = entity.get_transport_line(line_index)
          if line ~= nil then
            line_count = line_count + 1
            for item_name, _ in pairs(input_items) do
              local ok_count, item_count = pcall(function()
                return line.get_item_count(item_name)
              end)
              if ok_count and item_count ~= nil and item_count > 0 then
                item_counts[item_name] = (item_counts[item_name] or 0) + item_count
                input_item_count = input_item_count + item_count
                if item_samples[item_name] == nil then
                  item_samples[item_name] = {{}}
                end
                if #item_samples[item_name] < 8 then
                  item_samples[item_name][#item_samples[item_name] + 1] = tostring(entity.name) .. "@x" .. tostring(entity.position.x) .. "y" .. tostring(entity.position.y) .. "l" .. tostring(line_index) .. ":" .. tostring(item_count)
                end
              end
            end
            for item_name, _ in pairs(product_items) do
              local ok_count, item_count = pcall(function()
                return line.get_item_count(item_name)
              end)
              if ok_count and item_count ~= nil and item_count > 0 then
                item_counts[item_name] = (item_counts[item_name] or 0) + item_count
                product_item_count = product_item_count + item_count
                if item_samples[item_name] == nil then
                  item_samples[item_name] = {{}}
                end
                if #item_samples[item_name] < 8 then
                  item_samples[item_name][#item_samples[item_name] + 1] = tostring(entity.name) .. "@x" .. tostring(entity.position.x) .. "y" .. tostring(entity.position.y) .. "l" .. tostring(line_index) .. ":" .. tostring(item_count)
                end
              end
            end
          end
        end
      end
    end
  end
  log("BLUEPRINT_LAB_VALIDATION right_boundary_transport_item_audit max_x=" .. tostring(max_x) .. " belts=" .. tostring(belt_count) .. " lines=" .. tostring(line_count) .. " items=" .. sorted_count_string(item_counts))
  local sample_parts = {{}}
  for _, item_name in pairs(sorted_keys(item_samples)) do
    sample_parts[#sample_parts + 1] = item_name .. "=" .. table.concat(item_samples[item_name] or {{}}, "|")
  end
  log("BLUEPRINT_LAB_VALIDATION right_boundary_item_samples items=" .. table.concat(sample_parts, ";"))
  local cleanliness_status = "empty"
  if max_x == nil then
    cleanliness_status = "no-boundary"
  elseif input_item_count > 0 then
    cleanliness_status = "contaminated"
  elseif product_item_count > 0 then
    cleanliness_status = "clean"
  end
  log("BLUEPRINT_LAB_VALIDATION right_boundary_cleanliness status=" .. cleanliness_status .. " product_items=" .. tostring(product_item_count) .. " input_items=" .. tostring(input_item_count))
end

local function remove_item_from_line(line, item_name)
  local removed_total = 0
  while true do
    local ok_count, item_count = pcall(function()
      return line.get_item_count(item_name)
    end)
    if not ok_count or item_count == nil or item_count <= 0 then
      break
    end
    local ok_remove, removed_count = pcall(function()
      return line.remove_item({{name = item_name, count = item_count}})
    end)
    if not ok_remove or removed_count == nil or removed_count <= 0 then
      break
    end
    removed_total = removed_total + removed_count
    if removed_count >= item_count then
      break
    end
  end
  return removed_total
end

local function drain_right_boundary_throughput_tick(surface)
  local input_items = collect_recipe_input_items(surface)
  local product_items = collect_recipe_product_items(surface)
  local tracked_items = {{}}
  for item_name, _ in pairs(input_items) do
    tracked_items[item_name] = true
  end
  for item_name, _ in pairs(product_items) do
    tracked_items[item_name] = true
  end
  if throughput_target_item_name ~= nil and throughput_target_item_name ~= "" then
    tracked_items[throughput_target_item_name] = true
  end

  local max_x = throughput_probe_x
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_line_count, max_line_index = pcall(function()
      return entity.get_max_transport_line_index()
    end)
    if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
      if throughput_probe_x == nil and (max_x == nil or entity.position.x > max_x) then
        max_x = entity.position.x
      end
    end
  end

  local product_removed = {{}}
  local input_removed = {{}}
  local target_removed = 0
  local belt_count = 0
  local line_count = 0
  if max_x ~= nil then
    for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
      local ok_line_count, max_line_index = pcall(function()
        return entity.get_max_transport_line_index()
      end)
      if ok_line_count and max_line_index ~= nil and max_line_index > 0 and math.abs(entity.position.x - max_x) <= 0.1 then
        belt_count = belt_count + 1
        for line_index = 1, max_line_index do
          local line = entity.get_transport_line(line_index)
          if line ~= nil then
            line_count = line_count + 1
            for item_name, _ in pairs(tracked_items) do
              local removed_count = remove_item_from_line(line, item_name)
              if removed_count > 0 then
                if product_items[item_name] then
                  add_amount(product_removed, item_name, removed_count)
                  add_amount(throughput_removed_product_items, item_name, removed_count)
                  add_amount(throughput_current_product_items, item_name, removed_count)
                end
                if input_items[item_name] then
                  add_amount(input_removed, item_name, removed_count)
                  add_amount(throughput_removed_input_items, item_name, removed_count)
                  add_amount(throughput_current_input_items, item_name, removed_count)
                end
                if throughput_target_item_name ~= nil and throughput_target_item_name ~= "" and item_name == throughput_target_item_name then
                  target_removed = target_removed + removed_count
                  throughput_removed_target_items = throughput_removed_target_items + removed_count
                  throughput_current_target_items = throughput_current_target_items + removed_count
                  local line_key = throughput_line_key(entity, line_index)
                  add_amount(throughput_removed_target_by_line, line_key, removed_count)
                  add_amount(throughput_current_target_by_line, line_key, removed_count)
                end
              end
            end
          end
        end
      end
    end
  end

  throughput_current_max_x = max_x
  throughput_current_probe_x = throughput_probe_x
  throughput_current_belt_count = belt_count
  throughput_current_line_count = line_count
  throughput_last_tick = game.tick
  return {{target_removed = target_removed, product_removed = product_removed, input_removed = input_removed, max_x = max_x, belt_count = belt_count, line_count = line_count}}
end

local function log_right_boundary_throughput_window()
  throughput_window_count = throughput_window_count + 1
  throughput_last_tick = game.tick
  local observed_ticks = 0
  if throughput_current_start_tick ~= nil then
    observed_ticks = game.tick - throughput_current_start_tick
  end
  local target_per_minute = 0
  if observed_ticks > 0 then
    target_per_minute = throughput_current_target_items * 3600 / observed_ticks
  end
  log("BLUEPRINT_LAB_VALIDATION right_boundary_throughput_window index=" .. tostring(throughput_window_count) .. " tick=" .. tostring(game.tick) .. " observed_ticks=" .. tostring(observed_ticks) .. " max_x=" .. tostring(throughput_current_max_x) .. " probe_x=" .. tostring(throughput_current_probe_x) .. " belts=" .. tostring(throughput_current_belt_count) .. " lines=" .. tostring(throughput_current_line_count) .. " target_item=" .. tostring(throughput_target_item_name) .. " target_removed=" .. tostring(throughput_current_target_items) .. " target_per_minute=" .. string.format("%.2f", target_per_minute) .. " product_items=" .. sorted_count_string(throughput_current_product_items) .. " input_items=" .. sorted_count_string(throughput_current_input_items))
  log("BLUEPRINT_LAB_VALIDATION right_boundary_throughput_lane_window index=" .. tostring(throughput_window_count) .. " target_item=" .. tostring(throughput_target_item_name) .. " target_by_line=" .. sorted_count_string(throughput_current_target_by_line))
  throughput_current_product_items = {{}}
  throughput_current_input_items = {{}}
  throughput_current_target_items = 0
  throughput_current_target_by_line = {{}}
  throughput_current_start_tick = game.tick
  throughput_current_max_x = nil
  throughput_current_probe_x = nil
  throughput_current_belt_count = 0
  throughput_current_line_count = 0
end

local function log_right_boundary_throughput_summary()
  local observed_ticks = 0
  if throughput_start_tick ~= nil and throughput_last_tick ~= nil then
    observed_ticks = throughput_last_tick - throughput_start_tick
  end
  local target_per_minute = 0
  if observed_ticks > 0 then
    target_per_minute = throughput_removed_target_items * 3600 / observed_ticks
  end
  log("BLUEPRINT_LAB_VALIDATION right_boundary_throughput_summary window_ticks=" .. tostring(throughput_window_ticks) .. " windows=" .. tostring(throughput_window_count) .. " observed_ticks=" .. tostring(observed_ticks) .. " probe_x=" .. tostring(throughput_probe_x) .. " target_item=" .. tostring(throughput_target_item_name) .. " target_removed=" .. tostring(throughput_removed_target_items) .. " target_per_minute=" .. string.format("%.2f", target_per_minute) .. " product_items=" .. sorted_count_string(throughput_removed_product_items) .. " input_items=" .. sorted_count_string(throughput_removed_input_items))
  log("BLUEPRINT_LAB_VALIDATION right_boundary_throughput_lane_summary target_item=" .. tostring(throughput_target_item_name) .. " target_by_line=" .. sorted_count_string(throughput_removed_target_by_line))
end

local function inject_input_items_to_left_belts(surface, should_log)
  local input_items = collect_recipe_input_items(surface)
  local item_names = sorted_keys(input_items)
  if #item_names == 0 then
    if should_log ~= false then
      log("BLUEPRINT_LAB_VALIDATION input_injection items= belts=0 lines=0 inserted=0 failures=0")
    end
    return {{inserted = 0, failures = 0}}
  end

  local belt_candidates = {{}}
  local min_x = nil
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_line_count, line_count = pcall(function()
      return entity.get_max_transport_line_index()
    end)
    if ok_line_count and line_count ~= nil and line_count > 0 and entity.direction == defines.direction.east then
      local x = entity.position.x
      if min_x == nil or x < min_x then
        min_x = x
      end
      belt_candidates[#belt_candidates + 1] = {{entity = entity, line_count = line_count, x = x}}
    end
  end

  local belt_count = 0
  local line_count_total = 0
  local inserted_count = 0
  local insertion_failures = 0
  if min_x ~= nil then
    for _, candidate in pairs(belt_candidates) do
      if candidate.x <= min_x + 0.1 then
        belt_count = belt_count + 1
        for line_index = 1, candidate.line_count do
          local line = candidate.entity.get_transport_line(line_index)
          if line ~= nil then
            line_count_total = line_count_total + 1
            local line_length = line.line_length or 1
            local position = 0
            while position <= line_length do
              for _, item_name in pairs(item_names) do
                local ok_insert = pcall(function()
                  line.force_insert_at(position, {{name = item_name, count = 1}}, 1)
                end)
                if ok_insert then
                  inserted_count = inserted_count + 1
                else
                  insertion_failures = insertion_failures + 1
                end
              end
              position = position + 0.25
            end
          end
        end
      end
    end
  end
  if should_log ~= false then
    log("BLUEPRINT_LAB_VALIDATION input_injection items=" .. table.concat(item_names, ",") .. " belts=" .. tostring(belt_count) .. " lines=" .. tostring(line_count_total) .. " inserted=" .. tostring(inserted_count) .. " failures=" .. tostring(insertion_failures))
  end
  return {{inserted = inserted_count, failures = insertion_failures}}
end

local function inject_input_items_to_machine_pickup_belts(surface)
  local input_items = collect_recipe_input_items(surface)
  local item_names = sorted_keys(input_items)
  if #item_names == 0 then
    log("BLUEPRINT_LAB_VALIDATION machine_pickup_injection items= inserters=0 belts=0 lines=0 inserted=0 failures=0")
    return
  end

  local recipe_machines = {{}}
  for _, entity in pairs(surface.find_entities_filtered{{force = "player"}}) do
    local ok_recipe, recipe = pcall(function()
      return entity.get_recipe()
    end)
    if ok_recipe and recipe ~= nil then
      recipe_machines[entity.unit_number or tostring(entity.position.x) .. ":" .. tostring(entity.position.y)] = entity
    end
  end

  local function recipe_machine_at_position(position)
    if position == nil then
      return nil
    end
    local entities = surface.find_entities_filtered{{
      force = "player",
      area = {{
        {{position.x - 0.1, position.y - 0.1}},
        {{position.x + 0.1, position.y + 0.1}},
      }},
    }}
    for _, entity in pairs(entities) do
      local machine_key = entity.unit_number or tostring(entity.position.x) .. ":" .. tostring(entity.position.y)
      if recipe_machines[machine_key] ~= nil then
        return entity
      end
    end
    return nil
  end

  local function transport_line_entity_at_position(position)
    if position == nil then
      return nil
    end
    local entities = surface.find_entities_filtered{{
      force = "player",
      area = {{
        {{position.x - 0.1, position.y - 0.1}},
        {{position.x + 0.1, position.y + 0.1}},
      }},
    }}
    for _, entity in pairs(entities) do
      local ok_line_count, max_line_index = pcall(function()
        return entity.get_max_transport_line_index()
      end)
      if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
        return entity
      end
    end
    return nil
  end

  local inserter_count = 0
  local total_inserters = 0
  local drop_target_count = 0
  local geometry_drop_count = 0
  local pickup_target_count = 0
  local geometry_pickup_count = 0
  local belt_keys = {{}}
  local belt_count = 0
  local line_count = 0
  local inserted_count = 0
  local insertion_failures = 0
  for _, inserter in pairs(surface.find_entities_filtered{{force = "player"}}) do
    if inserter.type == "inserter" then
      total_inserters = total_inserters + 1
    else
      goto continue_inserter
    end
    local ok_drop_target, drop_target = pcall(function()
      return inserter.drop_target
    end)
    if not ok_drop_target then
      drop_target = nil
    end
    local used_geometry_drop = false
    if drop_target == nil then
      used_geometry_drop = true
      drop_target = recipe_machine_at_position(inserter.drop_position)
    end
    if drop_target ~= nil then
      local machine_key = drop_target.unit_number or tostring(drop_target.position.x) .. ":" .. tostring(drop_target.position.y)
      if recipe_machines[machine_key] ~= nil then
        if used_geometry_drop then
          geometry_drop_count = geometry_drop_count + 1
        else
          drop_target_count = drop_target_count + 1
        end
        local ok_pickup_target, pickup_target = pcall(function()
          return inserter.pickup_target
        end)
        if not ok_pickup_target then
          pickup_target = nil
        end
        local used_geometry_pickup = false
        if pickup_target == nil then
          used_geometry_pickup = true
          pickup_target = transport_line_entity_at_position(inserter.pickup_position)
        end
        if pickup_target ~= nil then
          local ok_line_count, max_line_index = pcall(function()
            return pickup_target.get_max_transport_line_index()
          end)
          if ok_line_count and max_line_index ~= nil and max_line_index > 0 then
            inserter_count = inserter_count + 1
            if used_geometry_pickup then
              geometry_pickup_count = geometry_pickup_count + 1
            else
              pickup_target_count = pickup_target_count + 1
            end
            local belt_key = pickup_target.unit_number or tostring(pickup_target.position.x) .. ":" .. tostring(pickup_target.position.y)
            if not belt_keys[belt_key] then
              belt_keys[belt_key] = true
              belt_count = belt_count + 1
            end
            local ok_spec, spec_line_index, spec_position = pcall(function()
              return pickup_target.get_item_insert_specification(inserter.pickup_position)
            end)
            if ok_spec and spec_line_index ~= nil and spec_line_index > 0 then
              local line = pickup_target.get_transport_line(spec_line_index)
              if line ~= nil then
                line_count = line_count + 1
                for _, item_name in pairs(item_names) do
                  local ok_insert = pcall(function()
                    line.force_insert_at(spec_position or 0, {{name = item_name, count = 1}}, 1)
                  end)
                  if ok_insert then
                    inserted_count = inserted_count + 1
                  else
                    insertion_failures = insertion_failures + 1
                  end
                end
              end
            else
              insertion_failures = insertion_failures + 1
            end
          end
        end
      end
    end
    ::continue_inserter::
  end
  log("BLUEPRINT_LAB_VALIDATION machine_pickup_injection items=" .. table.concat(item_names, ",") .. " total_inserters=" .. tostring(total_inserters) .. " inserters=" .. tostring(inserter_count) .. " drop_targets=" .. tostring(drop_target_count) .. " geometry_drops=" .. tostring(geometry_drop_count) .. " pickup_targets=" .. tostring(pickup_target_count) .. " geometry_pickups=" .. tostring(geometry_pickup_count) .. " belts=" .. tostring(belt_count) .. " lines=" .. tostring(line_count) .. " inserted=" .. tostring(inserted_count) .. " failures=" .. tostring(insertion_failures))
end

local validation_started = false
local pending_audit_surface = nil
local pending_audit_tick = nil
local function run_validation()
  log("BLUEPRINT_LAB_VALIDATION start")
  game.forces.player.enable_all_recipes()
  game.forces.player.enable_all_technologies()
  log("BLUEPRINT_LAB_VALIDATION force_unlocks=all_recipes,all_technologies")

  local inventory = game.create_inventory(1)
  local stack = inventory[1]
  if not stack.set_stack({{name = "blueprint"}}) then
    validation_fail("set_stack blueprint failed")
  end

  local import_result = stack.import_stack(blueprint_string)
  log("BLUEPRINT_LAB_VALIDATION import_result=" .. tostring(import_result))
  if import_result ~= 0 then
    validation_fail("import_stack returned " .. tostring(import_result))
  end

  local blueprint_entities = stack.get_blueprint_entities() or {{}}
  local blueprint_tiles = stack.get_blueprint_tiles() or {{}}
  log("BLUEPRINT_LAB_VALIDATION blueprint_entities=" .. tostring(#blueprint_entities) .. " blueprint_tiles=" .. tostring(#blueprint_tiles))
  if #blueprint_entities == 0 then
    validation_fail("blueprint has no entities after import")
  end

  local needs_space_platform = false
  for _, tile in pairs(blueprint_tiles) do
    if tile.name == "space-platform-foundation" then
      needs_space_platform = true
      break
    end
  end

  local surface
  local build_position = {{x = 0, y = 0}}
  if needs_space_platform then
    game.forces.player.unlock_space_platforms()
    local ok, platform = pcall(function()
      return game.forces.player.create_space_platform{{
        name = "blueprint_lab_validation_platform",
        planet = "nauvis",
        starter_pack = "space-platform-starter-pack",
      }}
    end)
    if not ok then
      validation_fail("create_space_platform failed: " .. tostring(platform))
    end
    platform.hidden = true
    if platform.surface == nil then
      platform.apply_starter_pack()
    end
    surface = platform.surface
    if surface == nil then
      validation_fail("space platform surface was not created")
    end
    build_position = {{x = 64, y = 0}}
    local min_tile_x = nil
    local min_tile_y = nil
    for _, tile in pairs(blueprint_tiles) do
      if min_tile_x == nil or tile.position.x < min_tile_x then
        min_tile_x = tile.position.x
      end
      if min_tile_y == nil or tile.position.y < min_tile_y then
        min_tile_y = tile.position.y
      end
    end
    local platform_tiles = {{}}
    local platform_tile_keys = {{}}
    local function add_platform_tile(name, x, y)
      local key = tostring(name) .. ":" .. tostring(x) .. ":" .. tostring(y)
      if platform_tile_keys[key] then
        return
      end
      platform_tile_keys[key] = true
      platform_tiles[#platform_tiles + 1] = {{
        name = name,
        position = {{x = x, y = y}},
      }}
    end
    for _, tile in pairs(blueprint_tiles) do
      add_platform_tile(tile.name, tile.position.x + build_position.x, tile.position.y + build_position.y)
      add_platform_tile(tile.name, tile.position.x - min_tile_x + build_position.x, tile.position.y - min_tile_y + build_position.y)
    end
    surface.set_tiles(platform_tiles, true, true, true, false)
    log("BLUEPRINT_LAB_VALIDATION platform_tiles_set=" .. tostring(#platform_tiles))
    log("BLUEPRINT_LAB_VALIDATION surface=space-platform")
  else
    surface = game.create_surface("blueprint_lab_validation_surface")
    surface.request_to_generate_chunks({{0, 0}}, 8)
    surface.force_generate_chunk_requests()
    log("BLUEPRINT_LAB_VALIDATION surface=generic")
  end

  local can_place_checked = 0
  for _, entity in pairs(blueprint_entities) do
    if can_place_checked >= 8 then
      break
    end
    local entity_direction = entity.direction or defines.direction.north
    local can_place = surface.can_place_entity{{
      name = entity.name,
      position = {{
        x = entity.position.x + build_position.x,
        y = entity.position.y + build_position.y,
      }},
      direction = entity_direction,
      force = game.forces.player,
      build_check_type = defines.build_check_type.blueprint_ghost,
      forced = true,
    }}
    log("BLUEPRINT_LAB_VALIDATION can_place " .. entity.name .. "=" .. tostring(can_place))
    can_place_checked = can_place_checked + 1
  end

  local ok, built_entities = pcall(function()
    return stack.build_blueprint{{
      surface = surface.name,
      force = "player",
      position = build_position,
      build_mode = defines.build_mode.superforced,
      skip_fog_of_war = false,
      raise_built = false,
    }}
  end)
  if not ok then
    validation_fail("build_blueprint failed: " .. tostring(built_entities))
  end

  local built_count = #built_entities
  log("BLUEPRINT_LAB_VALIDATION built_entities=" .. tostring(built_count))
  if built_count == 0 then
    log("BLUEPRINT_LAB_VALIDATION manual_fallback=start")
    local manual_count = 0
    local manual_failures = 0
    local recipe_failures = 0
    local recipe_set_count = 0
    local underground_type_count = 0
    local underground_type_failures = 0
    local module_inserted_count = 0
    local module_insert_failures = 0
    local splitter_setting_count = 0
    local splitter_setting_failures = 0
    local inserter_filter_count = 0
    local inserter_filter_failures = 0
    for _, entity in pairs(blueprint_entities) do
      local entity_direction = entity.direction or defines.direction.north
      local ok_create, created = pcall(function()
        return surface.create_entity{{
          name = entity.name,
          position = {{
            x = entity.position.x + build_position.x,
            y = entity.position.y + build_position.y,
          }},
          direction = entity_direction,
          type = entity.type,
          quality = entity.quality,
          force = "player",
          raise_built = false,
        }}
      end)
      if not ok_create or created == nil then
        manual_failures = manual_failures + 1
        if manual_failures <= 8 then
          local fail_x = entity.position.x + build_position.x
          local fail_y = entity.position.y + build_position.y
          local can_place_after_failure = surface.can_place_entity{{
            name = entity.name,
            position = {{x = fail_x, y = fail_y}},
            direction = entity_direction,
            force = game.forces.player,
            build_check_type = defines.build_check_type.blueprint_ghost,
            forced = true,
          }}
          log("BLUEPRINT_LAB_VALIDATION manual_create_failed " .. entity.name .. "=" .. tostring(created) .. " x=" .. tostring(fail_x) .. " y=" .. tostring(fail_y) .. " direction=" .. tostring(entity_direction) .. " type=" .. tostring(entity.type) .. " quality=" .. tostring(entity.quality) .. " can_place=" .. tostring(can_place_after_failure) .. " error=" .. tostring(ok_create and "nil" or created))
        end
      else
        manual_count = manual_count + 1
        if entity.type ~= nil then
          local ok_belt_type, belt_type_result = pcall(function()
            return created.belt_to_ground_type
          end)
          if ok_belt_type and belt_type_result == entity.type then
            underground_type_count = underground_type_count + 1
          else
            underground_type_failures = underground_type_failures + 1
            if underground_type_failures <= 8 then
              log("BLUEPRINT_LAB_VALIDATION manual_underground_type_failed " .. entity.name .. "=" .. tostring(belt_type_result) .. " expected=" .. tostring(entity.type))
            end
          end
        end
        if entity.recipe ~= nil then
          local ok_recipe, recipe_result = pcall(function()
            if entity.recipe_quality == nil or entity.recipe_quality == "normal" then
              created.set_recipe(entity.recipe)
            else
              created.set_recipe(entity.recipe, entity.recipe_quality)
            end
          end)
          if not ok_recipe then
            recipe_failures = recipe_failures + 1
            if recipe_failures <= 8 then
              log("BLUEPRINT_LAB_VALIDATION manual_recipe_failed " .. entity.name .. "=" .. tostring(recipe_result))
            end
          else
            recipe_set_count = recipe_set_count + 1
          end
        end
        if entity.filter ~= nil then
          local ok_splitter_filter, splitter_filter_result = pcall(function()
            created.splitter_filter = entity.filter
          end)
          if ok_splitter_filter then
            splitter_setting_count = splitter_setting_count + 1
          else
            splitter_setting_failures = splitter_setting_failures + 1
            if splitter_setting_failures <= 8 then
              log("BLUEPRINT_LAB_VALIDATION manual_splitter_filter_failed " .. entity.name .. "=" .. tostring(splitter_filter_result))
            end
          end
        end
        if entity.input_priority ~= nil then
          local ok_splitter_input_priority, splitter_input_priority_result = pcall(function()
            created.splitter_input_priority = entity.input_priority
          end)
          if ok_splitter_input_priority then
            splitter_setting_count = splitter_setting_count + 1
          else
            splitter_setting_failures = splitter_setting_failures + 1
            if splitter_setting_failures <= 8 then
              log("BLUEPRINT_LAB_VALIDATION manual_splitter_input_priority_failed " .. entity.name .. "=" .. tostring(splitter_input_priority_result))
            end
          end
        end
        if entity.output_priority ~= nil then
          local ok_splitter_output_priority, splitter_output_priority_result = pcall(function()
            created.splitter_output_priority = entity.output_priority
          end)
          if ok_splitter_output_priority then
            splitter_setting_count = splitter_setting_count + 1
          else
            splitter_setting_failures = splitter_setting_failures + 1
            if splitter_setting_failures <= 8 then
              log("BLUEPRINT_LAB_VALIDATION manual_splitter_output_priority_failed " .. entity.name .. "=" .. tostring(splitter_output_priority_result))
            end
          end
        end
        if entity.filters ~= nil then
          for filter_index, filter in pairs(entity.filters) do
            local target_index = filter.index or filter_index
            local ok_inserter_filter, inserter_filter_result = pcall(function()
              created.set_filter(target_index, filter)
            end)
            if ok_inserter_filter then
              inserter_filter_count = inserter_filter_count + 1
            else
              inserter_filter_failures = inserter_filter_failures + 1
              if inserter_filter_failures <= 8 then
                log("BLUEPRINT_LAB_VALIDATION manual_inserter_filter_failed " .. entity.name .. "=" .. tostring(inserter_filter_result))
              end
            end
          end
        end
        if entity.use_filters ~= nil then
          local ok_inserter_use_filters, inserter_use_filters_result = pcall(function()
            created.use_filters = entity.use_filters
          end)
          if not ok_inserter_use_filters then
            inserter_filter_failures = inserter_filter_failures + 1
            if inserter_filter_failures <= 8 then
              log("BLUEPRINT_LAB_VALIDATION manual_inserter_use_filters_failed " .. entity.name .. "=" .. tostring(inserter_use_filters_result))
            end
          end
        end
        if entity.items ~= nil then
          local module_inventory = created.get_module_inventory()
          if module_inventory ~= nil then
            for _, item_stack in pairs(entity.items) do
              local item_id = item_stack.id or {{}}
              local requested_count = 0
              local item_payload = item_stack.items or {{}}
              for _, inventory_stack in pairs(item_payload.in_inventory or {{}}) do
                requested_count = requested_count + 1
              end
              if requested_count > 0 and item_id.name ~= nil then
                local ok_insert, inserted_count = pcall(function()
                  return module_inventory.insert{{
                    name = item_id.name,
                    quality = item_id.quality,
                    count = requested_count,
                  }}
                end)
                if ok_insert then
                  module_inserted_count = module_inserted_count + inserted_count
                  if inserted_count ~= requested_count then
                    module_insert_failures = module_insert_failures + 1
                    if module_insert_failures <= 8 then
                      log("BLUEPRINT_LAB_VALIDATION manual_module_partial " .. entity.name .. "=" .. tostring(inserted_count) .. "/" .. tostring(requested_count))
                    end
                  end
                else
                  module_insert_failures = module_insert_failures + 1
                  if module_insert_failures <= 8 then
                    log("BLUEPRINT_LAB_VALIDATION manual_module_failed " .. entity.name .. "=" .. tostring(inserted_count))
                  end
                end
              end
            end
          end
        end
      end
    end
    log("BLUEPRINT_LAB_VALIDATION manual_entities=" .. tostring(manual_count) .. " manual_failures=" .. tostring(manual_failures) .. " manual_recipe_set=" .. tostring(recipe_set_count) .. " manual_recipe_failures=" .. tostring(recipe_failures))
    log("BLUEPRINT_LAB_VALIDATION manual_underground_types=" .. tostring(underground_type_count) .. " manual_underground_type_failures=" .. tostring(underground_type_failures) .. " manual_modules_inserted=" .. tostring(module_inserted_count) .. " manual_module_failures=" .. tostring(module_insert_failures))
    log("BLUEPRINT_LAB_VALIDATION manual_splitter_settings=" .. tostring(splitter_setting_count) .. " manual_splitter_setting_failures=" .. tostring(splitter_setting_failures))
    log("BLUEPRINT_LAB_VALIDATION manual_inserter_filters=" .. tostring(inserter_filter_count) .. " manual_inserter_filter_failures=" .. tostring(inserter_filter_failures))
    if manual_failures > 0 or recipe_failures > 0 or underground_type_failures > 0 or module_insert_failures > 0 or splitter_setting_failures > 0 or inserter_filter_failures > 0 then
      validation_fail("manual fallback failed to place all entities")
    end
  end

  local surface_entities = surface.find_entities_filtered{{force = game.forces.player}}
  log("BLUEPRINT_LAB_VALIDATION surface_entities=" .. tostring(#surface_entities))
  if input_probe_mode == "left" or input_probe_mode == "both" then
    inject_input_items_to_left_belts(surface)
  end
  if input_probe_mode == "pickup" or input_probe_mode == "both" then
    inject_input_items_to_machine_pickup_belts(surface)
  end
  pending_audit_surface = surface
  pending_audit_tick = game.tick + runtime_audit_wait_ticks
  if sustained_input_interval_ticks > 0 and (input_probe_mode == "left" or input_probe_mode == "both") then
    next_sustained_input_tick = game.tick + sustained_input_interval_ticks
  end
  if throughput_window_ticks > 0 then
    throughput_start_tick = game.tick
    throughput_current_start_tick = game.tick
    next_throughput_window_tick = game.tick + throughput_window_ticks
  end
  log("BLUEPRINT_LAB_VALIDATION runtime_audit_wait_ticks=" .. tostring(runtime_audit_wait_ticks))
  log("BLUEPRINT_LAB_VALIDATION sustained_input_interval_ticks=" .. tostring(sustained_input_interval_ticks))
  log("BLUEPRINT_LAB_VALIDATION throughput_window_ticks=" .. tostring(throughput_window_ticks) .. " throughput_target_item=" .. tostring(throughput_target_item_name))
end

script.on_event(defines.events.on_tick, function(event)
  if not validation_started then
    validation_started = true
    run_validation()
    return
  end
  if pending_audit_surface ~= nil and next_sustained_input_tick ~= nil and game.tick >= next_sustained_input_tick and game.tick < pending_audit_tick then
    local result = inject_input_items_to_left_belts(pending_audit_surface, false)
    sustained_input_cycles = sustained_input_cycles + 1
    sustained_input_inserted = sustained_input_inserted + (result.inserted or 0)
    sustained_input_failures = sustained_input_failures + (result.failures or 0)
    next_sustained_input_tick = game.tick + sustained_input_interval_ticks
  end
  if pending_audit_surface ~= nil and next_throughput_window_tick ~= nil and game.tick < pending_audit_tick then
    drain_right_boundary_throughput_tick(pending_audit_surface)
    if game.tick >= next_throughput_window_tick then
      log_right_boundary_throughput_window()
      next_throughput_window_tick = game.tick + throughput_window_ticks
    end
  end
  if pending_audit_surface ~= nil and game.tick >= pending_audit_tick then
    script.on_event(defines.events.on_tick, nil)
    log("BLUEPRINT_LAB_VALIDATION sustained_input_injection interval_ticks=" .. tostring(sustained_input_interval_ticks) .. " cycles=" .. tostring(sustained_input_cycles) .. " inserted=" .. tostring(sustained_input_inserted) .. " failures=" .. tostring(sustained_input_failures))
    if throughput_window_ticks > 0 then
      log_right_boundary_throughput_summary()
    end
    audit_recipe_machines(pending_audit_surface)
    audit_recipe_machine_output_items(pending_audit_surface)
    audit_output_unloading(pending_audit_surface)
    audit_transport_items(pending_audit_surface)
    audit_transport_item_extents(pending_audit_surface)
    audit_right_boundary_transport_items(pending_audit_surface)
    log("BLUEPRINT_LAB_VALIDATION success")
    validation_fail("completed")
  end
end)
"""


def effective_runtime_audit_wait_ticks(until_tick: int, runtime_audit_wait_ticks: int) -> int:
    return max(int(until_tick), int(runtime_audit_wait_ticks))


def write_validation_scenario(
    *,
    user_data_dir: Path,
    scenario_name: str,
    blueprint_string: str,
    input_probe: str = "both",
    runtime_audit_wait_ticks: int = 120,
    sustained_input_interval_ticks: int = 0,
    throughput_window_ticks: int = 0,
    throughput_target_item: str = "",
    throughput_probe_x: float | None = None,
) -> Path:
    scenario_dir = user_data_dir / "scenarios" / scenario_name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "info.json").write_text(
        json.dumps(
            {
                "name": scenario_name,
                "version": "1.0.0",
                "title": "Blueprint Lab Validation",
                "description": "Temporary scenario generated by Blueprint Lab to import and build one blueprint.",
                "factorio_version": "2.0",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (scenario_dir / "control.lua").write_text(
        render_control_lua(
            blueprint_string,
            input_probe=input_probe,
            runtime_audit_wait_ticks=runtime_audit_wait_ticks,
            sustained_input_interval_ticks=sustained_input_interval_ticks,
            throughput_window_ticks=throughput_window_ticks,
            throughput_target_item=throughput_target_item,
            throughput_probe_x=throughput_probe_x,
        ),
        encoding="utf-8",
    )
    return scenario_dir


def write_server_settings(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "name": "Blueprint Lab Validation",
                "description": "Temporary server for Blueprint Lab validation.",
                "tags": [],
                "max_players": 0,
                "visibility": {"public": False, "lan": False},
                "username": "",
                "password": "",
                "token": "",
                "game_password": "",
                "require_user_verification": False,
                "max_upload_in_kilobytes_per_second": 0,
                "ignore_player_limit_for_returning_players": False,
                "allow_commands": "admins-only",
                "autosave_interval": 0,
                "autosave_slots": 1,
                "afk_autokick_interval": 0,
                "auto_pause": False,
                "auto_pause_when_players_connect": False,
                "only_admins_can_pause_the_game": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def factorio_windows_path(path: Path) -> str:
    resolved = path.resolve()
    parts = resolved.parts
    if len(parts) >= 4 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1:
        drive = parts[2].upper()
        return drive + ":\\" + "\\".join(parts[3:])
    return str(resolved)


def write_factorio_config(user_data_dir: Path) -> Path:
    config_dir = user_data_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "codex-blueprint-lab-config.ini"
    config_path.write_text(
        "\n".join(
            [
                "; version=13",
                "[path]",
                "read-data=__PATH__system-read-data__",
                f"write-data={factorio_windows_path(user_data_dir)}",
                "",
                "[general]",
                "locale=auto",
                "",
                "[other]",
                "enable-blueprint-storage-cloud-sync=false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def validation_markers(log_text: str) -> list[str]:
    return [line for line in log_text.splitlines() if "BLUEPRINT_LAB_VALIDATION" in line]


def process_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def kill_factorio_processes_for_scenario(scenario_name: str) -> list[int]:
    if not POWERSHELL_EXE.exists() or not TASKKILL_EXE.exists():
        return []
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'factorio.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{scenario_name}*' }} | "
        "ForEach-Object { $_.ProcessId }"
    )
    found = subprocess.run(
        [str(POWERSHELL_EXE), "-NoProfile", "-Command", command],
        text=True,
        capture_output=True,
        check=False,
    )
    killed: list[int] = []
    for line in found.stdout.splitlines():
        line = line.strip()
        if not line.isdigit():
            continue
        pid = int(line)
        subprocess.run([str(TASKKILL_EXE), "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        killed.append(pid)
    return killed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one generated blueprint with a real Factorio runtime scenario.")
    parser.add_argument("--factorio-exe", type=Path, default=DEFAULT_FACTORIO_EXE)
    parser.add_argument("--user-data-dir", type=Path, default=DEFAULT_USER_DATA)
    parser.add_argument("--mod-directory", type=Path)
    parser.add_argument("--scenario-name", default=DEFAULT_SCENARIO_NAME)
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--console-log", type=Path, default=Path(".codex/artifacts/blueprint-lab-factorio-validation.log"))
    parser.add_argument("--until-tick", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=45)
    parser.add_argument("--input-probe", choices=["left", "pickup", "both"], default="both")
    parser.add_argument("--runtime-audit-wait-ticks", type=int, default=120)
    parser.add_argument("--sustained-input-interval-ticks", type=int, default=0)
    parser.add_argument("--throughput-window-ticks", type=int, default=0)
    parser.add_argument("--throughput-target-item", default="")
    parser.add_argument("--throughput-probe-x", type=float, help="Drain throughput at this exact x column instead of the rightmost boundary belt column.")
    args = parser.parse_args(argv)

    blueprint_string = args.blueprint.read_text(encoding="utf-8").strip()
    scenario_dir = write_validation_scenario(
        user_data_dir=args.user_data_dir,
        scenario_name=args.scenario_name,
        blueprint_string=blueprint_string,
        input_probe=args.input_probe,
        runtime_audit_wait_ticks=effective_runtime_audit_wait_ticks(args.until_tick, args.runtime_audit_wait_ticks),
        sustained_input_interval_ticks=args.sustained_input_interval_ticks,
        throughput_window_ticks=args.throughput_window_ticks,
        throughput_target_item=args.throughput_target_item,
        throughput_probe_x=args.throughput_probe_x,
    )

    args.console_log.parent.mkdir(parents=True, exist_ok=True)
    server_settings = write_server_settings(args.console_log.parent / "blueprint-lab-server-settings.json")
    factorio_config = write_factorio_config(args.user_data_dir)
    command = [
        str(args.factorio_exe),
        "--config",
        str(factorio_config),
        "--start-server-load-scenario",
        args.scenario_name,
        "--server-settings",
        str(server_settings),
        "--until-tick",
        str(args.until_tick),
        "--console-log",
        str(args.console_log),
        "--disable-audio",
    ]
    if args.mod_directory:
        command.extend(["--mod-directory", str(args.mod_directory)])

    timed_out = False
    killed_pids: list[int] = []
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=args.timeout_seconds)
        stdout = completed.stdout
        stderr = completed.stderr
        command_exit: int | str = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = process_output_text(exc.stdout)
        stderr = process_output_text(exc.stderr)
        killed_pids = kill_factorio_processes_for_scenario(args.scenario_name)
        command_exit = "timeout"
    log_text = args.console_log.read_text(encoding="utf-8", errors="replace") if args.console_log.exists() else ""
    markers = validation_markers(log_text + "\n" + stdout + "\n" + stderr)
    print(f"scenario={scenario_dir}")
    print(f"command_exit={command_exit}")
    if timed_out:
        print(f"timeout_seconds={args.timeout_seconds}")
        print(f"killed_factorio_pids={killed_pids}")
    for marker in markers:
        print(marker)
    if any("BLUEPRINT_LAB_VALIDATION success" in marker for marker in markers):
        return 0
    if timed_out:
        print(stdout)
        print(stderr)
        return 124
    if command_exit != 0:
        print(stdout)
        print(stderr)
        return int(command_exit)
    print("FAIL: Factorio exited without validation success marker")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
