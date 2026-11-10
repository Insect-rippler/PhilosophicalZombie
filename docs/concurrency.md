# 并发与快照：tick 和模型调用同时动文件时，谁该锁什么

结论先说：**写入已经串行化了，但读取没有快照。**
所以不会写坏文件，却可能读到“位置更新了、回执还没落”的中间态。

---

## 一、现在的锁

| 锁 | 位置 | 管什么 |
|---|---|---|
| `LOCK`（全局） | `server.py:44` | 一整轮 `/turn` 与一次 tick 互斥，维护线程也拿它 |
| `fsio` 每路径锁 | `engine/fsio.py` | 同一文件的并发写不互相覆盖（原子替换） |
| `_MAINTAIN_LOCK` | `server.py:705` | 防止两个维护任务同时跑 |
| `timers.tick_lock()` | 不是锁 | 情绪越界后的冷却轮数（名字有误导性） |

现状：

```
写：/turn 全程持 LOCK；tick 拿 LOCK；维护拿 LOCK
     → 同一时刻只有一个写者，文件不会写坏

读：context_assemble()、GET /state 直接读文件，不拿锁
     → 可能读到半更新状态
```

---

## 二、风险场景

### 场景 1：回执未落、位置已变

```
① 模型调 active
② body.commit 写 timers.json + character.json（位置已变）
③ server 把回执写进 session.jsonl
④ 在这两步之间，tick 不可能插进来（LOCK 挡着）
   但 GET /state 可以：它不拿锁，读到新位置 + 旧 session
```

对前端来说，就是“她人已经到了，气泡还没更新”。

### 场景 2：state 内部不一致

`render_state()` 一次要读好几个来源：

```
character.json   位置、生理
timers.json      手上占槽
session.jsonl    距上次行动（最后一条 assistant 的 ts）
```

三次独立读取之间，写者可以插进来，于是 state 里出现：

```
位置 = 矿坑底（刚更新）
手上 = []（旧，其实还在赶路）
距上次行动 = 2 分钟（旧 session 的 ts）
```

模型看到的就是一个**不可能同时成立的瞬间**。

### 场景 3：维护重写 session

维护会整体重写 `session.jsonl`（压缩）。现在靠 `LOCK` 和 `/turn` 排队，
这条没问题——**但前提是 `/turn` 全程持锁**，这点现在成立。

---

## 三、哪些文件需要「一致性」

| 文件 | 写者 | 读者 | 需要快照 |
|---|---|---|---|
| `realTime/character.json` | tick、active 工具 | 组装、/state、body | ✅ |
| `realTime/timers.json` | active 工具、tick | 组装、/state、body | ✅ |
| `session/session.jsonl` | 模型线、维护 | 组装、压缩、印象 | ✅（与上面同一瞬间） |
| `realTime/events.json` | tick、事件工具 | 组装 | ✅ |
| `plan/agenda.json` | plan 工具 | 组装、dream | ⚠️ 弱一致即可 |
| `media/self/appearance/*` | appearance 工具 | 组装、/state | ⚠️ 换装不频繁 |
| `file/hash.json` | FileAPI | FileAPI | ❌ 自带锁 |
| `realTime/maintenance.json` | 维护、tick | 维护 | ❌ 单写者 |
| `realTime/pending.json` | tick、工具 | tick | ❌ |
| `logs/` `usage/` | 追加 | 只读统计 | ❌ |

**核心只有四个**：character、timers、events、session —— 它们必须来自同一瞬间。

---

## 四、方案

### 4.1 不做全局读写锁

把 `LOCK` 换成读写锁，让 `GET /state` 也能读，看似简单，但：

```
/turn 持锁 10~20 秒（模型流式）
→ 期间的 /state 全部阻塞
→ 前端 6 秒一次的轮询直接超时，界面卡住
```

**不可取。**

### 4.2 快照 + 版本号（推荐）

```
写者：改完一组文件后，把版本号 +1（一个 int，存在内存里）
读者：读前取版本 → 读文件 → 再取版本
       两次一样 = 这段时间没被改过，数据是同一瞬间的
       不一样 = 重读（最多重试 3 次）
```

代价：读者可能重读两次，每次读文件是毫秒级，不影响体验。
写者不需要停住，模型调用期间读者照常读。

### 4.3 什么算「一组」

一次提交 = 一个快照边界：

```
active 工具   写 timers + character + 回执 → 完成后 +1
tick          写 character（+ events）→ 完成后 +1
维护          重写 session → 完成后 +1
换装          写 current.png + outfit.json → 完成后 +1
```

**回执和它对应的状态变更必须同一组。** 这样就不存在“位置变了、回执没落”的窗口。

### 4.4 快照 API

```python
# engine/snapshot.py
snapshot() -> {
    "at": 1794... ,              # 生成快照的虚拟/真实时间
    "version": 12,
    "character": {...},
    "timers": [...],
    "events": {...},
    "session_tail": [...],        # 最近 40 行，够算 lastActionAt / 去重
}
```

`render_state()`、`context_assemble()`、body 的判断都改成**只吃快照**，不再自己读文件。

好处不只是并发：state 里每一行都来自同一时刻，模型不会看到互相矛盾的字段。

---

## 五、要改的地方

| 文件 | 改动 |
|---|---|
| `engine/snapshot.py` | 新增：版本号、快照读取、重试 |
| `engine/timers.py` | `render_state/load_character` 支持传入快照 |
| `engine/core.py` | `context_assemble` 开头取一次快照，全程用它 |
| `engine/server.py` | 每次提交结束 `snapshot.bump()`；`/state` 走快照 |
| `engine/body.py` | `commit/observe` 用快照，不再自己 load |
| `engine/fsio.py` | 保持现状（文件级原子），快照是逻辑层的事 |

---

## 六、验证

| 编号 | 做法 | 判据 |
|---|---|---|
| C1 | 一边循环 `/turn`，一边每 0.2s 调 `/state` | 不出现「位置变了、手上还在旧动作」的响应 |
| C2 | tick 与 /turn 同时跑 100 次 | 不出 4096/文件损坏，快照版本单调 |
| C3 | 人为在写 timers 后 sleep 50ms 再写 session | 快照读到的两者要么都新、要么都旧 |
| C4 | 维护进行中持续轮询 /state | 不阻塞、不读到空 session（压缩中间态） |

C3 是这轮最重要的：它直接模拟“回执没到、位置变了”。

---

## 七、还要确认的一点

`/turn` 全程持 `LOCK` 意味着：**模型调用期间 tick 完全停摆**。

```
一次流式 10 秒 → 这 10 秒里 tick 不跑
影响：生理少推进 10 秒、计时器结算延后 10 秒
```

现在靠 `advance()` 下次补偿（按 lastUpdate 算），所以数值不会丢；
但如果以后事件要“整点触发”，就得考虑 tick 被长轮次挤掉的情况。

选项：

```
A  保持现状：模型调用期间不 tick，事后补偿（简单，数值无损）
B  tick 不等 LOCK：只结算计时器与生理，但写文件要快照版本支持
```

建议先 A，等事件改成整点判定后再看要不要 B。
