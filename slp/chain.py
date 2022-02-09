# -*- coding:utf-8 -*-

"""
`chain` module is designed to manage webhook subscription with blockchain and
process validated blocks. Idea here is to extract SLP smartbridge transactions
and store it as a Mongo DB document.

Document structure:

name|description|type
-|-|-
height|transaction block height|unsigned long long
index|transaction index in block|short
txid|transaction id|hexadecimal
slp_type|SLP contract type|string
emitter|sender wallet address|base58
receiver|receiver wallet address|base58
cost|transaction amount|unsigned long long
tx|blockchain transaction id|hexadecimal
tp|type of action|string
id|token ID|hexidecimal
de|decimal places|short: 0..8
qt|quantity|float
sy|symbol / ticker|string
na|token name|string
du|document URI|string (`ipfs://` scheme)
no|notes|string
pa|pausable|boolean: Default false
mi|mintable|boolean: Default false
ch|smartbridge chunck|short
dt|data|string
"""

import os
import sys
import slp
import json
import queue
import random
import pickle
import hashlib
import importlib
import traceback
import threading

from slp import serde, dbapi
from usrv import req


def select_peers():
    peers = []
    try:
        # here candadates is at least [slp.JSON["api peer"]], so if default api
        # peer does not respond, it should loop until api peer is back
        candidates = req.GET.api.peers(
            peer=slp.JSON["api peer"], orderBy="height:desc",
            headers=slp.HEADERS
        ).get("data", [slp.JSON["api peer"]])
    except Exception:
        # in case of any HTTP error set peers to [slp.JSON["api peer"]]
        slp.LOG.error("Can not fetch peers from %s", slp.JSON["api peer"])
        peers = [slp.JSON["api peer"]]
    else:
        for candidate in candidates[:20]:
            api_port = candidate.get("ports", {}).get(
                "@arkecosystem/core-api", -1
            )
            if api_port > 0:
                peers.append("http://%s:%s" % (candidate["ip"], api_port))
    finally:
        return peers


def subscribed():
    # TODO: test webhook instead of file checking ?
    return os.path.exists(
        os.path.join(slp.ROOT, ".json", f"{slp.JSON['database name']}.wbh")
    )


def subscribe():
    """
    Webhook subscription management.
    """
    if subscribed():
        slp.LOG.info("Already subscribed to %s", slp.JSON["webhook peer"])
        return False

    # if webhook peer is local (ie, python-slp installed on a blockchain node)
    # use 127.0.0.1:{slp.PORT}, else use slp.PUBLIC_IP
    if slp.JSON["webhook peer"][:11] in ["http://loca", "http://127."]:
        ip = "127.0.0.1"
    else:
        ip = slp.PUBLIC_IP

    # blockchain subscription api use, only for applied blocks with at least
    # one transaction (numberOfTransactions >= 1)
    data = req.POST.api.webhooks(
        peer=slp.JSON["webhook peer"],
        target=f"http://{ip}:{slp.PORT}/blocks",
        event="block.applied",
        conditions=[
            {"key": "numberOfTransactions", "condition": "gte", "value": "1"}
        ]
    ).get("data", {})

    if data != {}:
        # manage security token
        data["key"] = dump_webhook_token(data.pop("token"))
        # dump webhook data
        slp.dumpJson(
            data, f"{slp.JSON['database name']}.wbh",
            os.path.join(slp.ROOT, ".json")
        )
        slp.LOG.info("Subscribed to %s", slp.JSON["webhook peer"])
        return True
    else:
        slp.LOG.error("Subscription to %s failed", slp.JSON["webhook peer"])
        return False


def unsubscribe():
    """
    Webhook subscription management.
    """
    webhook = f"{slp.JSON['database name']}.wbh"
    # slp.loadJson returns {} if file not found
    data = slp.loadJson(webhook, os.path.join(slp.ROOT, ".json"))
    if data != {}:
        resp = req.DELETE.api.webhooks(
            data["id"], peer=slp.JSON["webhook peer"]
        )
        # if status < 300 --> success and remove webhook files
        if resp.get("status", 300) < 300:
            os.remove(data["key"])
            os.remove(os.path.join(slp.ROOT, ".json", webhook))
            slp.LOG.info("Unsubscribed from %s", slp.JSON["webhook peer"])
        else:
            slp.LOG.error(
                "Unsubscription from %s failed", slp.JSON["webhook peer"]
            )
        return resp
    else:
        return False


def dump_webhook_token(token):
    """
    Secure webhook token management.
    """
    authorization = token[:32]
    verification = token[32:]
    filename = os.path.join(
        os.path.dirname(__file__),
        hashlib.md5(authorization.encode("utf-8")).hexdigest() + ".key"
    )
    with open(filename, "wb") as out:
        pickle.dump(
            {
                "verification": verification,
                "hash": hashlib.sha256(token.encode("utf-8")).hexdigest()
            }, out
        )
    return filename


def check_webhook_token(authorization):
    """
    Secure webhook token check.
    """
    filename = os.path.join(
        os.path.dirname(__file__),
        hashlib.md5(authorization.encode("utf-8")).hexdigest() + ".key"
    )
    try:
        with open(filename, "rb") as in_:
            data = pickle.load(in_)
    except Exception:
        return False
    else:
        token = authorization + data["verification"]
        return hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest() == data["hash"]


def get_unix_time(blockstamp, peer=None):
    """
    Convert blockstamp to unix timestamp.
    """
    height, index = blockstamp.split("#")
    block = req.GET.api.blocks(
        height, peer=peer or slp.JSON["api peer"], headers=slp.HEADERS
    ).get("data", {})
    transactions = block.get("transactions", 0)
    timestamp = block.get("timestamp", {}).get("unix", None)
    interval = float(slp.JSON["blocktime"]) / (int(transactions) + 1)
    if timestamp:
        return timestamp + interval * int(index)


def get_block_transactions(blockId, peer=None):
    data, page, result = [None], 1, []
    peer = peer or slp.JSON["api peer"]
    while len(data) > 0:
        data = req.GET.api.blocks(
            blockId, "transactions", page=page, peer=peer, headers=slp.HEADERS
        ).get("data", [])
        result += data
        page += 1
    return result


def read_vendorField(vendorField):
    contract = False
    try:
        contract = json.loads(vendorField)
    except Exception:
        try:
            contract = serde.unpack_slp(vendorField)
        except Exception:
            pass
    return False or contract


def manage_block(**request):
    """
    Dispatch webhook request.
    """
    # webhook security check
    auth = request.get("headers", {}).get("authorization", "?")
    if not check_webhook_token(auth):
        slp.LOG.info(
            "Webhook auth failed with header %s",
            request.get("headers", {})
        )
        return False
    # get block data
    slp.LOG.info("Genuine block header received:\n%s", request)
    body = json.loads(request.get("data", {}))
    block = body.get("data", {})
    # homogenize diff between api data and webhook data
    timestamp = float(body["timestamp"]) / 1000.  # time.time()
    timestamp -= timestamp % slp.JSON["blocktime"]
    block["timestamp"] = {
        "epoch": block.pop("timestamp"),
        "unix": timestamp,
    }
    block["transactions"] = block.pop("numberOfTransactions")
    # push block into queue to be parsed
    BlockParser.JOB.put(block)


def parse_block(block, peer=None):
    """
    Search valid SLP vendor fields in all transactions from specified block.
    If any, it is normalized and registered as a rreccord in journal.
    """
    # contracts to be returned
    contracts = []
    # get transactions from block
    nb_tx = int(block["transactions"])
    tx_list = get_block_transactions(block["id"], peer)
    # because at some point, peer could return nothing good, check the
    # transaction count, AssertionError will be managed by BlockParser
    try:
        assert len(tx_list) == nb_tx
    except AssertionError:
        slp.LOG.error("Can't retrieve all transactions from block")
        raise Exception("Block integrity breach")
    loop = zip(list(range(len(tx_list))), tx_list)
    # search for SLP vendor fields in transfer type transactions
    for index, tx in [
        (i+1, t) for i, t in loop
        if t["type"] == 0 and
        t.get("vendorField", "") != ""
    ]:
        # try to read contract from vendor field
        contract = read_vendorField(tx["vendorField"])
        if contract:
            try:
                slp_type, fields = list(contract.items())[0]
                if slp_type not in slp.JSON.ask("slp types", block["height"]):
                    slp.LOG.info("> unknown SLP contract found: %s", slp_type)
                    raise Exception("unknown SLP contract %s" % slp_type)
                slp.LOG.info(
                    "> SLP contract found: %s->%s", slp_type, fields["tp"]
                )
                # compute unix timestamp
                timestamp = block["timestamp"]["unix"]
                interval = float(slp.JSON["blocktime"]) / (nb_tx + 1)
                fields["timestamp"] = timestamp + interval * int(index)
                # compute token id for GENESIS contracts
                if fields["tp"] == "GENESIS":
                    if fields["sy"] in slp.JSON.ask("denied tickers"):
                        raise Exception(
                            "'%s' ticker is denied..." % fields["sy"]
                        )
                    fields.update(id=slp.get_token_id(
                        slp_type, fields["sy"], block["height"], tx["id"]
                    ))
                # add wallet informations and cost
                fields.update(
                    emitter=tx["sender"], receiver=tx["recipient"],
                    cost=int(tx["amount"])
                )
                # tweak numeric values
                if "de" in fields:
                    fields["de"] = int(fields["de"])
                if "qt" in fields:
                    fields["qt"] = float(fields["qt"])
                # add a new reccord in journal
                contract = dbapi.add_reccord(
                    block["height"], index, tx["id"], slp_type, **fields
                )
            except Exception as error:
                slp.LOG.error(
                    "Error occured with tx %s in block %d",
                    tx["id"], block["height"]
                )
                slp.LOG.debug("%r\n%s", error, traceback.format_exc())
            else:
                # because dbapi.add_reccord could return False or None if
                # reccord impossible do store in database
                if contract not in [None, False]:
                    contracts.append(contract)
    return contracts


class BlockParser(threading.Thread):

    JOB = queue.Queue()
    LOCK = threading.Lock()
    STOP = threading.Event()

    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self)
        self.daemon = True
        self.start()
        slp.LOG.info("BlockParser %s set", id(self))

    @staticmethod
    def apply(contract):
        module = f"slp.{contract['slp_type'][1:]}"
        try:
            if module not in sys.modules:
                importlib.__import__(module)
            sys.modules[module].manage(contract)
        except ImportError:
            slp.LOG.info(
                "No modules found to handle '%s' contracts",
                contract['slp_type']
            )
        except Exception as error:
            slp.LOG.error(
                "%r\n%s", error, traceback.format_exc()
            )

    @staticmethod
    def stop():
        if BlockParser.LOCK.locked():
            BlockParser.LOCK.release()
        BlockParser.STOP.set()
        BlockParser.JOB.put(None)

    def run(self):
        peers = select_peers()
        peer = random.choice(peers)
        BlockParser.STOP.clear()
        while not BlockParser.STOP.is_set():
            # atomic action starts here ---
            block = BlockParser.JOB.get()
            msg = ""
            if block is not None:
                BlockParser.LOCK.acquire()
                msg += "Parsing % 3d transaction(s) from block %s" % (
                    block["transactions"], block["height"]
                )
                try:
                    contracts = parse_block(block, peer)
                except Exception:
                    msg += " [FAILED]\nPushing back block %d, " \
                        "not enough transaction found" % block["height"]
                    slp.LOG.error(msg)
                    # put the block to the left of queue to be sure it will be
                    # get first on BlockParser LOCK release
                    with BlockParser.JOB.mutex:
                        BlockParser.JOB.queue.appendleft(block)
                    if peer in peers:
                        peers.remove(peer)
                    if len(peers) <= 1:
                        peers = select_peers()
                    peer = random.choice(peers)
                    BlockParser.LOCK.release()
                else:
                    msg += " [OK]"
                    slp.LOG.info(msg)
                    BlockParser.LOCK.release()
                    # atomic action is stopped for sure ---
                    for contract in contracts:
                        BlockParser.apply(contract)
            else:
                slp.LOG.info("BlockParser %s clean exit", id(self))
