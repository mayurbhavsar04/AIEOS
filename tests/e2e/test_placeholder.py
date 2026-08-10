from fastapi.testclient import TestClient

from aieos_api.main import app


def test_reference_host_executes_hello_aieos_workflow() -> None:
    with TestClient(app) as client:
        response = client.post("/reference/hello", json={"message": "host smoke"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Succeeded"
    assert body["outcome"] == "Success"
    assert body["value"] == "Hello from AIEOS: host smoke"


def test_reference_host_exposes_incremental_ai_stream() -> None:
    with (
        TestClient(app) as client,
        client.stream(
            "POST",
            "/reference/ai",
            json={"prompt": "stream safely", "idempotency_key": "host-stream-1", "stream": True},
        ) as response,
    ):
        events = [line for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert '"kind": "acknowledgement"' in events[0]
    assert any('"kind": "content_delta"' in event for event in events[1:-1])
    assert '"kind": "terminal"' in events[-1]
