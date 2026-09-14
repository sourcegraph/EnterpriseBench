# Map customer Kafka deserialization error to Spring Kafka ErrorHandler and Spring Boot auto-configuration chain

A customer reports that their Spring Boot Kafka consumer silently drops messages
when the Kafka topic contains records with an unexpected schema. The consumer
logs show:

    org.apache.kafka.common.errors.SerializationException: Error deserializing
    key/value for partition topic-orders-0 at offset 1523.

The consumer continues processing subsequent messages but the failed messages
are never retried and never sent to a dead letter topic, despite the customer
configuring `spring.kafka.consumer.properties.spring.deserializer.value.delegate.class`.

The customer expects that deserialization failures would be routed to the
configured DeadLetterPublishingRecoverer, but this only works for exceptions
thrown AFTER deserialization succeeds.

Your task:

1. Find where Spring Kafka handles deserialization errors -- trace from the
   KafkaMessageListenerContainer through the ErrorHandler chain and identify
   where SerializationException is caught vs where it bypasses the error handler.
2. Find where Spring Boot auto-configures Kafka consumer error handling -- trace
   from KafkaAutoConfiguration through ConcurrentKafkaListenerContainerFactory
   setup and identify which ErrorHandler is installed by default.
3. Map the full code path: Kafka client throws SerializationException -> Spring
   Kafka container catches it -> error handler decision -> why dead letter topic
   is skipped for deserialization errors. Document each file and function.
4. Identify the correct configuration to handle deserialization errors
   (ErrorHandlingDeserializer wrapper) and trace where it integrates.

- Full code path from exception to handling decision
- Why DeadLetterPublishingRecoverer is bypassed for deserialization errors
- Correct configuration approach and its integration point
