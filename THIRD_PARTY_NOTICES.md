# Third-party notices

## deepseek-harness-quant

QTrade 的桥接行为参考并复用 `deepseek-harness-quant` 的代码接口和页面/API 约定。该底座位于 `third_party/deepseek-harness-quant`，属于独立的 upstream external source；本仓库不修改、打包或提交其目录。

用户需自行合规提供外部底座及其数据，并自行承担相应许可、来源和使用限制；本归属说明不构成 DeepSeek HARNESS Quant、DSHQuant 或其作者对 QTrade 的背书。

The upstream project is distributed under the MIT License:

```text
MIT License

Copyright (c) 2026 DeepSeek HARNESS Quant (DSHQuant)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Reuse scope and data boundary

- Reuse is limited to compatible code-level behavior: dynamic page/API integration, the `deck/` layout, live/proxy route conventions, and the adaptation contract documented in `docs/DEEPSEEK_ADAPTATION_STANDARD.md`.
- QTrade-owned adapter code remains separately maintained and must preserve its documented fallback behavior.
- This repository and this adaptation layer do not include third-party market data, real market-data CSV files, databases, logs, caches, model artifacts, or generated snapshots.
- The contract tests use only small synthetic OHLCV values and temporary fake pages; they do not copy or redistribute upstream data assets.
- Users must provide any external runtime base and data lawfully and independently; this notice does not imply endorsement by the upstream project or its authors.
