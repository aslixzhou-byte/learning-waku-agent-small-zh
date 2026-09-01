# Dashboard 前端——地图

由 `waku/ops/dashboard.py`（stdlib HTTP 服务器）原样托管的朴素静态文件。**没有构建步骤、没有框架、没有打包器、没有依赖。** 改这些文件来改 UI；改 `dashboard.py` 来改服务器/API。

- `index.html`——外壳（侧边栏导航、`<main>`、聊天坞）+ 有序的 `<script>` 标签。
- `style.css`——一个扁平文件，顶部是 `:root` 设计令牌，浅色 + 深色。
- `js/`——应用，按职责拆分（见下）。

## 这些文件（`js/`），按加载顺序

它们是**共享同一个全局作用域的经典脚本**——一个文件里的 `function`/`let`/`const` 对所有其它文件可见。顺序只在一点上有讲究：**`main.js` 负责启动引导，必须最后加载**。

| 文件 | 这里有什么 |
|------|-----------------|
| `util.js`    | `esc`、markdown 渲染器、核心全局变量（`D`、`editing`）、`postJSON`、`reveal` |
| `memory.js`  | 内联的 Memory / SOUL / skill 编辑动作 |
| `models.js`  | `applyModel`（唯一的 `/api/settings` 写入者）、模型选择器 / 目录 / 置顶 |
| `render.js`  | 格式化器 + 聊天卡片渲染器（`stagesRow`/`teleFooter`）+ 聊天记录 + 流式 + `sendChat` |
| `diagram.js` | `archSVG`（架构图）**以及**它的实时动画（`STAGE`/`hot`/`pollEvents`） |
| `views.js`   | 子标签/db 辅助、SQL console、Memory/Tools 子视图、`VIEWS` 路由对象 |
| `compare.js` | Compare（`Compare` 标签）——让一条消息同时穿过多个模型比赛 |
| `dock.js`    | 聊天会话/历史（`loadThreadInto`）、模型芯片、统计开关 |
| `main.js`    | `render`/`refresh` 循环、resizers、语音，以及启动引导（**最后加载**） |

数据单向流动：`refresh()`（main.js）把 `/api/data` 抓进全局 `D`，然后 `render()` 把 `VIEWS[hash](D)` 写进 `#view`。每次变更（`applyModel`、`pinModel`、`saveFact`、……）完成后都会调用 `refresh()`。

## 会咬人的规则（改动前先读）

- **内联处理器需要全局名字。** 按钮在 JS 生成的 HTML 字符串里用 `onclick="fn()"`。`fn` 必须是某个 `js/` 文件里的顶层名字。重命名/移动一个处理器却忘了它的调用点 → 按钮会悄悄失效。`test_static_assets.py` 守护这一点。
- **`archSVG` 是字节冻结的——不要重写架构图。** 它发出的 `data-node="…"`/`data-edge="…"` id 由 `STAGE` 映射（同一个文件）驱动实时动画。如果你改了某个 node/edge id，两处都要改。（两者都放在 `diagram.js` 正是为了让它们在一起。）
- **没有构建步骤 / 没有框架 / 不加新依赖。** 如果你想用这些，停下来——全部要点就是它什么都不装也能读、能跑。
- **UI 里不用 emoji**（项目规则）。已知的历史例外：`models.js` 里的 `★`/`☆` 置顶星标（排版装饰符，不是彩色 emoji）——保持原样。

## 验证改动（没有 JS 测试运行器）

前端逻辑不做单元测试；在浏览器预览里验证：`make dashboard`（或预览工具）→ 硬刷新 `localhost:7777` → 点侧边栏标签和聊天坞 → 确认控制台**零报错**。Python 一侧（`dashboard.py` 端点、`_thread_history`、置顶、会话恢复）*有* `evals/deterministic/` 覆盖。

**运行中的服务器不会拾取 Python 改动。** 这里的静态文件（`.js`、`.css`、`index.html`）每次请求都从磁盘读取，所以硬刷新就能看到它们。但 `dashboard.py` 及其导入的一切都驻留在内存里——拉取或编辑后端代码后，**重启 `make dashboard`**，否则页面会用过期数据渲染新标记（比如新的 Settings 面板什么都显示不出来，因为旧路由没有发送它的字段）。
