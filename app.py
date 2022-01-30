# -*- coding:utf-8 -*-

import io
import os
import re
import sys
import slp
import signal
import logging
import logging.handlers

from usrv import srv, req
from pymongo import MongoClient
from bson.decimal128 import Decimal128
from slp import sync, node, msg, dbapi


def init(name, **overrides):
    slp.JSON.load(name, **overrides)
    database_name = slp.JSON["database name"]
    slp.REGEXP = re.compile(slp.JSON["serialized regex"])
    slp.INPUT_TYPES = slp.JSON.ask("input types")
    slp.TYPES_INPUT = dict([v, k] for k, v in slp.INPUT_TYPES.items())
    slp.PUBLIC_IP = req.GET.plain(peer="https://www.ipecho.net").get(
        "raw", slp.get_extern_ip()
    )
    # update validation field 'tp'
    slp.VALIDATION["tp"] = lambda value: value in slp.INPUT_TYPES
    # create the SLP[i] global variables
    for slp_type in slp.JSON.ask("slp types"):
        setattr(slp, slp_type[1:].upper(), slp_type)
    # initialize logger
    # TODO: add log rotation parameters to slp.json
    slp.LOG.handlers.clear()
    slp.LOG.setLevel(slp.JSON.get("log level", "DEBUG"))
    logpath = os.path.join(slp.ROOT, ".log", f"{database_name}.log")
    os.makedirs(os.path.dirname(logpath), exist_ok=True)
    slp.LOG.addHandler(
        logging.handlers.TimedRotatingFileHandler(
            logpath, when="d", interval=1, backupCount=7
        )
    )
    # MONGO DB definitions
    dbapi.db = MongoClient(slp.JSON.get("mongo url", None))[database_name]
    dbapi.db.contracts.create_index("tokenId", unique=True)
    dbapi.db.journal.create_index([("height", 1), ("index", 1)], unique=True)
    dbapi.db.rejected.create_index([("height", 1), ("index", 1)], unique=True)
    dbapi.db.slp1.create_index([("address", 1), ("tokenId", 1)], unique=True)
    dbapi.db.slp2.create_index([("address", 1), ("tokenId", 1)], unique=True)
    # generate Decimal128 builders for all legit slp1 token
    for reccord in dbapi.db.journal.find(
        {"tp": "GENESIS", "slp_type": slp.SLP1, "legit": True}
    ):
        slp.DECIMAL128[reccord["id"]] = \
            lambda v, de=reccord.get('de', 0): Decimal128(f"%.{de}f" % v)
    # update peer limit in node module
    node.PEER_LIMIT = slp.JSON.get("peer limit", 10)


def deploy(host="127.0.0.1", port=5200, blockchain="ark"):
    """
    Deploy slp node on ubuntu as system daemon.
    """
    normpath = os.path.normpath
    executable = normpath(sys.executable)
    package_path = normpath(os.path.abspath(os.path.dirname(slp.__path__[0])))
    gunicorn_conf = normpath(
        os.path.abspath(os.path.join(package_path, "gunicorn.conf.py"))
    )

    with io.open("./slp.service", "w") as unit:
        unit.write(f"""[Unit]
Description=Side ledger Protocol app
After=network.target
[Service]
User={os.environ.get("USER", "unknown")}
WorkingDirectory={normpath(sys.prefix)}
Environment=PYTHONPATH={package_path}
ExecStart={os.path.join(os.path.dirname(executable), "gunicorn")} \
"app:SlpApp('{host}', {port}, blockchain='{blockchain}')" \
--bind={host}:{port} --workers=1 --access-logfile -
Restart=always
[Install]
WantedBy=multi-user.target
""")
    os.system("chmod +x ./slp.service")
    os.system("sudo mv --force ./slp.service /etc/systemd/system")

    with io.open("./slpsrv.service", "w") as unit:
        unit.write(f"""[Unit]
Description=Side ledger Protocol server
After=network.target
[Service]
User={os.environ.get("USER", "unknown")}
WorkingDirectory={normpath(sys.prefix)}
Environment=PYTHONPATH={package_path}
ExecStart={os.path.join(os.path.dirname(executable), "gunicorn")} \
"app:uHtmlApp('{host}', {port + 1})" --bind={host}:{port + 1} --workers=4 \
--access-logfile -
Restart=always
[Install]
WantedBy=multi-user.target
""")
    os.system("chmod +x ./slpsrv.service")
    os.system("sudo mv --force ./slpsrv.service /etc/systemd/system")

    if os.system("%s -m pip show gunicorn" % executable) != "0":
        os.system("%s -m pip install gunicorn" % executable)
    os.system("sudo cp %s %s" % (gunicorn_conf, normpath(sys.prefix)))
    os.system("sudo systemctl daemon-reload")
    if not os.system("sudo systemctl restart mongod"):
        os.system("sudo systemctl start mongod")
    if not os.system("sudo systemctl restart slp"):
        os.system("sudo systemctl start slp")
    if not os.system("sudo systemctl restart slpsrv"):
        os.system("sudo systemctl start slpsrv")


class uHtmlApp(srv.uJsonApp):

    def __call__(self, environ, start_response):
        return srv.uroot.wsgi_call(
            srv.uroot.uRawHandler, environ, start_response
        )


class SlpApp(srv.uJsonApp):

    def __init__(self, host="127.0.0.1", port=5200, **options):
        slp.PORT = port
        init(options.get("blockchain", "ark"))
        srv.uJsonApp.__init__(
            self, host, port, loglevel=options.get("loglevel", 20)
        )
        sync.Processor()  # --> will start a BlockParser
        node.Broadcaster()
        msg.Messenger()
        signal.signal(signal.SIGTERM, SlpApp.kill)

    @staticmethod
    def kill(*args, **kwargs):
        sync.Processor.stop()
        node.Broadcaster.stop()
        msg.Messenger.stop()
        sync.chain.BlockParser.stop()


@srv.bind("/slp/<str:addr>/<str:tp>")
def test(addr, tp, **kw):
    return f"<html><body><p>{addr}, {tp}</p><p>{kw}</p></body></html>"


if __name__ == "__main__":
    # FOR TESTING PURPOSE ONLY ---

    parser = srv.OptionParser(
        usage="usage: %prog [options] BINDINGS...",
        version="%prog 1.0"
    )
    parser.add_option(
        "-i", "--ip", action="store", dest="host", default=slp.PUBLIC_IP,
        help="ip to run from             [default: slp defaul public ip]"
    )
    parser.add_option(
        "-p", "--port", action="store", dest="port", default=slp.PORT,
        type="int",
        help="port to use                [default: slp default port]"
    )

    init("ark")

    (options, args) = parser.parse_args()
    slp.PUBLIC_IP = options.host
    slp.PORT = options.port

    msg.Messenger()
    node.Broadcaster()

    srv.main()
