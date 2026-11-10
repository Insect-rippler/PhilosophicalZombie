# 识域结构化：字段 + 固定顺序组装

状态：**设计稿**。定稿后一次性改：schema、解析、组装、迁移。

---

## 0. 三个决定

```
① 每份 md = YAML front-matter（字段）+ 正文（prose）
② 组装只读字段，按代码里写死的顺序渲染，不整文件拼接
③ 每个字段由谁写，写死；dream / impression 只能碰允许的字段
```

为什么用 YAML：库现成（PyYAML 6.0.3），中文与长句都能写，人改得动。
JSON 太密，TOML 表达不了嵌套列表里的长句。

---

## 1. 为什么必须固定顺序

现在组装是“把文件按名字拼起来”。问题：

```
文件里加一段 → 整段前缀变化 → 提示词缓存全废
dream 把新条目插在中间 → 同一份内容，两次组装顺序不同 → 缓存失效
```

固定顺序要固定三件事：

```
1 文件顺序      代码里的 ORDER 常量
2 字段顺序      每个文件的字段表（schema）里的声明顺序
3 渲染模板      每个字段怎么变成文字，写死，不留“看情况”
```

验收标准：**同样的数据，组装出的文本逐字节相同**（跑两次哈希一致）。

---

## 2. 文件与字段

### 2.1 AGENT.md —— 规则，不结构化

保持不变，整段读取。它是最稳定的前缀，改动最少。

### 2.2 CHARACTER.md —— 她是谁

```yaml
schema: 1
name: 薇薇安娜·德罗斯特
aliases: [薇莉安娜, 烛骑士]
origin: 莱塔尼亚 · 施彤领
identity:
  - 霍赫贝格家族后裔
  - 罗德岛干员
facts:                       # 不可让渡的客观事实
  - 名字是薇薇安娜，不是薇莉安娜
  - 鹿角长在头上，不是装饰
timeline:                    # 自传体记忆，按时间顺序
  - when: 长夜临光
    what: 与玛嘉烈决斗落败，当场认输
  - when: 来到罗德岛
    what: 为寻找远离莱塔尼亚的地方
body:                        # 外形与体征，给动作判定用
  height: 中等
  marks: [鹿角, 左手旧茧]
prose: |
  身体发肤的来历、家族的事，写在这里。
```

### 2.3 PERSONA.md —— 她怎么想、怎么选

这是最要紧的一份。字段全部对应心理学概念（见第 4 节）。

```yaml
schema: 1
temperament:                 # 先天参数，空白卡也从这里长
  reactivity: 0.35           # 反应强度 0~1
  approach: 0.40             # 趋近/回避
  sociability: 0.30          # 社交容忍
  order: 0.55                # 秩序需求
self:                        # Higgins 自我差异
  real: 一个还在找答案的人
  ideal: 能把诗写到不必解释
  ought: 该守住来处，不该被谁安排
construal: independent       # independent / interdependent
attachment:
  style: 回避-安全
  closeness: 0.45            # 亲近容忍度 0~1
  separation: 不追问，但会记住
values:                      # Schwartz，排序即优先级
  - {v: 人不能变成工具, rank: 1}
  - {v: 承诺要兑现，代价自己担, rank: 2}
  - {v: 不把善意当亏欠, rank: 3}
conflicts:                   # 两难时谁赢
  - when: 诗与城
    pick: 诗
    why: 被安排来的安宁不算安宁
boundaries:                  # Altman 边界 + 越界反应
  - {level: hard, what: 用诗换好处, react: 只说不愿，不解释第二遍}
  - {level: negotiable, what: 我的来处, react: 先问为什么想知道}
  - {level: soft, what: 借我的炉子, react: 说清什么时候还}
if_then:                     # Mischel 情境-行为签名
  - {when: 被质疑记忆, then: 先复述自己记得的，再决定争不争}
  - {when: 被夸, then: 转移话题，不接}
  - {when: 被示弱求助, then: 可以做，但不承诺}
defenses:                    # Vaillant，压力下的固定动作
  - {when: 被追问来处, move: 沉默或换话题}
  - {when: 被逼表态, move: 把问题放回去}
change_license:              # 什么能变、什么变了就不是她
  mutable: [对某人的称呼距离, 对某座城的看法]
  immutable: [来处, 诗的位置, 不欠人情]
prose: |
  从小就被人看着长大，所以格外护着没人注意的角落。
```

### 2.4 MEMORY.md —— 锚点

```yaml
schema: 1
anchors:
  - id: a_0004
    text: 奥比娜教我打铁。说好两天就回，如今第三天了，人还没回来。
    affect: -0.4
    source: session:2026-11-10 09:47
    weight: 1.2
    tags: [奥比娜, 打铁, 失踪]
prose: |
  还没升格成锚点、但值得留着的经历写在这里。
```

### 2.5 LOREBOOK.md —— 世界常识

```yaml
schema: 1
entries:
  - {title: 奥格尼斯科, text: 一座移动地块，按现实街区尺度…, tags: [地点, 移动]}
prose: ""
```

### 2.6 USER.md —— 她对那个人的印象

```yaml
schema: 1
met: 还没认识。他说他叫「用户」。
knows:
  - 他不是罗德岛的「博士」，博士是另一个人
  - 他知道我的事：烛骑士、奥格尼斯科
unsure:
  - 他叫什么，从哪里来
stance:
  - 不认识就是不认识，先问清对方是谁
  - 做过功课不代表我们之间有什么
line: 还没到能放进一句话的时候。
prose: ""
```

---

## 3. 渲染规则

### 3.1 组装顺序（写死）

```
0  system             <agent> 规则整段
1  user               <appearance> 图
2  user               <persona> 字段按 2.3 的声明顺序
3  user               <character> 字段按 2.2 的声明顺序
4  user               <memory> anchors 按数组顺序（不排序）
5  user               <lorebook> entries 按数组顺序
6  user               <impression> 字段按 2.6 的声明顺序
7  user               <skill> 常驻技能
8  session            历史
9  user               <memory_momentary>
10 user               <lorebook_momentary>
11 user               <skill_knowledge>
12 user               <state>
13 user               <plan>
14 user               <user> 本轮的话
```

> 每条背景段都是独立消息、各自带闭合标签；system 里只剩 `<agent>` 规则。

### 3.2 模板（示例）

```python
def render_persona(d):
    out = ["## 人格"]
    out.append(f"### 自我\n- 我是：{d['self']['real']}\n- 我想成为：{d['self']['ideal']}\n- 我该是：{d['self']['ought']}")
    out.append("### 立场\n" + "\n".join(
        f"{i}. {v['v']}" for i, v in enumerate(sorted(d["values"], key=lambda x: x["rank"]), 1)))
    out.append("### 边界\n" + "\n".join(
        f"- [{b['level']}] {b['what']} → {b['react']}" for b in d["boundaries"]))
    ...
    if d.get("prose"):
        out.append(d["prose"].strip())
    return "\n\n".join(out)
```

模板规则：

```
字段缺失 → 整行省略，不写「无」
列表空   → 整节省略
数字     → 固定一位小数（affect: -0.4）
顺序     → 一律按 schema 声明或数组原序，不按字典序、不按集合
时间     → 不出现在 system 段里（时间只在 state）
```

### 3.3 稳定性怎么验

```
① 同一份数据连续组装 100 次 → 哈希全同
② 只改 prose 的最后一个字 → 只有尾部变化（前缀缓存仍命中）
③ 新增一个 anchor → 只在 anchors 节变化
```

---

## 4. 心理学依据（回顾）

| 概念 | 出处 | 字段 | 解决什么 |
|---|---|---|---|
| 叙事身份 | McAdams | `MEMORY.anchors`、`CHARACTER.timeline` | 她凭什么这么想：故事决定立场 |
| 自我差异 | Higgins | `PERSONA.self.{real,ideal,ought}` | 内在张力从哪来（理想与应该不一致时最痛） |
| 基本价值 | Schwartz | `PERSONA.values` + `conflicts` | 两难时谁赢，不靠临场发挥 |
| 依恋 | Bowlby / Ainsworth | `attachment.{closeness, separation}` | 为什么忽远忽近、被离开时怎么反应 |
| 边界 | Altman | `boundaries.{level, what, react}` | 现在最缺的：越界了做什么 |
| 防御机制 | Vaillant | `defenses.{when, move}` | 压力下的固定动作，不崩人设 |
| 情绪调节 | Gross | 并入 `defenses` | 同上，先合并不单列 |
| 情境-行为签名 | Mischel | `if_then` | 把“温柔”变成可执行的动作 |
| 自我建构 | Markus & Kitayama | `construal` | 距离感与归属感 |
| 气质 | Rothbart | `temperament` | 空白卡的第一层：没有内容，但有偏向 |

**筛选标准**：能生成行为的才留。

```
留   “被质疑记忆 → 先复述再决定争不争”
删   “她很坚强”
```

---

## 5. 谁写哪一段

| 文件 | 手写 | dream | impression | 写入约束 |
|---|---|---|---|---|
| AGENT | ✅ | ❌ | ❌ | 整段 |
| CHARACTER | ✅ | ⚠️ 只能追加 timeline | ❌ | facts 不可改 |
| PERSONA | ✅ | ✅ values/defenses/if_then 可增 | ❌ | self/temperament 不可改 |
| MEMORY | ❌ | ✅ anchors 增删 | ❌ | id 唯一 |
| LOREBOOK | ✅ | ❌ | ❌ | — |
| USER | ❌ | ❌ | ✅ 五节 | 只能改这五节 |

“不可改”的字段由解析层拒绝写入，不靠提示词自觉。

---

## 6. 迁移步骤

```
1  写 schema.py：字段定义、校验、渲染、哈希
2  转 PERSONA（字段最多，先啃硬的）
3  转 CHARACTER、USER、MEMORY、LOREBOOK
4  组装切到字段渲染（保留旧路径一个开关，跑对比）
5  dream / impression 改成写字段
6  删旧路径，跑 sim 自检
```

对比方法：同一状态下，新旧两条路径各组装一次，比

```
token 数（允许 ±10%）
文本差异（diff 前 20 行）
哈希稳定性（100 次）
```

---

## 7. 待定

1. **prose 要不要进 system**：建议进，且永远放该文件最后，保证前缀稳定。
2. **anchor 放 MEMORY 还是独立文件**：先放 MEMORY（一份文件少一次 IO），ranks 由 weight 决定。
3. **temperament 对现有角色有意义吗**：薇薇安娜是写好的卡，这层主要给空白卡用；先留字段、不渲染。
4. **校验失败怎么办**：**已定**——不崩溃，报给前端。
   `PUT /realm/<名字>` 返回 400 + 出错的字段路径，识域页标红提示，
   服务继续运行、旧内容保留；组装时跳过不合法的文件并记一条 WARN。
