# 验证记录（2026-10-17）

## 本环境实际执行并通过

- `python -m compileall -q cryptoproof scripts tests`：通过。
- `python -m pytest -q`：**11 passed, 1 skipped**。
- `node --check` 检查静态网页中的脚本：通过。
- `python -m scripts.evaluate`：10/10 小型规则样例吻合，10 TP、0 FP、0 FN。
- `python -m scripts.offline_preview`：词库检索、偏移、修改建议及人工批准后的文本修改逻辑通过。

## 本环境未能验证的部分

- LangGraph / LangChain 库未预装，执行 `pip install` 时网络 DNS 不可达，因此 `test_agent_integration.py` 由 `pytest.importorskip('langgraph')` 跳过。
- `AGENT_MODE=llm` 的模型接口调用需要用户自备有效 API Key，本环境未运行真实大模型调用。
- Docker 运行、PDF 扫描件 OCR、生产鉴权与并发性能均未验证或未实现。

**质量表述边界**：本包包含真实 LangGraph StateGraph、ToolNode、ChatOpenAI.bind_tools、SqliteSaver、interrupt/Command 等实现和针对它们的集成测试，但受依赖安装限制，不能宣称全链路已实测；本次仅完成代码层及不依赖该框架的功能验证。请在有网络的环境安装依赖后先运行 `pytest -q`，再设置真实模型 API Key 进行联调。
