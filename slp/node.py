# -*- coding:utf-8 -*-

import slp
import queue
import random
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
