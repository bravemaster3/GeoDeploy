"""Turning a driver exception into something the operator can act on.

The setup wizard is the first thing anyone runs, and until this module existed it surfaced the raw
exception: `Cannot connect to PostGIS: <asyncpg text>`. Four completely different problems arrived
looking the same —

    the port is firewalled          → fix the firewall, or listen_addresses
    nothing is listening there      → check the port, or that the service is up
    the password is wrong           → fix the credentials
    PostGIS is not in that database → run CREATE EXTENSION

— and only the last two have anything to do with what the operator just typed. Someone hitting the
first one goes hunting through credentials because that is what a connection error usually means.

Each classifier returns a sentence naming the CAUSE and the next command to run. The original
exception is always appended, because a message that hides the underlying error is worse than a
verbose one when the guess is wrong.
"""
from __future__ import annotations

import re


def _detail(exc: Exception) -> str:
    text = str(exc).strip()
    return f" (the database said: {text})" if text else ""


def postgres_error(exc: Exception, host: str, port: int, db: str, user: str) -> str:
    """A sentence for a failed external-PostGIS connection."""
    name = type(exc).__name__
    text = str(exc).lower()
    where = f"{host}:{port}"

    # TIMEOUT — the packets went nowhere. Distinct from a refusal, and the distinction is the whole
    # diagnosis: something dropped them, so nothing about the credentials has been tested yet.
    if name in ("TimeoutError", "ConnectionTimeoutError") or "timed out" in text:
        return (
            f"No response from {where} — the connection timed out, so nothing answered at all. "
            f"This is a network problem, not a password one: the credentials were never checked. "
            f"Check that PostgreSQL listens on a public address (`listen_addresses = '*'`, then "
            f"`ss -lntp | grep {port}` on that server), and that no firewall — including your "
            f"provider's — blocks port {port} from this machine.")

    # REFUSED — something answered and said no. The host is right, the port is wrong or closed.
    if "refused" in text or name == "ConnectionRefusedError":
        return (
            f"{where} refused the connection. The host is reachable, so this is the port or the "
            f"service: check PostgreSQL is running and that it is really on port {port}.")

    if "does not exist" in text and "database" in text:
        return (f'The server at {where} has no database named "{db}". Create it, or point '
                f'GeoDeploy at an existing one.' + _detail(exc))

    if "password authentication failed" in text or "authentication" in text:
        return (f'The server at {where} rejected the credentials for user "{user}".' + _detail(exc))

    # pg_hba is a REJECTION, not a network failure — the operator has got further than they think.
    if "pg_hba" in text or "no encryption" in text:
        return (
            f"{where} is reachable but refuses connections from this server. Add a rule for this "
            f"machine's IP to `pg_hba.conf` on the database server and reload it "
            f"(`SELECT pg_reload_conf();`)." + _detail(exc))

    if "postgis" in text and ("not installed" in text or "does not exist" in text):
        return (
            f'Connected to "{db}" at {where}, but PostGIS is not enabled IN THAT DATABASE. '
            f"Extensions are per-database, not per-server: connect to it and run "
            f"`CREATE EXTENSION postgis;`. If that fails, PostGIS is not installed on the server.")

    if "ssl" in text:
        return (f"{where} rejected the SSL settings. Check whether the server requires or forbids "
                f"SSL, and set POSTGIS_SSLMODE to match." + _detail(exc))

    return f"Could not connect to PostgreSQL at {where}." + _detail(exc)


def storage_error(exc: Exception, endpoint: str, bucket: str) -> str:
    """A sentence for a failed external object-storage connection."""
    name = type(exc).__name__
    text = str(exc)
    lower = text.lower()
    where = endpoint or "AWS S3"
    code = ""
    m = re.search(r"\((\w+)\)", text)           # botocore renders "... (AccessDenied) when ..."
    if m:
        code = m.group(1)

    if name in ("EndpointConnectionError", "ConnectTimeoutError") or "could not connect" in lower \
            or "timed out" in lower:
        return (
            f"No response from {where}. Nothing answered, so the credentials were never checked — "
            f"verify the endpoint URL (it needs the scheme, e.g. `https://s3.eu-central-1."
            f"wasabisys.com`) and that this server can reach it.")

    if "name or service not known" in lower or name == "gaierror" or "dns" in lower:
        return f"The endpoint host in {where} could not be resolved. Check the URL for a typo."

    if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch") or "invalid access" in lower \
            or "signaturedoesnotmatch" in lower:
        return (
            f"{where} rejected the credentials ({code or 'signature mismatch'}). Check the access "
            f"and secret keys. On a provider that validates the region — AWS does, most others do "
            f"not — a wrong region also surfaces as a signature error, so leave Region blank "
            f"unless your provider insists.")

    if code in ("AccessDenied", "403") or "access denied" in lower:
        return (
            f'The credentials work at {where}, but are not allowed on bucket "{bucket}". The key '
            f"needs PutObject, GetObject, ListBucket and DeleteObject on it — and CreateBucket too "
            f"if the bucket does not exist yet.")

    if code in ("NoSuchBucket", "404"):
        return (f'{where} has no bucket named "{bucket}", and it could not be created with these '
                f"credentials. Create it, or use a key permitted to create buckets.")

    if code in ("PermanentRedirect", "AuthorizationHeaderMalformed") or "region" in lower:
        return (
            f'Bucket "{bucket}" is not in the region this endpoint serves. On location-scoped '
            f"providers a bucket is reachable only through its own region's endpoint — check which "
            f"region it was created in." + _detail(exc))

    return f"Could not use bucket \"{bucket}\" at {where}." + _detail(exc)
