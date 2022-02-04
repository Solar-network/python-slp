# -*- coding:utf-8 -*-

import os
import slp
import math
import json
import queue
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
            "hash": hashlib.sha256(seed).hexdigest(),
        }
    }
    return send_message(msg)


def manage_hello(msg):
    prospect_peers(msg["hello"]["peer"])
    slp.LOG.info("discovered peers: %s", len(PEERS))


def manage_consensus(msg):
    # get consensus data
    blockstamp = msg["consensus"]["blockstamp"]
    height, index = [int(e) for e in blockstamp.split("#")]
    reccord = dbapi.find_reccord(height=height, index=index)

    if reccord is not None:
        # compute poh from asked reccord and send it to requester peer
        poh = dbapi.compute_poh(
            "journal", reccord.get("poh", ""), **msg["consensus"]
        )
        send_message(
            {
                "consent": {
                    "blockstamp": blockstamp,
                    "poh": poh,
                    "#": os.urandom(32).hex()
                }
            },
            msg["consensus"]["origin"]
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
    for peer in set(peers) - set([me]):
        # ask peer's peer list
        resp = req.GET.peers(peer=peer)
        # if it answerd
        if isinstance(resp, list):
            # add peer to peerlist and prospect peer's peer list
            PEERS.update([peer])
            peer_s_peer = set(resp)
            # if peer is missing some known peer from here
            if len(PEERS - peer_s_peer):
                discovery(peer, peer=me)
            # recursively prospect unknown peers from here
            prospect_peers(*(peer_s_peer - PEERS))


class Consensus:

    MUTEX = threading.Lock()
    JOB = {}

    def __init__(self, poh, func, *args, **kwargs):
        self.quorum = 0
        self.aim = math.ceil(len(PEERS) / 2.)
        self.poh = poh
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def push(self, blockstamp):
        with Consensus.MUTEX:
            self._blockstamp = blockstamp
            Consensus.JOB[blockstamp] = self

    @staticmethod
    def update(blockstamp, poh):
        if blockstamp not in Consensus.JOB:
            slp.LOG.info(
                "no concesus initialized at blockstamp %s" % blockstamp
            )
            return None
        with Consensus.MUTEX:
            Consensus.JOB[blockstamp].quorum += (
                1 if Consensus.JOB[blockstamp].poh == poh else 0
            )
            if Consensus.JOB[blockstamp].quorum >= \
               Consensus.JOB[blockstamp].aim:
                return Consensus.JOB.pop(blockstamp).trigger()
                return True
            else:
                return False

    def trigger(self):
        result = self.func(*self.args, *self.kwargs)
        del Consensus.JOB[self._blockstamp]
        return result or "[triggered]"


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


class Topology(threading.Thread):

    PEERS = set([])

    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self)
        peers = slp.loadJson("topology.json")
        if peers:
            Topology.PEERS.update(peers)
        self.daemon = True
        self.start()
        slp.LOG.info("Topology %s set", id(self))

    @staticmethod
    def stop():
        Topology.STOP.set()

    def run(self):
        # idea is to prospect for a all peers
        for peer in set([
            "http://%s:5201" % p["ip"] for p in req.GET.api.peers(
                orderBy="height:desc", peer=slp.JSON["api peer"]
            ).get("data", [])
        ]) - Topology.PEERS:
            slp.LOG.debug("checking %s", peer)
            if req.HEAD.message(peer=peer).get("status", 400) == 200:
                Topology.PEERS.update([peer])
                slp.LOG.info("SLP peer found: %s", peer)
        slp.dumpJson(list(Topology.PEERS), "topology.json")
        slp.LOG.info(
            "topology determination done (%d peers)", len(Topology.PEERS)
        )
        discovery(*Topology.PEERS)
