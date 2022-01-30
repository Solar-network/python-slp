# -*- coding:utf-8 -*-

import slp
import math
import json
import queue
import random
import hashlib
import threading
import traceback

from usrv import req
from slp import dbapi

#: place to sort discovered peers
PEERS = set([])
#: peer limit to avoid auto DDOS on peer prospection
PEER_LIMIT = slp.JSON.get("peer limit", 10)


def send_message(msg, *peers):
    """
    Post message to `/message` endpoints from a peer selection.
    """
    return Broadcaster.broadcast(req.POST.message, msg, *peers or PEERS)


def bind_callback(reccord, func, *args, **kwargs):
    """
    Create a bound between python function and a consensus on reccord validity.
    """
    # get valid slp fields from milestone
    height = reccord["height"]
    blockstamp = f"{height}#{reccord['index']}"
    slp_fields = slp.JSON.ask("slp fields", height=height)
    # filter reccord to extract slp fields only for poh computation and create
    # consensus object
    fields = dict([k, v] for k, v in reccord.items() if k in slp_fields)
    Consensus(
        dbapi.compute_poh("journal", **fields), func, *args, **kwargs
    ).push(blockstamp)
    # compute slp fields hash
    seed = json.dumps(fields, sort_keys=True, separators=(',', ':'))
    seed = seed.encode("utf-8")
    msg = {
        "consensus": {
            "origin": f"http://{slp.PUBLIC_IP}:{slp.PORT}",
            "blockstamp": blockstamp,
            "hash": hashlib.md5(seed).hexdigest(),
            "n": len(PEERS), "x": 0
        }
    }

    resp = {}
    while not resp.get("queued", False):
        resp = req.POST.message(
            _jsonify=msg, peer=random.choice(list(PEERS))
        )


def manage_hello(msg):
    prospect_peers(msg["hello"]["peer"])
    slp.LOG.info("discovered peers: %s", len(PEERS))


def manage_consensus(msg):
    # forward is a flag to be set to True if quorum not reached
    forward = False
    # get consensus data
    blockstamp = msg["consensus"]["blockstamp"]
    height, index = [int(e) for e in blockstamp.split("#")]
    reccord = dbapi.find_reccord(height=height, index=index)
    # if reccord is not found, journal is not sync so forward message to a
    # random peer
    if reccord is None:
        forward = True
    else:
        # compute poh from asked reccord and send it to requester peer
        poh = dbapi.compute_poh(
            "journal", reccord.get("poh", ""), **msg["consensus"]
        )
        send_message(
            {"consent": {"blockstamp": blockstamp, "poh": poh}},
            msg["consensus"]["origin"]
        )
        # then increment the x value and forward message if needed
        msg["consensus"]["x"] += 1
        if msg["consensus"]["x"] < msg["consensus"]["n"]:
            forward = True
    # if forward needed, send consensus message to a random peer
    if forward:
        resp = {}
        while not resp.get("queued", False):
            resp = req.POST.message(
                _jsonify=msg, peer=random.choice(list(PEERS))
            )


def discovery(*peers, peer=None):
    peers = peers or PEERS
    msg = {"hello": {"peer": peer or f"http://{slp.PUBLIC_IP}:{slp.PORT}"}}
    slp.LOG.debug(
        "launching a discovery of %s to %s peers",
        msg["hello"]["peer"], len(peers)
    )
    return Broadcaster.broadcast(req.POST.message, msg, *peers)


def prospect_peers(*peers):
    """
    Recursive peer prospection from a peer selection.
    """
    # exit prospetion if peer limit reached
    if len(PEERS) > PEER_LIMIT:
        return

    slp.LOG.debug("prospecting %s peers", len(peers))
    me = f"http://{slp.PUBLIC_IP}:{slp.PORT}"
    # for all new peer
    for peer in set(peers) - set([me]) - PEERS:
        # ask peer's peer list
        resp = req.GET.peers(peer=peer)
        # if it answerd
        if isinstance(resp, list):
            # add peer to peerlist and prospect peer's peer list
            PEERS.update([peer])
            peer_s_peer = set(resp)
            # if peer is missing some known peer from here
            if len(PEERS - peer_s_peer):
                discovery(peer, me)
            # recursively prospect unknown peers from here
            prospect_peers(*(peer_s_peer - PEERS))


class Consensus:

    MUTEX = threading.Lock()
    JOB = {}

    def __init__(self, poh, func, *args, **kwargs):
        self.responses = 0
        self.quorum = 0
        self.poh = poh
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def push(self, blockstamp):
        with Consensus.MUTEX:
            Consensus.JOB[blockstamp] = self

    @staticmethod
    def update(blockstamp, poh):
        if blockstamp not in Consensus.JOB:
            raise Exception(
                "no concesus initialized at blockstamp %s" % blockstamp
            )
        with Consensus.MUTEX:
            Consensus.JOB[blockstamp].responses += 1
            if Consensus.JOB[blockstamp].poh == poh:
                Consensus.JOB[blockstamp].quorum += 1
            if Consensus.JOB[blockstamp].quorum >= math.ceil(len(PEERS) / 2.):
                return Consensus.JOB.pop(blockstamp).trigger()
            elif Consensus.JOB[blockstamp].responses >= len(PEERS):
                del Consensus.JOB[blockstamp]

    def trigger(self):
        return self.func(*self.args, *self.kwargs)


class Broadcaster(threading.Thread):
    """
    Daemon broadcast manager.
    """

    JOB = queue.Queue()
    STOP = threading.Event()

    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self)
        self.daemon = True
        self.start()
        slp.LOG.info("Broadcaster %s set", id(self))

    @staticmethod
    def broadcast(endpoint, msg, *peers):
        Broadcaster.JOB.put([endpoint, msg, *peers])

    @staticmethod
    def stop():
        Broadcaster.STOP.set()
        Broadcaster.JOB.put([None, None])

    def run(self):
        # controled infinite loop
        while not Broadcaster.STOP.is_set():
            try:
                endpoint, msg, *peers = Broadcaster.JOB.get()
                if isinstance(endpoint, req.EndPoint):
                    for peer in peers or PEERS:
                        slp.LOG.info(
                            "%s", endpoint(peer=peer, _jsonify=msg)
                        )
                else:
                    slp.LOG.info("Broadcaster %s clean exit", id(self))
            except Exception as error:
                slp.LOG.error("%r\n%s", error, traceback.format_exc())
