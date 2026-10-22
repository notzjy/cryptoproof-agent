import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from cryptoproof.api import create_app
from cryptoproof.editor import parse_model_result
from cryptoproof.libraries import LibraryStore


def add_library(client, name="通信术语库", domain="通信技术", content="信道编码\n调制解调\n信道编码\n"):
    return client.post('/api/libraries', data={'name': name, 'domain': domain},
                       files={'file': ('terms.txt', content.encode('utf-8'), 'text/plain')})


def test_library_crud_deduplication_and_persistence(tmp_path):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        assert len(client.get('/api/libraries').json()['libraries']) == 1
        response = add_library(client)
        assert response.status_code == 201, response.text
        created = response.json()
        assert created['term_count'] == 2 and created['preview'] == ['信道编码', '调制解调']
        assert add_library(client).status_code == 422
        assert len(client.get('/api/libraries').json()['libraries']) == 2
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        assert len(client.get('/api/libraries').json()['libraries']) == 2
        assert client.delete('/api/libraries/' + created['id']).status_code == 200
        assert client.delete('/api/libraries/' + created['id']).status_code == 404
        assert len(client.get('/api/libraries').json()['libraries']) == 1


def test_selected_library_search_never_mixes_domains(tmp_path):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        comm = add_library(client).json()
        security = add_library(client, '网安术语库', '网络安全', '入侵检测\n访问控制\n').json()
        def hits(library_id, query):
            return client.post('/api/knowledge/search', json={'library_id': library_id, 'query': query}).json()['results']
        assert {row['term'] for row in hits(comm['id'], '信道编马')} == {'信道编码'}
        assert hits(security['id'], '信道编马') == []
        assert {row['term'] for row in hits(security['id'], '入侵检侧')} == {'入侵检测'}


def test_delete_all_stays_empty_after_restart_and_requires_import(tmp_path):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        initial = client.get('/api/libraries').json()['libraries'][0]
        assert client.delete('/api/libraries/' + initial['id']).status_code == 200
        assert client.get('/api/libraries').json()['libraries'] == []
        assert client.get('/api/health').json()['term_count'] == 0
        assert client.post('/api/tasks', json={'text': '检查文字'}).status_code == 409
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        assert client.get('/api/libraries').json()['libraries'] == []
        assert add_library(client).status_code == 201


@pytest.mark.parametrize(('filename', 'raw'), [('bad.csv', b'abc'), ('bad.txt', b'\xff'),
                                               ('empty.txt', b'\n '), ('long.txt', ('词' * 121).encode())])
def test_invalid_import_is_rejected_without_creating_library(tmp_path, filename, raw):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        response = client.post('/api/libraries', data={'name': '测试库'}, files={'file': (filename, raw)})
        assert response.status_code == 422
        assert len(client.get('/api/libraries').json()['libraries']) == 1


def test_oversized_file_and_blank_name_rejected(tmp_path):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        response = client.post('/api/libraries', data={'name': '大文件'}, files={'file': ('big.txt', b'a' * (1024 * 1024 + 1))})
        assert response.status_code == 413
        assert add_library(client, name='  ').status_code == 422


def test_snapshot_survives_deletion_and_unknown_selection_is_not_defaulted(tmp_path):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        library = client.get('/api/libraries').json()['libraries'][0]
        task = client.post('/api/tasks', json={'text': '数子签名', 'library_id': library['id']}).json()
        assert task['library']['id'] == library['id']
        client.delete('/api/libraries/' + library['id'])
        assert client.post('/api/tasks', json={'text': '测试', 'library_id': library['id']}).status_code == 409
        result = client.post(f'/api/tasks/{task["task_id"]}/approve', json={'approved_ids': [task['edits'][0]['id']]}).json()
        assert result['corrected_text'] == '数字签名'
        assert result['library']['name'] == library['name']


def test_model_uses_selected_domain_and_task_snapshot(tmp_path, monkeypatch):
    seen = []
    class Backend:
        def bind_tools(self, tools):
            class Main:
                def invoke(self, messages):
                    if not any(getattr(m, 'type', '') == 'tool' for m in messages):
                        assert '通信技术' in messages[0].content
                        return AIMessage(content='', tool_calls=[{'name': 'review_domain_term', 'args': {'suspected_text': '信道编马'},
                            'id': 'communication-call', 'type': 'tool_call'}])
                    payload = parse_model_result(messages[-1].content)
                    assert payload['library']['domain'] == '通信技术'
                    return AIMessage(content=json.dumps({'edits': [payload['proposed_edit']], 'summary': '术语已核实'}))
            return Main()
        def invoke(self, messages):
            payload = json.loads(messages[-1].content)
            assert payload['library']['domain'] == '通信技术'
            if 'candidate' in payload:
                seen.append(payload['candidate']['term'])
                result = {'grammar': 'good', 'domain_fit': True, 'meaning_preserved': True,
                          'improves_original': True, 'reason': '通信技术语境匹配'}
            elif 'evaluations' in payload:
                result = {'selected_term_id': payload['evaluations'][0]['term_id'], 'reason': '通信术语匹配'}
            else:
                result = {'edits': payload['edits'], 'summary': '组合复核完成'}
            return AIMessage(content=json.dumps(result))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-no-network')
    monkeypatch.setattr('cryptoproof.agent.ChatOpenAI', lambda **kwargs: Backend())
    with TestClient(create_app(tmp_path, mode='llm')) as client:
        library = add_library(client).json()
        task = client.post('/api/tasks', json={'text': '系统采用信道编马。', 'library_id': library['id']}).json()
        assert task['analysis_complete'], task
        assert seen == ['信道编码']
        assert task['edits'][0]['replacement'] == '信道编码'
        assert task['library']['id'] == library['id']
        client.delete('/api/libraries/' + library['id'])
        result = client.post(f'/api/tasks/{task["task_id"]}/approve', json={'approved_ids': [task['edits'][0]['id']]}).json()
        assert result['corrected_text'] == '系统采用信道编码。'


def test_document_upload_carries_selected_library(tmp_path):
    with TestClient(create_app(tmp_path, mode='demo')) as client:
        library = add_library(client).json()
        response = client.post('/api/tasks/upload', data={'library_id': library['id']},
                               files={'file': ('document.txt', '系统采用信道编码。'.encode('utf-8'))})
        assert response.status_code == 200, response.text
        assert response.json()['library']['id'] == library['id']


def test_changing_library_does_not_mutate_previous_snapshot(tmp_path):
    store = LibraryStore(tmp_path)
    first = store.snapshot()
    other = store.add('通信术语库', '通信技术', 'terms.txt', '信道编码\n'.encode())
    second = store.snapshot(other['id'])
    assert first['id'] != second['id']
    assert second['terms'][0]['term'] == '信道编码'
    assert first['terms'] != second['terms']
