import io
import json

from bench.records import Row
from bench.store import RunStore

META = {"run_id": "a-20261009T120000Z-ab12", "agent": "a", "created_at": "2026-10-09T12:00:00Z"}
ROWS = [Row("a-20261009T120000Z-ab12", "a", "m1", "c1", 1, "s", "Hej æøå", None, None, 10, 1)]


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, Delimiter, ContinuationToken=None):
        prefixes = sorted({k.split("/")[0] + "/" + k.split("/")[1] + "/" for k in self.objects if k.startswith(Prefix)})
        return {"CommonPrefixes": [{"Prefix": p} for p in prefixes], "IsTruncated": False}


def test_local_round_trip(tmp_path):
    store = RunStore(local_root=tmp_path)
    locations = store.save(META, ROWS, "<html>r</html>")
    assert len(locations) == 3
    meta, rows = store.load(META["run_id"])
    assert meta == META and rows == ROWS
    assert store.list_runs()[0]["run_id"] == META["run_id"]


def test_s3_round_trip_and_listing(tmp_path):
    s3 = FakeS3()
    store = RunStore(local_root=tmp_path / "a", s3=s3, bucket="b")
    store.save(META, ROWS, "<html>r</html>")
    assert set(s3.objects) == {f"runs/{META['run_id']}/{n}" for n in ("run.json", "results.jsonl", "report.html")}
    other = RunStore(local_root=tmp_path / "b", s3=s3, bucket="b")  # another user, empty local cache
    meta, rows = other.load(META["run_id"])
    assert rows[0].output == "Hej æøå"
    assert [m["run_id"] for m in other.list_runs()] == [META["run_id"]]
    other.save_report(META["run_id"], "<html>new</html>")
    assert s3.objects[f"runs/{META['run_id']}/report.html"] == b"<html>new</html>"
    assert json.loads(s3.objects[f"runs/{META['run_id']}/run.json"])["agent"] == "a"
