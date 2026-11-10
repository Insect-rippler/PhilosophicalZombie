# lab —— 先做实验，不进引擎

这里的东西**不参与运行**：没有 import 进 `engine/`，不改 `state/`，跑挂了也不影响她。
等结论稳了，再决定要不要搬回主流程。

## 当前实验：锚点 / 立场的向量召回

想法：本地向量模型（`model/bge-small-zh-v1.5`，512 维）只在启动时慢，
之后单条 5~20ms。除了召回“发生了什么”，也可以召回**她是谁**——
锚点、立场、边界、承诺。

## 目录

```
lab/anchors/schema.md      字段定义（怎么算一条锚点）
lab/anchors/anchors.jsonl  种子数据（从薇薇安娜身上取）
lab/anchors/probe.md       探针问题：哪种问法应该召回哪一条
lab/anchors/retrieve.py    检索脚本（复用 model/memory_store.encode）
```

## 跑法

```
python lab/anchors/retrieve.py --stats
python lab/anchors/retrieve.py --search "有人坚持说我记错了" --top 5
python lab/anchors/retrieve.py --list --kind boundary
```

## 要验证的问题

1. **区分度**：相近但不同的立场（“不欠人情” vs “记账”）会不会混在一起？
2. **阈值**：锚点要不要绝对下限？现在记忆切片是 0.55、块是 0.40。
3. **多召回**：一次给 3 条还是 5 条？给多了会不会把上下文塞满。
4. **时效**：立场也会变，旧锚点要不要衰减或被 `supersedes`。
5. **反向锚点**：情绪强度高的多是冲突，容易长成只会记仇的人格，
   需要单独一套“正向锚点”的选取标准。

## 与主系统的关系

现在是**只读实验**。等薇薇安娜跑出足够数据，再回答两个问题：

- 哪些时刻她该想起某条锚点，实际却没想起（漏召回）
- 哪些时刻她想起的是一条不相干的（错召回）

有了这两组样本，才谈得上接进 `recall.py`。
