# 上下文的可信任权重

先确认现状，再谈要不要改。**这一轮不改代码。**

---

## 一、现在实际是怎么排的

组装后是一条 system + 一串消息（实测 12,989 字的 system）：

```
system   PERSONA → AGENT → CHARACTER → MEMORY → LOREBOOK → <impression>
user     appearance 图
user     session 历史（user / assistant / tool 混排）
user     <memory_momentary>   检索块
user     <state>
user     <plan>
user     [INFO] 自主回合提示
```

AGENT 里写着的规则有三处：

```
可信度：system > tool > user > 背景资料
状态：<state> 全部以它为准
一句话：底色不动，事实优先于打算，打算优先于回忆
```

## 二、现状里的三处错位

### 1. `<impression>` 在 system 里，但它是“可能不准”的

```
位置     system（最高传输层）
内容     她对那个人的主观判断
AGENT    自己写着「主观、会变、可能不准」
```

按「system > user」这条，印象会压过 `memory` 和 `state`，**但它恰恰是最容易错的一层**。

### 2. `<state>` 是 user 角色，但被赋予一票否决

```
位置     user 消息
规则     「时间、位置、身体、手上的事，全部以它为准」
```

一条普通 user 消息却拥有最高事实权——规则与传输层不一致。
模型要同时相信“system 最可信”和“state 最可信”，只能靠上下文自行调和。

### 3. MEMORY 出现在两个信任层

```
MEMORY.md           system 里常驻（整段）
memory_momentary    user 里按需召回（片段）
```

同一份“她记得的事”，优先级取决于它是常驻还是被召回。

### 附：一处历史残留

第 543 条 assistant 里还留着抄进去的 `[INFO]`（模型自己吐的），
说明这类内容一旦进 history 会一直留在前缀里。

---

## 三、问题不在“顺序”，在“两个维度被混为一谈”

```
传输层    system / user / tool     决定模型多看重它
认识层    事实 / 设定 / 记忆 / 判断  决定它本身可不可靠
```

现在两者被当成一回事：越靠前的越可信。但实际情况是交叉的：

| 内容 | 传输层 | 认识层 | 谁写的 |
|---|---|---|---|
| 工具回执 | tool | **事实**（执行结果） | 代码 |
| `state` | user | **事实**（此刻） | 代码 |
| `persona` / `character` | system | **设定**（不可证伪） | 人 |
| `memory` | system / user | **记忆**（可能记错） | dream 整理 |
| `lorebook` | system / user | **常识**（世界规则） | 人 |
| `<impression>` | system | **判断**（主观、会错） | 印象流程 |
| `user` 的话 | user | **说法**（可能有假） | 对方 |
| `plan` | user | **打算**（会变） | 她/工具 |

---

## 四、建议改成“按问题定优先级”

不再给全局排序，而是按模型要回答的问题给：

```
此刻能不能做 / 在哪 / 身体如何
    state（代码）> 工具回执 > 其他

发生过什么
    工具回执与记录 > memory > 对方说法 > impression

她是谁 / 她怎么想
    persona + character（不可被任何对话推翻）

世界怎么运作
    lorebook > 常识推断

对方是什么人
    对方的话（事实部分）+ impression（判断部分，允许错）
```

配一句话写进 AGENT：

> **位置靠前不代表更可信。** 每类信息只在自己的问题上说了算。

---

## 五、要不要动结构（三个选项）

| 选项 | 做法 | 代价 |
|---|---|---|
| A | 只改规则措辞，位置不动 | 零成本；但 system 里放判断层，模型仍可能高估 |
| B | 把 `<impression>` 移出 system，放到 session 之后的 user 块 | 结构更诚实；会动前缀缓存 |
| C | 给每块加一行可信度标签（“这是判断，可能错”） | 每块多几字，稳定但不优雅 |

我倾向 **A + C 各取一点**：

```
规则里写清「位置≠可信度」
impression 块开头加一句「这是我的判断，可能记错」
state 块开头加一句「这是此刻的事实，优先于记忆」
```

不动结构，只补两行提示，前缀仍然稳定。

---

## 六、待确认

1. 这套“按问题定优先级”的表，认同吗？
2. `<impression>` 要不要真挪出 system（选项 B）？
3. 校验失败报前端的处理，确认成：**PUT /realm 返回 400 + 字段路径，页面标红，服务不退出、旧内容保留**。

---

## 六、结构方案（已定）

固定的背景段从 system 里拆出来，**和图片一样放在 system 与 session 之间**，
每段一条独立消息，用成对的开始/结束标签包住。

### 新的消息顺序

```
[0]  system        <agent> 规则 </agent>              ← 只剩规则
[1]  user          <appearance> 图 </appearance>      ← 图片
[2]  user          <persona>…</persona>
[3]  user          <character>…</character>
[4]  user          <memory>…</memory>
[5]  user          <lorebook>…</lorebook>
[6]  user          <impression>…</impression>
[7]  session       历史（user / assistant / tool）
[8]  user          <memory_momentary>…</memory_momentary>
[9]  user          <lorebook_momentary>…</lorebook_momentary>
[10] user          <skill_knowledge>…</skill_knowledge>
[11] user          <state>…</state>
[12] user          <plan>…</plan>
[13] user          <user> 本轮的话 </user>
```

### 标签规范

```
一律成对闭合：<persona>…</persona>
标签名只用小写英文与下划线
一条消息只放一个标签，不嵌套
标签内的第一个换行留给内容，不留空行
```

常驻段与检索段用**不同后缀**区分：

```
<persona> <character> <memory> <lorebook> <impression>   常驻
<memory_momentary> <lorebook_momentary> <skill_knowledge> 按需检索
```

### 为什么这样拆

```
1  每段独立成消息 → 改一段只影响那一段，不再把整块 system 顶掉
2  标签自己说明类型 → 模型不必靠位置猜哪段是什么
3  system 只剩规则 → 规则短、稳定，缓存命中率更高
4  与可信度规则对齐 → 标签能直接写上「这是判断，可能错」
```

代价：

```
每条消息有固定开销（几个 token），十几段约 50~80 token
前缀仍然从 system 开始逐条匹配，顺序必须写死
```

### 标签与可信度的对应

| 标签 | 类型 | 冲突时 |
|---|---|---|
| `<agent>` | 规则 | 最高，不可被对话推翻 |
| `<persona>` `<character>` | 设定 | 不可被对话推翻 |
| `<memory>` | 记忆 | 与对方说法冲突时以记忆为准 |
| `<lorebook>` | 常识 | 世界规则优先于临时说法 |
| `<impression>` | 判断 | 允许错，被事实纠正 |
| `<state>` | 此刻事实 | 时间/位置/身体以它为准 |
| `<plan>` | 打算 | 与 state 冲突时顺延 |
| `<user>` | 对方的话 | 只代表对方说了什么，不代表事实 |
| `<appearance>` | 形象 | 参考，不描述 |

一句话：**标签说类型，类型决定可信度；位置只决定读取顺序。**
