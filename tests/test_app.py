from fastapi.testclient import TestClient

from conftest import seconds
from samplemark.app import create_app


def test_full_flow(make_manager, tmp_path):
    m = make_manager()
    client = TestClient(create_app(m))
    page = client.get("/")
    assert "Start session" in page.text
    assert "samplemark_client" in client.cookies
    page = client.post("/start", data={"title": "Alien", "year": "1979",
                                       "kind": "film", "notes": "", "position": ""})
    assert ">Mark<" in page.text
    m.session.buffer.write(seconds(10))
    assert "Tap Save when it ends" in client.post("/mark").text
    m.session.buffer.write(seconds(2))
    assert "Keep clip" in client.post("/end").text
    page = client.post("/finish", data={"text": "a line", "tags": "eerie, speaker=Ripley"})
    assert "Saved" in page.text
    wavs = list((tmp_path / "inbox").glob("*.wav"))
    assert len(wavs) == 1
    sidecar = wavs[0].with_suffix(".json").read_text()
    assert "marked_by" not in sidecar
    assert '"speaker=Ripley"' in sidecar
    assert "Start session" in client.post("/stop").text


def test_errors_are_shown(make_manager):
    client = TestClient(create_app(make_manager()))
    client.get("/")
    assert "No session running." in client.post("/mark").text
    client.post("/start", data={"title": "Alien"})
    assert "Not a position" in client.post("/sync", data={"position": "soon"}).text


def test_capture_down_offers_restart(make_manager):
    m = make_manager()
    client = TestClient(create_app(m))
    client.get("/")
    client.post("/start", data={"title": "Alien"})
    m.session.capture.stop()
    page = client.get("/")
    assert "DOWN" in page.text and "Restart capture" in page.text
    assert "running" in client.post("/restart").text
