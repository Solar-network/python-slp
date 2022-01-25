# -*- coding:utf-8 -*-

import slp
import queue
import threading
import traceback

from usrv import req

#: place to sort discovered peers
PEERS = set([])
#: peer limit to avoid auto DDOS on peer prospection
PEER_LIMIT = 10


def broadcast(endpoint, msg, *peers):
    """
    Send message as json string to specific endpoints from a peer selection.
    """
    resp = []
    if isinstance(endpoint, req.EndPoint):
        for peer in peers or PEERS:
            resp.append(endpoint(peer=peer, _jsonify=msg))
    return resp


def send_message(msg, *peers):
    """
    Post message to `/message` endpoints from a peer selection.
    """
    return Broadcaster.broadcast(req.POST.message, msg, *peers)


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


def manage_hello(msg):
    prospect_peers(*[msg["hello"]["peer"]])
    slp.LOG.info("discovered peers: %s", len(PEERS))


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
    def broadcast(*args):
        Broadcaster.JOB.put(args)

    @staticmethod
    def stop():
        Broadcaster.STOP.set()
        Broadcaster.JOB.put([None, None])

    def run(self):
        # controled infinite loop
        while not Broadcaster.STOP.is_set():
            try:
                endpoint, msg, *peers = Broadcaster.JOB.get()
                if endpoint is not None:
                    broadcast(endpoint, msg, *peers)
                else:
                    slp.LOG.info("Broadcaster %s clean exit", id(self))
            except Exception as error:
                slp.LOG.error("%r\n%s", error, traceback.format_exc())
