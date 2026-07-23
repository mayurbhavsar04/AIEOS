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
