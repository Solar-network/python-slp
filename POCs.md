
# SLP concensus

 > Databases being built from journal, focus have to be done on slp journal.

## Concensus arround journal proof of history

User submits a contract proposition to a specific peer (requested peer) of SLP network. It will return a transaction to sign and broadcast if network concensus is reached.

A concensus message is sent by a requested peer and has to reach a succession of N random peers or a succession of M validation. Once N or M is reach, message returns back to the requested peer.

### concensus flow

  1. user sublit a SLP contract proposition to requested peer
  2. requested peer checks contract assertions
     - exit if at least one assertion is `False`
  3. requested peer computes `hash(SLP contract)` and POH
  4. requested peer generates concensus message and send it to a random peer
     - message : `{"ip":<ip>, "height":<height>, "hash"<hash>, "poh":<POH>, "answers":[], "N":<N>, "M":<M>}`
  5. on concensus message received :
     + append `True` or `False` to `answers` according to contract assertions and computed POH
     + if `len(answers) >= N` or `nb(true) in answers >= M` send back to requested peer ip else send message to a random peer
  6. on concensus back to requested peer:
     - compute a transaction to be signed by user if quorum is reach (`nb(True) >= M`)

# SLP crosschain POC

## Basic

[![](https://mermaid.ink/img/pako:eNqNUs1PgzAU_1de6nWQUL85eCDTkzEm6Ek8dPRta4CWtMW5wP5332DoFjSxlzYvv8-Xtiw3ElnMlqXZ5GthPbzMMw10mrdXh_YdgjC466IQamtq49BBbo1zAYGVhvTxGVxFtIVVcoUdpO1-9IR-Y2wB0W7QSgcV_oeK_zyLwBtwaqU7aA7-EBDlPOynILSEhTVC5sL5CbmDpE1KkxfDaLRNeomLED5QS2MfFJYSlAOLgm4NG1ysjSlACi8o-knWy3AakaiirkuF8hhN4KsQKsqFFiLQxqvl9peIKe93ow-74eNueC9x_S3BwVHcyYY4leTHLUeBZBC4-W9N_uO7L3o7KcpPixKezViFthJK0ldp9_yM-TVWmLGYnlLYImOZ3hGuqckG76XyxrJ4KUqHMyYab9KtzlnsbYMjaK7EyorqgNp9AbHS1MM)](https://mermaid.live/edit/#pako:eNqNUs1PgzAU_1de6nWQUL85eCDTkzEm6Ek8dPRta4CWtMW5wP5332DoFjSxlzYvv8-Xtiw3ElnMlqXZ5GthPbzMMw10mrdXh_YdgjC466IQamtq49BBbo1zAYGVhvTxGVxFtIVVcoUdpO1-9IR-Y2wB0W7QSgcV_oeK_zyLwBtwaqU7aA7-EBDlPOynILSEhTVC5sL5CbmDpE1KkxfDaLRNeomLED5QS2MfFJYSlAOLgm4NG1ysjSlACi8o-knWy3AakaiirkuF8hhN4KsQKsqFFiLQxqvl9peIKe93ow-74eNueC9x_S3BwVHcyYY4leTHLUeBZBC4-W9N_uO7L3o7KcpPixKezViFthJK0ldp9_yM-TVWmLGYnlLYImOZ3hGuqckG76XyxrJ4KUqHMyYab9KtzlnsbYMjaK7EyorqgNp9AbHS1MM)

## SLP master node

SLP node having passphrase of SLP master address. This node may then issue transactions on blockchain or certified messages between SLP networks.

  - foreign slp network can be stored in `hub.json` file
  - network definition :
    + master node public key
    + list of slp peers
  - network discovery :
    + any slp peer can answer with {"master public key", "PEERS"}

Knowing foreign-slp-network public key, each message issued from this network can be verified
