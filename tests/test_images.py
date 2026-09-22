from pathlib import Path

from PIL import Image

from image_pipeline import _prepare_without_crop
from image_resolver import _query_variants
from dataset import facts_for_date, load_all


def test_image_ratio_preserved_without_crop(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "output.jpg"
    with Image.new("RGB", (1200, 700), "white") as image:
        image.save(source)
    _prepare_without_crop(source, output)
    with Image.open(output) as result:
        assert abs((result.width / result.height) - (1200 / 700)) < 0.01


def test_fact_image_queries_are_contextual():
    fact = facts_for_date(load_all(), 10, 1)[0]
    queries = _query_variants(fact)
    assert queries
    assert any(fact.category.lower() in q.lower() for q in queries[:2])


def test_commons_candidate_selection_with_mocked_http(monkeypatch, tmp_path):
    import image_resolver

    fact = facts_for_date(load_all(), 10, 1)[0]
    destination = tmp_path / "source.bin"

    class FakeResponse:
        def __init__(self, payload=None, content=b"x" * 12000):
            self._payload = payload or {}
            self.content = content
            self.headers = {"content-type": "image/jpeg"}
            self.text = ""
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    payload = {"query": {"pages": {
        "1": {"title": "File:Moon photo.jpg", "imageinfo": [{
            "mime": "image/jpeg", "width": 1800, "height": 1200,
            "thumburl": "https://upload.wikimedia.org/moon.jpg",
            "extmetadata": {
                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                "Artist": {"value": "Test Artist"},
            },
        }]}
    }}}

    def fake_get(url, *args, **kwargs):
        if "w/api.php" in url:
            return FakeResponse(payload=payload)
        return FakeResponse()

    monkeypatch.setattr(image_resolver.requests, "get", fake_get)
    result = image_resolver._commons_search(fact, "Moon satellite", destination)
    assert result is not None
    assert result.score >= 5
    assert destination.exists()
