"""The setup wizard must name the cause, not echo the driver.

Every failure in the first screen of a new install used to read `Cannot connect to PostGIS: <raw
exception>`. Four unrelated problems looked identical there — a firewalled port, a closed one, wrong
credentials, and PostGIS missing from the target database — and only two of them have anything to do
with what the operator just typed. Someone hitting the first spends an hour on credentials, because
that is what "cannot connect" normally means.

These tests pin the DISTINCTIONS rather than exact wording: each message must point at the thing
that is actually wrong, and must not point at the things that are not.
"""
import asyncio

from geodeploy.services.setup_errors import postgres_error, storage_error


class _Boto(Exception):
    """botocore renders codes in parentheses: 'An error occurred (AccessDenied) when calling ...'"""


class EndpointConnectionError(_Boto):
    """Named to match botocore's real class, since the classifier keys on the TYPE NAME for the
    errors that carry no code. A separate class, not `_Boto` with its `__name__` reassigned — doing
    that mutates the shared class and every later test in the file inherits the wrong name."""


def test_timeout_is_not_reported_as_a_credentials_problem():
    """THE one from the field. A TCP timeout happens before any credential is examined, so a message
    mentioning passwords sends the operator to the wrong place entirely."""
    msg = postgres_error(asyncio.TimeoutError(), "10.0.0.5", 5435, "postgres", "postgres")
    assert "timed out" in msg
    assert "network problem, not a password" in msg
    assert "listen_addresses" in msg and "firewall" in msg
    assert "10.0.0.5:5435" in msg


def test_refused_is_distinguished_from_timed_out():
    """Refused means something answered. That is a different fix — the host is right and the port or
    the service is wrong — and conflating the two is how people end up editing firewall rules that
    were never the problem."""
    msg = postgres_error(ConnectionRefusedError("connection refused"), "10.0.0.5", 5432, "db", "u")
    assert "refused" in msg
    assert "firewall" not in msg


def test_missing_extension_says_it_is_per_database():
    """The commonest real mistake: pointing at a server that HAS PostGIS, but at a database where
    the extension was never created. Saying 'not installed' alone sends people to install packages
    that are already there."""
    msg = postgres_error(ValueError("PostGIS extension not installed on this database."),
                         "db.example.com", 5432, "postgres", "postgres")
    assert "per-database" in msg
    assert "CREATE EXTENSION postgis" in msg


def test_pg_hba_is_reported_as_progress_not_as_unreachable():
    msg = postgres_error(Exception('no pg_hba.conf entry for host "1.2.3.4"'), "h", 5432, "d", "u")
    assert "pg_hba.conf" in msg
    assert "reachable" in msg


def test_authentication_failure_names_the_user():
    msg = postgres_error(Exception("password authentication failed for user \"geo\""),
                         "h", 5432, "d", "geo")
    assert "geo" in msg and "rejected" in msg


def test_unknown_errors_still_carry_the_original_text():
    """A classifier that swallows what it cannot categorise is worse than none: the operator loses
    the one clue that would have told them something."""
    msg = postgres_error(Exception("something nobody predicted"), "h", 5432, "d", "u")
    assert "something nobody predicted" in msg


# ── storage ────────────────────────────────────────────────────────────────────────────────────

def test_unreachable_endpoint_does_not_blame_the_keys():
    e = EndpointConnectionError('Could not connect to the endpoint URL: "https://typo.example.com"')
    msg = storage_error(e, "https://typo.example.com", "b")
    assert "endpoint URL" in msg
    assert "never checked" in msg


def test_access_denied_is_about_the_bucket_not_the_keys():
    """'The keys work but may not touch this bucket' and 'the keys are wrong' need different fixes,
    and S3 reports both as a 403-shaped failure."""
    msg = storage_error(_Boto("An error occurred (AccessDenied) when calling the HeadBucket "
                              "operation"), "https://s3.example.com", "mybucket")
    assert "mybucket" in msg
    assert "PutObject" in msg          # names the permissions actually needed


def test_signature_error_mentions_region_as_a_cause():
    """A wrong region surfaces as a signature mismatch on providers that validate it, which reads as
    a wrong secret key and is the single most confusing S3 error there is."""
    msg = storage_error(_Boto("An error occurred (SignatureDoesNotMatch) when calling ..."),
                        "https://s3.example.com", "b")
    assert "region" in msg


def test_missing_bucket_says_it_could_not_be_created_either():
    msg = storage_error(_Boto("An error occurred (NoSuchBucket) when calling ..."),
                        "https://s3.example.com", "gone")
    assert "gone" in msg and "create" in msg.lower()


def test_wrong_region_endpoint_is_named():
    msg = storage_error(_Boto("An error occurred (PermanentRedirect) when calling ..."),
                        "https://s3.example.com", "b")
    assert "region" in msg


def test_bucket_creation_sends_location_constraint_only_where_it_is_legal():
    """AWS REQUIRES LocationConstraint outside us-east-1 and REJECTS it inside; R2's 'auto' is a
    signing input, not a location. Getting this wrong fails only against a real provider."""
    import inspect

    from geodeploy.services import minio as mn

    src = inspect.getsource(mn._ensure_bucket)
    assert "CreateBucketConfiguration" in src
    assert "us-east-1" in src and "auto" in src
