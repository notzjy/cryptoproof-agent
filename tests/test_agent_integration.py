"""pip 安装项目依赖后可运行。这里真正测试 LangGraph ToolNode / Interrupt / SQLite 检查点。"""
from pathlib import Path
import pytest
pytest.importorskip("langgraph")
from fastapi.testclient import TestClient
from cryptoproof.api import create_app


def test_full_human_review_resume(tmp_path: Path):
    app = create_app(data_dir=tmp_path, mode="demo")
    with TestClient(app) as client:
        assert client.get('/api/health').json()['agent'] == 'langgraph'
        data = client.post('/api/tasks', json={'text':'通过数子签名检查数字正书。'}).json()
        assert data['status'] == 'awaiting_review', data
        assert len(data['edits']) == 2
        assert any(e['type'] == 'tool_requested' for e in data['trace'])
        assert any(e['type'] == 'tool_returned' for e in data['trace'])
        assert client.get(f'/api/tasks/{data["task_id"]}').status_code == 200
        selected = [e['id'] for e in data['edits']]
        done = client.post(f'/api/tasks/{data["task_id"]}/approve',
                           json={'approved_ids':selected}).json()
        assert done['status'] == 'completed'
        assert done['corrected_text'] == '通过数字签名检查数字证书。'
        assert client.get(f'/api/tasks/{data["task_id"]}/download.docx').status_code == 200
        assert client.post(f'/api/tasks/{data["task_id"]}/approve',
                           json={'approved_ids':selected}).status_code == 409


def test_reject_all_and_no_edits(tmp_path: Path):
    app = create_app(data_dir=tmp_path, mode='demo')
    with TestClient(app) as client:
        task = client.post('/api/tasks', json={'text':'数子签名'}).json()
        done = client.post(f'/api/tasks/{task["task_id"]}/approve',
                           json={'approved_ids':[]}).json()
        assert done['corrected_text'] == '数子签名'
        clean = client.post('/api/tasks', json={'text':'数字签名'}).json()
        assert clean['status'] == 'completed'
        assert clean['edits'] == []
