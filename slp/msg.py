# -*- coding:utf-8 -*-

import slp
import json
import queue
import hashlib
import threading
import traceback

from slp import node, chain, sync
from usrv import srv


@srv.bind("/blocks", methods=["POST"])
def listen_blockchain(**request):
    """
    Endpoint used to listen webhook data from `chain.subscribe` action. It
    sends the request to Messenger so the endpoint unloads itself as fast as
    possible.
    """
    if request["method"] == "POST":
        return {"queued": Messenger.put(request)}


# listen requests to /message endpoint
@srv.bind("/message", methods=["HEAD", "POST"])
def manage_message(**request):
    if request["method"] == "POST":
        return {"queued": Messenger.put(request)}


# listen requests to /peers endpoint
@srv.bind("/peers", methods=["GET"])
def send_peers(**request):
    if request["method"] == "GET":
        return list(node.PEERS)


class Memory(queue.Queue):
    """
    Queue avoiding double inputs.
    """

    LOCK = threading.Lock()

    def __contains__(self, item):
        with Memory.LOCK:
            return Memory.hash_item(item) in self.queue

    def put(self, item, block=True, timeout=None):
        item_h = Memory.hash_item(item)
        with Memory.LOCK:
            if item_h not in self.queue:
                if self.full():
                    self.get()
                queue.Queue.put(self, item_h, block, timeout)
                return True

    @staticmethod
    def hash_item(item):
        return hashlib.md5(
            json.dumps(item, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
        ).hexdigest()


class Messenger(threading.Thread):
    """
    Message manager.
    """

    JOB = queue.Queue()
    LOCK = threading.Lock()
    STOP = threading.Event()
    MEM = Memory(slp.JSON.get("message memory size", 20))

    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self)
        self.daemon = True
        self.start()
        slp.LOG.info("Messenger %s set", id(self))

    @staticmethod
    def put(request):
        # try to memorize message
        queued = Messenger.MEM.put(request.get("data", {}))
        # memorized
        if queued:
            Messenger.JOB.put(request)
        return queued

    @staticmethod
    def stop():
        if Messenger.LOCK.locked():
            Messenger.LOCK.release()
        Messenger.STOP.set()
        Messenger.JOB.put(None)

    def run(self):
        # controled infinite loop
        while not Messenger.STOP.is_set():
            try:
                request = Messenger.JOB.get()
                if request is not None:
                    # webhook data is {"timestamp":.., "event":.., "data":..}
                    if "event" in request.get("data", {}):
                        if sync.Processor.STOP.is_set():
                            chain.manage_block(**request)
                        else:
                            slp.LOG.info(
                                "Waiting for blochain sync, "
                                "webhooh request dropped:\n%s",
                                request
                            )
                    else:
                        msg = request.get("data", {})
                        headers = request.get("headers", {})
                        slp.LOG.info("Performing message: %r", msg)
                        if "hello" in msg:
                            resp = node.manage_hello(msg)
                        if "consensus" in msg:
                            resp = node.manage_consensus(msg)
                        if "consent" in msg:
                            resp = node.Consensus.increment(
                                msg["consent"]["poh"]
                            )
                else:
                    slp.LOG.info("Messenger %s clean exit", id(self))
            except Exception as error:
                slp.LOG.error("%r\n%s", error, traceback.format_exc())
