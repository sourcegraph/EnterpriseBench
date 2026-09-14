# Map customer gRPC reflection API regression after protobuf v3->v4 bump to the grpc-cpp ServerReflection / protobuf DescriptorPool boundary

A customer reports that after upgrading protobuf from 3.21 to 4.25
their grpc-cpp service's reflection API returns empty descriptors
for messages that previously worked. grpcurl -msg-template fails
with 'message not found in registry'. The .proto files are
unchanged.

Your task:
1. Find where grpc-cpp's ServerReflection service implements
   FileContainingSymbol and how it queries the descriptor pool.
2. Find where protobuf's DescriptorPool holds generated descriptors
   and how the v4 API change to message_factory affects descriptor
   visibility.
3. Trace the cross-repo path: grpc::reflection::v1alpha::ServerReflection
   -> grpc-cpp ProtoServerReflection -> protobuf DescriptorPool
   ::generated_pool -> generated_message_table lookup. Cite each
   file and the function or attribute responsible.
4. Identify why protobuf v4 split the generated descriptor pool
   per shared library, causing grpc-cpp's reflection lookup to miss
   descriptors compiled into a different .so.
