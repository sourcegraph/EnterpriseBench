# Map customer Spring Kafka listener acks lost during consumer rebalance to the KafkaListener / ConsumerRebalanceListener ack-mode boundary

A customer reports that their Spring Kafka listener occasionally
double-processes messages whenever the consumer group rebalances. The
logs show acks fired by the listener, but Kafka shows the offsets
were not committed before the partition was revoked.

Your task:
1. Find where Spring Kafka's KafkaMessageListenerContainer wires its
   ConsumerRebalanceListener and how it interacts with the configured
   ack-mode (BATCH, RECORD, MANUAL_IMMEDIATE).
2. Find where Apache Kafka's clients module enforces the
   ConsumerRebalanceListener.onPartitionsRevoked contract and the
   commit semantics around it.
3. Trace the cross-repo path: Spring KafkaListener -> ListenerConsumer
   poll loop -> rebalance event -> ConsumerCoordinator.invokeOnPartitionsRevoked
   -> Spring's ack queue flush -> commit. Cite each file and the
   function or attribute responsible.
4. Identify why ack-mode BATCH defers commit until after the listener
   batch returns, and why this can race with onPartitionsRevoked,
   leaving commit-pending records double-processed after rebalance.
