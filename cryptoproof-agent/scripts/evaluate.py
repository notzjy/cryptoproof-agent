"""仅评估词库基线，不将其误称为真实 LLM/完整校对模型成绩。"""
import json
from pathlib import Path
from cryptoproof.knowledge import detect_known_typos

path = Path(__file__).resolve().parent.parent / 'knowledge' / 'evaluation.json'
samples = json.loads(path.read_text(encoding='utf-8'))
tp = fp = fn = 0
for case in samples:
    predicted = {f'{e["original"]}->{e["replacement"]}' for e in detect_known_typos(case['text'])}
    expected = set(case['expected'])
    tp += len(predicted & expected)
    fp += len(predicted - expected)
    fn += len(expected - predicted)
    print(('PASS' if predicted == expected else 'FAIL'), case['text'])
precision = tp / (tp+fp) if (tp+fp) else 0
recall = tp / (tp+fn) if (tp+fn) else 0
print(f'Rule baseline only | TP={tp} FP={fp} FN={fn} | precision={precision:.3f} recall={recall:.3f}')
print('提醒：此数据为少量自建示例题，并非独立测试集或生产评测。')
