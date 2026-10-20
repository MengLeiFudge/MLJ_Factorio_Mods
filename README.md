# MLJ Factorio Mods

这是一个 Factorio Lua 模组工作区。每个模组都以独立目录维护源码，
共享根目录的 `pack_mods.py` 进行发现、校验和打包。

## 当前模组

| 目录 | 模组 | 说明 |
|------|------|------|
| `DynamicInventory/` | DynamicInventory | 按玩家设置动态调整背包容量的运行时模组 |
| `expend-toolbar/` | expend-toolbar | 创建可移动自定义工具栏，并显示当前视图的物品数量 |
| `py_quick_start/` | py_quick_start | 给 Pyanodons 开局提供装备和机器人等物品的运行时模组 |
| `quality-cycler/` | quality-cycler | 调整蓝图、蓝图册和绿图中品质的运行时模组 |
| `section-autocraft/` | section-autocraft | 根据物流编组自动安排手搓的模组 |
| `ups_saving_quality_ships/` | ups_saving_quality_ships | 用品质枢纽模拟多艘同款飞船以降低 UPS 压力的 Space Age 模组 |

## 离线工具

| 目录 | 说明 |
|------|------|
| `tools/blueprint_lab/` | 离线蓝图分析和生成工具；用于解码蓝图字符串、扫描本地蓝图语料、提取紧凑布局经验，并生成第一版矩形黑盒种子蓝图 |
| `tools/update_factorio_mods.py` | 本机模组并行更新工具；无需启动游戏即可按当前 Factorio 版本下载兼容最新版并清理旧 zip |

## 本地参考代码

`references/mods/` 可以存放第三方参考模组源码。这些目录只用于查阅和对照，不是本仓库自己的可打包模组，不参与 `pack_mods.py` 发现，也不会进入 Git。

## 目录约定

每个可打包模组都使用同一种源码布局：

```text
<mod>/
└── src/
    ├── info.json
    ├── control.lua
    ├── settings.lua
    ├── data.lua
    ├── data-updates.lua
    ├── data-final-fixes.lua
    └── locale/
```

并不是每个模组都需要所有入口文件。`pack_mods.py` 会从
`<mod>/src/info.json` 发现模组，并把 `src/` 内容打包到 Factorio 需要的
压缩包根目录中。

## 打包

在项目根目录运行：

```bash
python3 pack_mods.py
```

产物会写入 `ModZips/`，并在 Windows Factorio mods 目录存在时复制一份 zip 到该目录。文件名格式为：

```text
{info.name}_{info.version}.zip
```

压缩包内部的根目录也是：

```text
{info.name}_{info.version}/
```

在 WSL 中打包成功后，脚本会用 Windows 资源管理器打开 `ModZips/`。

## 调试部署

打包脚本会按模组自动处理 zip 复制和调试文件夹部署：

```text
%AppData%\Factorio\mods
```

注意事项：

- 成功打包后，脚本会把本轮生成的 `{info.name}_{info.version}.zip` 复制到该目录。
- 如果该目录已有 `{info.name}_*.zip`，脚本会跳过该模组的文件夹部署。
- 如果该目录没有 `{info.name}_*.zip`，脚本会复制本轮打包生成的 `{info.name}_{info.version}/` 文件夹。
- 不要直接把源码目录 `src/` 放进 Factorio mods 目录。

## 本机模组并行更新

关闭 Factorio 后，在项目根目录运行：

```bash
python3 tools/update_factorio_mods.py
```

Windows Python 也可以直接运行：

```powershell
py tools\update_factorio_mods.py
```

只查看更新计划而不修改文件：

```bash
python3 tools/update_factorio_mods.py --dry-run
```

工具会自动发现 Factorio 用户数据目录与当前游戏版本，读取 `player-data.json` 中已有的 Mod Portal 登录凭据，并默认使用 8 路并发下载。可用 `--workers 1..32` 调整并发数，也可用 `--user-data-dir` 和 `--factorio-version` 显式覆盖自动检测结果。

所有新包会先下载到用户数据目录下的可恢复缓存，并通过 Mod Portal SHA-1、zip 结构、包内 `info.json` 和必需依赖检查。下载中断后再次运行会复用已校验完成的包，并用 HTTP Range 继续未完成分片；全部成功后缓存自动清理。只有目标包验证并安装成功后，工具才删除同名旧版 zip；下载失败、同版本本地改包、非标准 zip、官网无当前兼容版本的模组和解压调试目录都会保留。工具不会修改模组启用状态，也不会自动安装本机原本没有的依赖。

这是使用 Factorio Mod Portal 内部下载 API 的非官方社区工具，与 Wube Software 无关联。请勿输出、提交或分享 `player-data.json` 中的 `service-token`。

## 发布

发布到 Factorio Mod Portal 使用独立脚本，平常打包不会触发官网发布：

```bash
python3 publish_mods.py --dry-run
python3 publish_mods.py --mod quality-cycler --dry-run
```

`publish_mods.py` 会读取本地 `<mod>/src/info.json`，查询 Mod Portal 的当前 release 列表，并比对本地版本和远端最新版本。本地版本等于或低于远端版本时会跳过；本地版本更高时，会检查 `ModZips/{info.name}_{info.version}.zip` 并在 dry-run 输出将要发布的 release。

真实发布必须显式使用 `--execute`。默认从已忽略的本地配置 `publish_mods.config.json` 读取 API key；环境变量 `FACTORIO_MOD_PORTAL_TOKEN` 非空时优先使用环境变量。

首次配置时可从示例复制：

```bash
cp publish_mods.config.example.json publish_mods.config.json
```

然后只在本地配置中填写 `factorio_mod_portal_token`，再执行：

```bash
python3 publish_mods.py --mod quality-cycler --execute
```

也可以继续只用环境变量：

```bash
FACTORIO_MOD_PORTAL_TOKEN=... python3 publish_mods.py --mod quality-cycler --execute
```

首次发布一个官网还不存在的模组时，需要额外显式允许创建：

```bash
python3 publish_mods.py --mod new-mod --execute --create
```

注意事项：

- `publish_mods.py` 不会自动调用 `pack_mods.py`；发布前先确认对应 zip 已经由打包脚本生成。
- API key 优先从 `FACTORIO_MOD_PORTAL_TOKEN` 读取，否则读取已忽略的 `publish_mods.config.json`；不要把真实 token 写入 example 或任何已跟踪文件。
- 可用 `--config-file <path>` 指定其他本地 JSON 配置。
- 默认 dry-run 只比对版本和检查本地 zip，不上传文件。
- `--execute` 会调用 Factorio 官方 Mod Portal API；只有明确要发布时才运行。
- 如果需要强制发布时工作区干净，可以加 `--require-clean`。

## 打包检查

需要生成并检查当前六个模组的压缩包时运行：

```bash
python3 pack_mods.py
```

本仓库不维护测试文件。打包命令输出 `Completed. Success: 6, Failed: 0` 时，表示当前 6 个模组都已成功生成 zip。

## Factorio 资料

- 官方 API 文档：`https://lua-api.factorio.com/latest/index.html`
- Data lifecycle：`https://lua-api.factorio.com/latest/auxiliary/data-lifecycle.html`
- Prototype 文档：`https://lua-api.factorio.com/latest/index-prototype.html`
- Runtime 文档：`https://lua-api.factorio.com/latest/index-runtime.html`
- Settings 教程：`https://wiki.factorio.com/Tutorial:Mod_settings`
- Modding tutorial：`https://wiki.factorio.com/Tutorial:Modding_tutorial/Gangsir`

实际行为以最新官方文档为准。
