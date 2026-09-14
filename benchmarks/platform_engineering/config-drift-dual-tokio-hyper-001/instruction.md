# Detect configuration drift between tokio runtime thread pool defaults and hyper server connection handling assumptions

A platform team reports that their hyper-based HTTP server occasionally drops
connections under high concurrency. The server uses tokio's multi-threaded
runtime with default settings and hyper v1.x's hyper::server::conn::http1::Builder
with default connection settings (the legacy Server::bind() / Http::new()
high-level APIs were removed in hyper 1.0 in favor of this service-based
per-connection builder model).

Investigation reveals a configuration drift: tokio's default worker thread
count (equal to CPU cores) combined with hyper's default connection-level
settings create a scenario where the accept loop can starve worker threads
under load.

Your task:
1. Find where tokio configures its multi-threaded runtime defaults — trace
   through Runtime::new() and Builder::new_multi_thread() to identify default
   worker thread count, blocking thread pool size, and task scheduling behavior.
2. Find where hyper v1.x configures its HTTP server connection handling — trace
   through hyper::server::conn::http1::Builder (and http2::Builder for HTTP/2)
   to identify default keep-alive timeouts, connection limits, and how the
   service-based serve_connection model spawns tasks per connection on the
   tokio runtime.
3. Identify the drift points where tokio's thread pool defaults conflict with
   hyper's connection spawning model — particularly around how many connections
   hyper will accept vs how many tokio worker threads are available to process
   them, and how blocking operations in request handlers interact with the
   tokio thread pool.
4. Document each drift point with source files from both repos.

Document in your answer (use these JSON fields):
{
  "drift_points": [
    {
      "config_key": "<configuration parameter>",
      "tokio_default": "<default value from tokio>",
      "tokio_source_file": "<path in tokio repo>",
      "hyper_assumption": "<what hyper assumes/configures>",
      "hyper_source_file": "<path in hyper repo>",
      "impact": "<potential impact of this drift>"
    }
  ]
}
