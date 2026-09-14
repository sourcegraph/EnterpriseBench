# Dependency Trace: TLS Certificate Verification from git through curl to OpenSSL

## Context

When `git clone` fails with a TLS certificate error, the error actually
originates in OpenSSL, passes through libcurl, and is reported by git. The
full verification chain crosses three codebases, each with its own
configuration points that affect certificate trust decisions.

Understanding this chain is critical for diagnosing corporate proxy/CA issues
where custom certificates need to be trusted.

## Task

Trace the TLS certificate verification path from git's HTTPS transport through
curl's OpenSSL backend to OpenSSL's X509 verification, documenting every
configuration point.

## Repos in Workspace

- `/workspace/git/` -- git v2.41.0 (consumer)
- `/workspace/curl/` -- curl 8.1.2 (intermediary)
- `/workspace/openssl/` -- OpenSSL 3.1.1 (upstream TLS library)

## Expected Output

Write `/workspace/DEPENDENCY_TRACE.md` containing:

1. Git's SSL configuration entry points (the config keys and environment
   variables that control CA trust, and where in git's source they are read)
2. How git configures curl for TLS (the libcurl API calls git uses to pass
   CA/verification options down to the transport library)
3. Curl's OpenSSL backend integration (which source file bridges libcurl's
   generic TLS layer to OpenSSL, and how it wires up certificate verification)
4. OpenSSL's X509 certificate chain verification code path
5. All configuration knobs across the three repos that affect verification
