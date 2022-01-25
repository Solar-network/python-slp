# -*- coding:utf-8 -*-

"""
Mongo database REST interface.
"""

import os
import sys
import slp
import math
import signal
import traceback

from usrv import srv
from slp import dbapi, serde
from pymongo import MongoClient

DECIMAL128_FIELDS = "balance,minted,burned,exited,globalSupply".split(",")
OPERATOR_FIELDS = "balance,minted,burned,exited,globalSupply,qt".split(",")
SEARCH_FIELDS = "address,tokenId,blockStamp,owner,frozen," \
                "slp_type,emitter,receiver,legit,tp,sy,id,pa,mi," \
                "height,index,type,paused,symbol".split(",")


@srv.bind("/<str:collection>/find", methods=["GET"])
def find(collection, **kw):
    try:
        # get collection
        col = getattr(dbapi.db, collection)

        # pop pagination keys
        orderBy = kw.pop("orderBy", None)
        page = int(kw.pop("page", 1))

        # filter kw so that only database specified keys can be search on.
        # it also gets rid of request environ (headers, environ, data...)
        filters = dict([k, v] for k, v in kw.items() if k in SEARCH_FIELDS)

        # build decima128 field filters so request with ==, >, >=, <, <=
        # operator can be used
        expr = {}
        for field, value in [
            (f, v) for f, v in kw.items()
            if f in OPERATOR_FIELDS and ":" in v
        ]:
            op, value = value.split(":")
            expr[f"${op}"] = [{"$toDouble": f"${field}"}, float(value)]
        if expr != {}:
            filters["$expr"] = expr

        # convert bool values
        for key in [
            k for k in ["owner", "frozen", "paused", "legit", "pa", "mi"]
            if k in filters
        ]:
            filters[key] = True if filters[key].lower() in ['1', 'true'] \
                else False if filters[key].lower() in ['0', 'false'] \
                else None

        # convert integer values
        for key in [k for k in ["height", "index"] if k in filters]:
            filters[key] = int(filters[key])

        # computes count and execute first filter
        total = col.count_documents(filters)
        pages = int(math.ceil(total / 100.))
        cursor = col.find(filters)

        # apply ordering
        if orderBy is not None:
            cursor = cursor.sort(
                tuple(
                    [field, -1 if order.lower() in ["desc", "reversed"] else 1]
                    for field, order in [
                        (order_by + (":" if ":" not in order_by else ""))
                        .split(":") for order_by in orderBy.split(",")
                    ]
                )
            )

        # jump to asked page
        cursor = cursor.skip((page-1) * 100)

        # build data
        data = []
        for reccord in list(cursor.limit(100)):
            reccord.pop("_id", False)
            if "metadata" in reccord:
                reccord["metadata"] = serde._unpack_meta(reccord["metadata"])
            for key in [k for k in DECIMAL128_FIELDS if k in reccord]:
                reccord[key] = str(reccord[key].to_decimal())
            data.append(reccord)

        return {
            "status": 200,
            "meta": {
                "count": len(data),
                "totalCount": total,
                "page": page,
                "pageCount": pages
            },
            "data": data
        }

    except Exception as error:
        slp.LOG.error(
            "Error trying to fetch data : %s\n%s", kw, traceback.format_exc()
        )
        return {"status": 501, "msg": "Internal Error: %r" % error}


def deploy(host="127.0.0.1", port=5100, blockchain="ark"):
    """
    Deploy API on ubuntu as system daemon.
    """
    normpath = os.path.normpath
    executable = normpath(sys.executable)
    package_path = normpath(os.path.abspath(os.path.dirname(slp.__path__[0])))
    gunicorn_conf = normpath(
        os.path.abspath(os.path.join(package_path, "gunicorn.conf.py"))
    )

    with open("./slpapi.service", "w") as unit:
        unit.write(f"""[Unit]
Description=Side ledger Protocol database API
After=network.target
[Service]
User={os.environ.get("USER", "unknown")}
WorkingDirectory={normpath(sys.prefix)}
Environment=PYTHONPATH={package_path}
ExecStart={os.path.join(os.path.dirname(executable), "gunicorn")} \
"slp.api:SlpApi(blockchain='{blockchain}')" --bind={host}:{port} --workers=5 \
--access-logfile -
Restart=always
[Install]
WantedBy=multi-user.target
""")
    if os.system("%s -m pip show gunicorn" % executable) != "0":
        os.system("%s -m pip install gunicorn" % executable)
    os.system("sudo cp %s %s" % (gunicorn_conf, normpath(sys.prefix)))
    os.system("chmod +x ./slpapi.service")
    os.system("sudo mv --force ./slpapi.service /etc/systemd/system")
    os.system("sudo systemctl daemon-reload")
    if not os.system("sudo systemctl restart mongod"):
        os.system("sudo systemctl start mongod")
    if not os.system("sudo systemctl restart slpapi"):
        os.system("sudo systemctl start slpapi")


class SlpApi(srv.uJsonApp):

    def __init__(self, **options):

        name = options.get("blockchain", "ark")
        level = options.get("loglevel", 20)

        # clear logging handlers
        slp.LOG.handlers.clear()

        data = slp.loadJson(f"{name}.json")
        if len(data) == 0:
            slp.LOG.error("Missing JSON configuration file for %s", name)
            raise Exception("No configuration file found for %s" % name)

        # MONGO DB definitions
        database_name = data["database name"]
        dbapi.db = MongoClient(data.get("mongo url", None))[database_name]

        srv.uJsonApp.__init__(self, loglevel=level)
        signal.signal(signal.SIGTERM, SlpApi.kill)

    @staticmethod
    def kill(*args, **kwargs):
        pass
