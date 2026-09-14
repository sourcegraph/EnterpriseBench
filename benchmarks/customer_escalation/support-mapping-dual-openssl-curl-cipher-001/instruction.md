# Map customer curl HTTPS handshake failure after openssl 3.0 upgrade to the libcurl ossl backend / openssl provider security-level boundary

A customer reports that after their distro upgraded openssl from
1.1.1 to 3.0, curl HTTPS calls to a partner endpoint suddenly fail
with 'no cipher match' even though the partner supports modern
ciphers. openssl s_client succeeds against the same host. The
customer's curl was rebuilt against the new openssl.

Your task:
1. Find where libcurl's openssl backend (vtls/openssl.c) constructs
   the SSL_CTX and applies cipher and protocol version constraints.
2. Find where openssl 3.0's provider model evaluates the security
   level (default 1 in 1.1.1, default 2 in 3.0) and which ciphers
   it filters.
3. Trace the cross-repo path: curl handle -> Curl_ossl_connect ->
   SSL_CTX_set_cipher_list -> openssl provider init -> security    level check -> cipher_filter callbacks. Cite each file and the
   function or attribute responsible.
4. Identify why openssl 3.0's default security level rejects RSA
   key exchange ciphers and SHA1 signatures that 1.1.1 accepted,
   and how libcurl's backend does not surface the level mismatch
   except as 'no cipher match'.
