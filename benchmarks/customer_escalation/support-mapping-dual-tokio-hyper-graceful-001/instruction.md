# Map customer hyper server hanging on graceful shutdown when tokio runtime drains to the hyper Server::with_graceful_shutdown / tokio runtime drain boundary

A customer reports that their hyper-based service hangs for 60 seconds
on SIGTERM during deployment. The signal handler triggers
Server::with_graceful_shutdown's shutdown_signal future, but the
process refuses to exit until the kubernetes terminationGracePeriod
kills it. They observe one in-flight long-poll request that never
drops.

Your task:
1. Find where hyper's Server type implements with_graceful_shutdown
   and the connection-tracking it does for in-flight requests.
2. Find where tokio's runtime drains tasks on Runtime::shutdown or
   when the runtime handle is dropped, and how it interacts with
   tasks holding I/O resources.
3. Trace the cross-repo path: tokio::signal::ctrl_c future ->
   hyper Server::with_graceful_shutdown future select -> hyper
   Connection::poll close -> tokio reactor I/O cancellation. Cite
   each file and the function or attribute responsible.
4. Identify why an in-flight HTTP/1.1 long-poll request held in a
   tokio task is not interrupted by hyper's graceful shutdown,
   because hyper waits for the response body to complete and tokio
   cannot force-cancel the task without dropping the runtime.
