
# SLP consensus

 > Databases being built from journal, focus have to be done on slp journal.

## Consensus with journal proof of history

Each journal entry contains a `poh` (proof of history) computed as a md5 hash on concatenation of previous `poh` entry and md5 hash of current SLP contract fields.

```python
>>> previous_poh = "9996e575e3306735b3098605b8b1efba"
>>> slp1 = {
   "tp": "SEND",
   "id": "8259ce077b1e767227e5e0fce590d26d",
   "qt": 10.0,
   "no": "Enjoy your bARK tokens!"
}
>>> seed = json.dumps(slp1, sort_keys=True, separators=(',', ':'))
>>> seed = previous_poh + hashlib.md5(seed.encode("utf-8")).hexdigest()
>>> hashlib.md5(seed.encode("utf-8")).hexdigest()
'04fbeeb813a5b1bee8fefa8735e196fb'
```

## SLP transaction

Once user submited a contract proposition to a specific peer (requested peer), it will return a transaction to be signed and broadcasted if network consensus is reached.

A consensus message is sent by a requested peer and has to reach a succession of N random peers.

### consensus flow

  1. user submit a SLP contract proposition to requested peer
  2. requested peer checks contract assertions
     - exit if at least one assertion is `False`
  3. requested peer computes `hash(SLP contract)` and `POH`
  4. requested peer generates consensus message and send it to a random peer
     - consensus message : `{"origin":<peer address>, "blockstamp":<bockstamp>, "hash"<slp fields hash>, "n":<N>, "x":0}`
  5. on consensus message received :
     + compute `POH`, and send it to requested peer with height and hash: `{"blockstamp":<blockstamp>, "POH":<poh>}`
     + if `x < n` increment X and forward to a random peer: `{"origin":<peer address>, "blockstamp":<bockstamp>, "hash"<slp fields hash>, "n":<N>, "x":1}`
  6. requested peer increment valid POH count until quorum is reach

# SLP crosschain POC

## Basic

[![](https://mermaid.ink/img/pako:eNqNkstOhDAUhl_lpG6FSL0T44KMrowxQVfiokMPMw3QkrY4TmDe3QKDwwRN7Ibm5L8cvrQhqeJIQpIVapOumbbwukgkuFO_vxnUH-D53n0b-FBpVSmDBlKtjPGcWEiIn17AlM621IKvsIW46UbPaDdK5xDshqx4SKF_pNivkwCsAiNWsoV63w-es5z7_RSY5LDUivGUGTsztxA1UaHSfBiNtVEfceHDJ0qu9KPAgoMwoJG5r4QNLtdK5cCZZW71o10v_fmKzsqqqhDIp2onvvKhdHuhhgCksiLb_rJiTHs2cs-Gjmwo3HUZ1z8ZFJjJIVUyRWlq05GZGoNDOe3bbyZO4_50Bpc6PnQKaOyOhoDb_xKih97-VZzNINFjSM5ATkmJumSCu2fWdAEJsWssMSGhu3LMWF3YhCRy56R15arwgQurNAkzVhg8Jay2Kt7KlIRW1ziKFoKtNCv3qt03hZfong)](https://mermaid.live/edit/#pako:eNqNkstOhDAUhl_lpG6FSL0T44KMrowxQVfiokMPMw3QkrY4TmDe3QKDwwRN7Ibm5L8cvrQhqeJIQpIVapOumbbwukgkuFO_vxnUH-D53n0b-FBpVSmDBlKtjPGcWEiIn17AlM621IKvsIW46UbPaDdK5xDshqx4SKF_pNivkwCsAiNWsoV63w-es5z7_RSY5LDUivGUGTsztxA1UaHSfBiNtVEfceHDJ0qu9KPAgoMwoJG5r4QNLtdK5cCZZW71o10v_fmKzsqqqhDIp2onvvKhdHuhhgCksiLb_rJiTHs2cs-Gjmwo3HUZ1z8ZFJjJIVUyRWlq05GZGoNDOe3bbyZO4_50Bpc6PnQKaOyOhoDb_xKih97-VZzNINFjSM5ATkmJumSCu2fWdAEJsWssMSGhu3LMWF3YhCRy56R15arwgQurNAkzVhg8Jay2Kt7KlIRW1ziKFoKtNCv3qt03hZfong)

## SLP master node

SLP node having passphrase of SLP master address. This node may then issue transactions on blockchain or certified messages between SLP networks.

  - foreign slp network can be stored in `hub.json` file
  - network definition :
    + master node public key
    + list of slp peers
  - network discovery :
    + any slp peer can answer with {"master public key", "PEERS"}

Knowing foreign-slp-network public key, each message issued from this network can be verified
