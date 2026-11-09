# 模拟器

不改角色代码，直接跑这个目录下的脚本，观察角色行为是否合你的预期。
所有脚本都跑在隔离的数据目录 `state/sim`，不会动真实存档。

## 第一次用

```bash
python sim/run.py 24      # 跑 24 小时完整模拟（会调用模型）
```

跑完看终端输出，或在 `state/sim/tmp/trace-*.jsonl` 里查细节。

## 五个工具

| 脚本 | 用途 | 要不要模型 |
|---|---|---|
| `sim/run.py <小时>` | 完整跑一段时间，看作息、动作、台词 | 要 |
| `sim/check.py` | 全功能自检（工具、槽位、情绪、睡眠、闹钟、伤害） | 少量 |
| `sim/phys.py` | 只跑生理数值，不调模型，验证一天的曲线 | 不要 |
| `sim/sleep.py` | 睡眠专项：不同困意下多久入睡、睡多久、醒来反应 | 要 |
| `sim/say.py "一句话"` | 手动递一轮对话，看她的反应和 reasoning | 要 |

`sim/say.py --reset` 重置状态，`--nofreeze` 关闭情绪冻结。

## 调整角色卡

1. 改 `ConsciousnessRealm/CHARACTER.md`（她是谁）
2. 改 `ConsciousnessRealm/PERSONA.md`（她怎么想、边界在哪）
3. 改 `ConsciousnessRealm/AGENT.md`（系统层规则，与角色无关）
4. 改 `ConsciousnessRealm/body/metabolism.json`（生理分段、系数、范围）

改完先跑 `sim/check.py` 确认没把系统改坏，再用 `sim/run.py 24` 看长期行为。

## 起始状态

`sim/run.py` 里的 `reset()` 决定模拟从谁、在哪、几点、什么状态开始：

```python
def reset(start_hour=6, start_minute=30):
    ch["local"] = "奥格尼斯科/她的房间"
    "metabolism": {"sleepiness": 12, "fatigue": 20, ...}
```

换角色时把这里的地点、时间、初始生理一起改掉。

## 数据隔离

模拟器通过 `PZ_DATA` 把数据目录指向 `state/sim`。
向量库在 `ConsciousnessRealm/embedding/`，是共享的——跑测试前建议先备份：

```bash
cp -r ConsciousnessRealm/embedding state/tmp/embedding_backup
```
