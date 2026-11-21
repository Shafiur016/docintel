from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app


def build_client():
    engine = create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine)
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    return TestClient(app)


def auth(client, email):
    r = client.post('/auth/register', json={'email': email, 'password': 'secure-password-123'})
    assert r.status_code == 201, r.text
    return {'Authorization': f"Bearer {r.json()['access_token']}"}


def test_document_isolation_and_cited_responses():
    with build_client() as client:
        alice, bob = auth(client, 'alice@test.io'), auth(client, 'bob@test.io')
        r = client.post('/documents', headers=alice, files={'file': ('guide.txt', b'PostgreSQL supports transactions. Redis provides caching.', 'text/plain')})
        assert r.status_code == 202, r.text
        doc_id = r.json()['id']
        docs_a = client.get('/documents', headers=alice).json()
        assert docs_a[0]['status'] == 'ready'
        assert client.get('/documents', headers=bob).json() == []
        assert client.get(f'/documents/{doc_id}', headers=bob).status_code == 404
        result = client.post('/ask', headers=alice, json={'question': 'What supports transactions?', 'top_k': 3})
        assert result.status_code == 200, result.text
        assert result.json()['citations']
        assert 'PostgreSQL' in result.json()['answer']
        assert client.post('/ask', headers=bob, json={'question': 'transactions'}).json()['citations'] == []
        app.dependency_overrides.clear()


def test_unsupported_upload_and_file_limits():
    with build_client() as client:
        headers = auth(client, 'a@test.io')
        assert client.post('/documents', headers=headers, files={'file': ('bad.exe', b'malware', 'application/octet-stream')}).status_code == 415
        assert client.post('/documents', headers=headers, files={'file': ('empty.txt', b'', 'text/plain')}).status_code == 400
        app.dependency_overrides.clear()


def test_delete_is_owner_scoped_and_usage_private():
    with build_client() as client:
        alice, bob = auth(client, 'alice2@test.io'), auth(client, 'bob2@test.io')
        upload = client.post('/documents', headers=alice, files={'file': ('policy.txt', b'Redis caches frequently requested API results.', 'text/plain')})
        doc_id = upload.json()['id']
        assert client.delete(f'/documents/{doc_id}', headers=bob).status_code == 404
        assert client.post('/ask', headers=alice, json={'question': 'What caches API results?'}).status_code == 200
        assert client.get('/usage', headers=bob).json() == []
        assert len(client.get('/usage', headers=alice).json()) == 1
        assert client.delete(f'/documents/{doc_id}', headers=alice).status_code == 204
        assert client.post('/ask', headers=alice, json={'question': 'What caches API results?'}).json()['citations'] == []
        app.dependency_overrides.clear()
