# Map customer tonic gRPC client stream stuck after server cancel to the tonic Streaming / tokio mpsc cancellation boundary

A customer reports that their tonic-based gRPC client occasionally
hangs forever on the next() call of a server-streaming RPC after the
server explicitly cancels the stream with Status::cancelled(). The
underlying HTTP/2 RST_STREAM is observed in tcpdump, but the
Streaming<T> never returns None.

Your task:
1. Find where tonic's Streaming type wraps the inner mpsc channel
   and how it converts a Status::cancelled into a stream end.
2. Find where tokio's mpsc Receiver decides between Some(item) and
   None on a closed channel, and how it interacts with sender drop
   ordering.
3. Trace the cross-repo path: tonic Streaming::poll_next -> mpsc
   Receiver::poll_recv -> server-side Status::cancelled emit ->
   sender drop -> Receiver close. Cite each file and the function
   responsible.
4. Identify why a Status::cancelled body frame on the wire does
   not always close tonic's mpsc sender immediately when h2 stream
   reset arrives at the client, and how an outstanding clone of
   the sender keeps the client receiver hanging.
