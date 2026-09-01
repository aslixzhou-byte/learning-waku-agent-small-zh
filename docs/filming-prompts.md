# 拍摄提示词——复制粘贴清单

上镜时在 Compare跑的确切提示词。**逐字粘贴每一条**——Compare按精确文本匹配来打 Completion 分（打错一个字 → 它照常比赛但 solved 显示 "—"）。打开 **"grade with K3"** 还能拿到 Quality 分。

完整基准组 + 预期结果：[benchmarks.md §3](benchmarks.md)。指标含义 + 算法：[benchmarks.md §10](benchmarks.md)。本文件只是提示词，按拍摄顺序排列。

## 第一幕——简单（人人都该过）

```
Schedule a coffee with Alex next Tuesday at 9am
```
```
Remember that Alex prefers morning meetings
```
```
Send Alex a message that the demo moved to Friday
```
```
What is the capital of France?
```
> 最后一条是诚实的**无工具**案例：好模型不调工具就能回答（绿色 "solved" = 它正确地保持不动手）。

## 第二幕——硬（便宜的模型在这里翻车——最值钱的片段）

```
I might grab coffee with Alex sometime, we'll see.
```
> 绝不能安排任何日程（过度积极陷阱）。

```
Block three 25-minute focus sessions tomorrow morning
```
> 必须建**三个**事件，不是一个（数量精确性）。

```
Remember that I'm vegetarian, then book dinner with Sam this Thursday at 7pm
```
> 必须**两件都做**——既存笔记又约日程（完整度）。

```
Check my calendar for a free 30 minutes this afternoon and schedule a short walk
```
> 安排日程前必须**先读**日历（状态感知）。

```
Book a catch-up with Alex on Friday
```
> Compare会自动预载事实"Alex prefers morning meetings"——好模型会应用它（约一个早上的时段）而不是忽略它。

## 第三幕——多工具展示

```
Build me a Kanto starter team around Pikachu: search current competitive picks for a balanced six, remember that Pikachu is my starter, and schedule two team-training sessions this week
```
```
Search for the result of the Spain vs Argentina World Cup final, remember who won, and draft a message to Raj about watching the highlights together
```
> 每条都需要 3+ 次工具调用：search + remember + schedule/message。

## 第四幕——揭晓

滚动到 **Scoreboard**：成本-vs-质量**散点图**（便宜又好 = 左上角），然后按 **solved**、**K3 grade** 或 **total cost** 排序表格。

## 第五幕——编码回合（终端，还不是Compare）

```bash
make shootout-coding RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8 gemini:gemini-3.5-flash"
```
每个模型的 pi 写真实代码，以测试是否通过打分。已实现 vs 未实现的说明见 [benchmarks.md §3.B](benchmarks.md) 里的 "coding in the arena" 备注。
