# Map customer Celery worker stalling after broker reconnect to the Kombu connection-retry / channel-prefetch reset boundary

A customer reports that their Celery worker stops consuming new tasks
after the RabbitMQ broker briefly disconnects. The worker logs the
reconnect, but no further task callbacks fire even though the queue
has thousands of waiting messages.

Your task:
1. Find where Celery's worker consumer wraps a Kombu Connection and
   subscribes to queues with a configured prefetch_count.
2. Find where Kombu re-establishes a channel after a broker drop and
   identify whether basic_qos is reapplied to the new channel.
3. Trace the cross-repo path: celery worker.consumer.Consumer ->
   kombu.connection.Connection.ensure -> channel re-creation -> prefetch
   reset. Cite each file and the function or attribute responsible.
4. Identify why a fresh AMQP channel without basic_qos defaults to
   prefetch=0 (unlimited), which combined with the RabbitMQ broker's
   semantics can leave the consumer effectively idle waiting for
   already-delivered messages to ack.
