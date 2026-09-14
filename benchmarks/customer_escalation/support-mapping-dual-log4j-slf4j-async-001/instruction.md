# Map customer async log lines lost under high load to the slf4j Logger / log4j2 AsyncAppender disruptor binding boundary

A customer reports that under sustained burst load their service
loses 1-2% of log lines that were written via the slf4j Logger API.
Logs from synchronous critical paths show fine; only the async
channels (driven by log4j2 AsyncAppender) are dropping. Their config
uses log4j-slf4j-impl as the binding.

Your task:
1. Find where slf4j routes a Logger.info() call through its binding
   API to a log4j2 implementation.
2. Find where log4j2 AsyncAppender wraps an LMAX Disruptor ring buffer
   and its overflow / blocking policy.
3. Trace the cross-repo path: slf4j LoggerFactory -> log4j-slf4j-impl
   Log4jLogger -> log4j2 AsyncAppender -> Disruptor publish -> ring
   buffer overflow policy. Cite each file and the function or
   attribute responsible.
4. Identify why the default Disruptor ring-buffer-full policy is
   non-blocking with a configurable async-queue-full-policy that
   silently drops events under burst load.
