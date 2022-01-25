
# SLP concensus

The way SLP is designed allows fast state build from simple assertions on journal database. There is however one contract type which application can not be based on thoses assertions: STC or cross blockchain exchange.

On SLP state computation, there are two possibilities about SLP network:

  1. there is one slp node: it have to do all the checkings by itself
  2. there are more than 5 slp node on a p2p network: it have to ask for a concensus

## Applying STC contract on its own

## Concensus within p2p network

The node asks if STC contract is legit to all known peers. To avoid malicious answer, peers have to send `legit` value of the contract back with a hash of the wallet state at the asked STC blockstamp.

The node can then filter all answers according to hash and apply the STC contract if the majority +1 is okay.