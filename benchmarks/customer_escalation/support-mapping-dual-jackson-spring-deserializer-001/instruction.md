# Map customer custom Jackson @JsonDeserializer being ignored under Spring Boot ObjectMapper auto-config to the SimpleModule registration boundary

A customer reports that their custom Jackson @JsonDeserializer for a
money type is silently ignored when Spring Boot deserializes a request
body. Calling objectMapper.readValue directly from a unit test works,
but the Spring MVC controller path uses the deserializer Jackson ships
by default. They expected the @JsonComponent annotation on their
deserializer to register it.

Your task:
1. Find where Spring Boot's JacksonAutoConfiguration builds the
   ObjectMapper bean, including how it discovers @JsonComponent and
   integrates it via SimpleModule.
2. Find where Jackson's ObjectMapper resolves a deserializer for a
   property at deserialization time, and the precedence order between
   per-property annotations, registered modules, and BeanDeserializerFactory.
3. Trace the cross-repo path: Spring MVC HandlerMethodArgumentResolver
   -> Spring's MappingJackson2HttpMessageConverter -> Jackson
   ObjectMapper.readValue -> BeanDeserializerFactory ->
   SimpleModule deserializer cache. Cite each file and the function
   responsible.
4. Identify why @JsonComponent registers the deserializer on the
   primary ObjectMapper bean only, and how a second ObjectMapper bean
   (for example for caching or messaging) bypasses the registration.
