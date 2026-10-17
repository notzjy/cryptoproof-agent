"""无需安装 Agent 依赖的词典候选预览，不等同于真实 Agent。"""
from cryptoproof.knowledge import detect_known_typos, lexical_retrieve
from cryptoproof.editor import validate_edits, apply_approved

text = '本系统采用数子签名技术，并使用数字正书验证身份。'
proposals = validate_edits(text)
print('原文：', text)
print('检索依据：', [(x['source'], x['score']) for x in lexical_retrieve(text)])
for e in proposals:
    print(f'待确认 {e["id"]} 位置[{e["start"]}:{e["end"]}]: {e["original"]} -> {e["replacement"]}')
print('模拟全部人工同意后：', apply_approved(text, proposals, [p['id'] for p in proposals]))
print('注意：这里只展示非 LLM 的校对工具，真实 Agent 从 Web API 或 agent.py 中启动。')
