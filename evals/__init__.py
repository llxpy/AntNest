# -*- coding: utf-8 -*-
"""evals/ 包的 __init__。

评测基础设施本身不参与 AntNest 运行时，只在开发/CI 里跑。
显式空实现是为了让 `python -m evals.run` 不会误触发包级副作用。
"""
