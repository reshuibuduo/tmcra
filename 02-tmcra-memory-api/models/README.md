# Runtime model assets

`tmcra_v3_reranker.pt` is the TMCRA runtime reranker checkpoint used by the
public default deployment profile. It is included so a third-party operator
does not need a private TMCRA server checkout.

- SHA-256: `380d4ce4949697110b963b1ac253bb29369b3e58f283515512c3d33c61f9d58e`
- Expected size: `4,234,837` bytes
- License: Apache-2.0, as part of this repository's distribution.

The larger learned-graph node and path checkpoints are not included. Keep
`TMCRA_LEARNED_GRAPH_ENABLED=0` unless those separately governed assets have
been obtained and configured by the operator.
