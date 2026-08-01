"""Creating a missing destination bucket from the settings page.

The behaviour worth pinning is not "it calls create_bucket" — it is the two things that decide
whether this feature is safe and whether it works at all:

  * the live data bucket must stay unreachable through it (creation is the one path that could
    bring such a destination INTO existence rather than merely reject it), and
  * `LocationConstraint` must be sent exactly when the provider expects it — AWS rejects it in
    us-east-1 and requires it everywhere else, and R2 signs with a literal "auto" that is not a
    location at all. Getting this wrong fails only against real providers, never in a mock, so the
    rule is asserted directly.
"""
import pytest
from botocore.exceptions import ClientError

from geodeploy.services import backup as bk


class Cfg:
    """Stands in for the SetupConfig row; the service only reads attributes."""

    def __init__(self, **kw):
        self.backup_endpoint = kw.get("endpoint", "https://s3.example.com")
        self.backup_bucket = kw.get("bucket", "gd-backups")
        self.backup_access_key = "AK"
        self.backup_secret_key = "SK"
        self.backup_region = kw.get("region", "us-east-1")
        self.backup_prefix = "geodeploy-backups"


class FakeS3:
    def __init__(self, *, create_error=None):
        self.created = []
        self._create_error = create_error

    def create_bucket(self, **kw):
        self.created.append(kw)
        if self._create_error:
            raise self._create_error

    # verify_destination's write probe — succeeding means "reachable and writable".
    def put_object(self, **kw):
        pass

    def delete_object(self, **kw):
        pass


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "CreateBucket")


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def make_client(endpoint, ak, sk, region):
        holder["region"] = region
        return holder["s3"]

    monkeypatch.setattr(bk, "make_client", make_client)
    holder["s3"] = FakeS3()
    return holder


def test_refuses_the_live_data_bucket(fake, monkeypatch):
    """The whole point of a backup is that it is somewhere else. Creation must refuse the live
    bucket for the same reason verify_destination does — more so, since it could otherwise be the
    thing that creates it."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: True)
    with pytest.raises(ValueError, match="live data"):
        bk.create_destination_bucket(Cfg())
    assert fake["s3"].created == []      # refused BEFORE touching the provider


def test_refuses_a_blank_bucket_name(fake, monkeypatch):
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    with pytest.raises(ValueError):
        bk.create_destination_bucket(Cfg(bucket="  "))
    assert fake["s3"].created == []


def test_location_constraint_is_omitted_for_us_east_1(fake, monkeypatch):
    """AWS returns InvalidLocationConstraint when us-east-1 is sent explicitly."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    bk.create_destination_bucket(Cfg(region="us-east-1"))
    assert "CreateBucketConfiguration" not in fake["s3"].created[0]


def test_location_constraint_is_omitted_for_r2(fake, monkeypatch):
    """R2's region is the literal string "auto" — a signing input, not a location."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    bk.create_destination_bucket(Cfg(region="auto"))
    assert "CreateBucketConfiguration" not in fake["s3"].created[0]


def test_location_constraint_is_sent_for_a_real_region(fake, monkeypatch):
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    bk.create_destination_bucket(Cfg(region="eu-central-1"))
    assert fake["s3"].created[0]["CreateBucketConfiguration"] == {
        "LocationConstraint": "eu-central-1"}


def test_an_existing_bucket_we_own_is_success(monkeypatch):
    """Idempotent on purpose: the operator asked for the bucket to EXIST, and a second click after a
    slow first one must not read as a failure."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    s3 = FakeS3(create_error=_client_error("BucketAlreadyOwnedByYou"))
    monkeypatch.setattr(bk, "make_client", lambda *a, **k: s3)
    assert bk.create_destination_bucket(Cfg())["ok"] is True


def test_access_denied_says_what_to_do(monkeypatch):
    """A key that can read and write objects often cannot create buckets. That is a common,
    recoverable situation, so it must not surface as a bare provider code."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    s3 = FakeS3(create_error=_client_error("AccessDenied"))
    monkeypatch.setattr(bk, "make_client", lambda *a, **k: s3)
    with pytest.raises(ValueError, match="cannot create buckets"):
        bk.create_destination_bucket(Cfg())


def test_creation_is_verified_not_assumed(monkeypatch):
    """create_bucket succeeding says nothing about whether this key may write INTO the bucket, and
    that is what a backup needs. The failure must propagate rather than report success."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    s3 = FakeS3()

    def put_denied(**kw):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject")

    s3.put_object = put_denied
    monkeypatch.setattr(bk, "make_client", lambda *a, **k: s3)
    with pytest.raises(ValueError):
        bk.create_destination_bucket(Cfg())


def test_missing_bucket_raises_the_offerable_error(monkeypatch):
    """The settings page keys its "Create it" button off this TYPE. A plain ValueError here would
    silently turn the button off, so the distinction is worth a test of its own."""
    monkeypatch.setattr(bk, "_same_as_live_data", lambda cfg: False)
    s3 = FakeS3()

    def put_missing(**kw):
        raise ClientError({"Error": {"Code": "NoSuchBucket", "Message": "nope"}}, "PutObject")

    s3.put_object = put_missing
    s3.list_buckets = lambda: {"Buckets": [{"Name": "something-else"}]}
    monkeypatch.setattr(bk, "make_client", lambda *a, **k: s3)

    with pytest.raises(bk.BucketMissing) as exc:
        bk.verify_destination(Cfg(bucket="gd-backups"))
    assert exc.value.bucket == "gd-backups"
    assert "something-else" in str(exc.value)      # still lists what the key CAN see
