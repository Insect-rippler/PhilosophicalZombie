# memory_vectors.npz

`numpy.savez` 存的向量表，`slices.json` 和 `blocks.json` 共用。仓库里只保留这份说明，`.npz` 本身不入库。

```python
import numpy as np

z = np.load("ConsciousnessRealm/embedding/memory_vectors.npz")
z["vecs"]     # (N, 512) float32，每行一条切片或记忆块
z["ids"]      # (N,) <U16，对应的 s-… / b-… id
z["hashes"]   # (N,) <U16，文本哈希，用来判断要不要重算
```

| 数组 | 说明 |
|---|---|
| `vecs` | 归一化后的向量，检索用点积 |
| `ids` | 与 `slices.json` / `blocks.json` 的 `items[].id` 对应 |
| `hashes` | 原文哈希；文本没变就不重算向量 |

行数 = 切片数 + 记忆块数；改文本、换模型或删条目后需要重建（设置页的 `reindex` 任务，或 `python model\memory_store.py ...`）。
文件缺失时检索会报“记忆库不可用”，会话仍能继续，但没有长期记忆召回。
