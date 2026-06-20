from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "eduvision-ai"


def test_geometry_answer_is_accessible() -> None:
    response = client.post(
        "/ask",
        json={
            "student_id": "S001",
            "subject": "geometry",
            "question": "I do not understand an isosceles triangle.",
            "language": "en",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    answer = payload["answer"].lower()
    serialized_payload = str(payload).lower()
    assert "imagine" in answer or "touch" in answer
    assert "look at the figure" not in serialized_payload
    assert "you can see" not in serialized_payload
    assert "as shown in the figure" not in serialized_payload


def test_english_correction() -> None:
    response = client.post(
        "/command",
        json={"student_id": "S001", "message": "/english I have many meeting today", "language": "en"},
    )
    assert response.status_code == 200
    assert "many meetings today" in response.json()["answer"]


def test_study_plan() -> None:
    response = client.post(
        "/study-plan",
        json={"student_id": "S001", "grade": "Grade 8", "weakness": "geometry", "available_time": "25 minutes per day"},
    )
    assert response.status_code == 200
    assert len(response.json()["weekly_plan"]) == 7
