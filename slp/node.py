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
    height = reccord["height"]
    index = reccord["index"]

    slp_fields = slp.JSON.ask("slp fields", height=height)
    fields = dict([k, v] for k, v in reccord.items() if k in slp_fields)
    poh = dbapi.compute_poh("journal", **fields)
    Consensus(poh, func, *args, **kwargs).push()

    seed = json.dumps(fields, sort_keys=True, separators=(',', ':'))
    seed = seed.encode("utf-8")
    msg = {
        "consensus": {
            "origin": f"http://{slp.PUBLIC_IP}:{slp.PORT}",
            "blockstamp": f"{height}#{index}",
            "hash": hashlib.md5(seed).hexdigest(),
            "poh": poh, "n": len(PEERS), "x": 0
        }
    }

    resp = {}
    while not resp.get("queued", False):
        resp = send_message(msg, random.choice(PEERS))


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
        # compute poh from asked reccord and send it to requster peer
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
            resp = send_message(msg, random.choice(PEERS))


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
        if resp.get("status", -1) == 200:
            # add peer to peerlist and prospect peer's peer list
            PEERS.update([peer])
            peer_s_peer = set(resp.get("result", []))
            # if peer is missing some known peer from here
            if len(PEERS - peer_s_peer):
                discovery(peer, me)
            # recursively prospect unknown peers from here
            prospect_peers(*(peer_s_peer - PEERS))


class Consensus:

    MUTEX = threading.Lock()
    JOB = {}

    def __init__(self, poh, func, *args, **kwargs):
        self.quorum = 0
        self.poh = poh
        self.func = func
        self.args = args
        self.kwwargs = kwargs

    def push(self):
        with Consensus.MUTEX:
            Consensus.JOB[self.poh] = self

    @staticmethod
    def get(poh):
        with Consensus.MUTEX:
            return Consensus.JOB.pop(poh)

    @staticmethod
    def increment(poh):
        if poh not in Consensus.JOB:
            raise Exception("no concesus initialized with poh %s" % poh)
        with Consensus.MUTEX:
            Consensus.JOB[poh].quorum += 1
            if Consensus.JOB[poh].quorum > math.ceil(len(PEERS) / 2.):
                return Consensus.get("poh").trigger()

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
                        endpoint(peer=peer, _jsonify=msg)
                else:
                    slp.LOG.info("Broadcaster %s clean exit", id(self))
            except Exception as error:
                slp.LOG.error("%r\n%s", error, traceback.format_exc())
