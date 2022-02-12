# -*- coding:utf-8 -*-

import slp
import math
import traceback
import functools

from usrv import srv
from slp import dbapi, serde

DECIMAL128_FIELDS = "balance,minted,burned,exited,crossed," \
                    "globalSupply".split(",")
OPERATOR_FIELDS = "balance,minted,burned,crossed,globalSupply,qt".split(",")
SEARCH_FIELDS = "address,tokenId,blockStamp,owner,frozen," \
                "slp_type,emitter,receiver,legit,tp,sy,id,pa,mi," \
                "height,index,type,paused,symbol".split(",")


def find(collection, **kw):
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


################
# database API #
################

@srv.bind("/<str:collection>/find", methods=["GET"], app=srv.uJsonHandler)
def lookup(collection, **kw):
    try:
        return find(collection, **kw)
    except Exception as error:
        slp.LOG.error(
            "Error trying to fetch data : %s\n%s", kw, traceback.format_exc()
        )
        return {"status": 501, "msg": "Internal Error: %r" % error}


###########
# SLP API #
###########
# TODO: https://aslp.qredit.dev
# TODO: https://github.com/Qredit/qslp/blob/ark/public/aslp_openapi3.yaml

@srv.bind("/api/status", methods=["GET"], app=srv.uJsonHandler)
def status():
    cursor = dbapi.db.journal.find({})
    total = dbapi.db.journal.count_documents({})
    try:
        downloaded = list(cursor.sort("_id", -1))[0]["height"]
    except IndexError:
        downloaded = 0
    return {
        "downloadedBlocks": downloaded,
        "scannedBlocks": total
    }


@srv.bind("/api/tokens", methods=["GET"], app=srv.uJsonHandler)
def tokens(page=1, limit=50):
    page = int(page)
    limit = int(limit)
    # computes count and execute first filter
    total = dbapi.db.contracts.count_documents({})
    cursor = dbapi.db.contracts.find({})
    pages = int(math.ceil(total / float(limit)))
    # jump to asked page
    cursor = cursor.skip((page-1) * limit)

    data = functools.reduce(
        lambda a, b: a + b, [
            list(dbapi.token_details(contract["tokenId"]))
            for contract in cursor.limit(min(50, limit))
        ], []
    )
    return {
        "meta": {
            "count": len(data),
            "totalCount": total,
            "page": page,
            "pageCount": pages
        },
        "data": data
    }


@srv.bind("/api/token/<str:tokenId>", methods=["GET"], app=srv.uJsonHandler)
def token(tokenId):
    token = list(dbapi.token_details(tokenId))
    if len(token):
        token = token[0]
        if token["type"][-1] in ["2", ]:
            metadata = {}
            for reccord in [
                r for r in dbapi.db.slp2.find()
                if "metadata" in r
            ]:
                metadata.update(serde._unpack_meta(reccord["metadata"]))
            token["metadata"] = metadata
        return token
    else:
        return {"status": 400, "msg": "token %s not found" % tokenId}


@srv.bind("/api/tokenByTxid/<str:txId>", methods=["GET"], app=srv.uJsonHandler)
def token_by_txid(txId):
    reccord = dbapi.find_reccord(txid=txId)
    if reccord is None:
        return {"status": 400, "msg": "%s is not a SLP transaction" % txId}
    token = list(dbapi.token_details(reccord["id"]))
    if len(token):
        token = token[0]
        if token["type"][-1] in ["2", ]:
            metadata = {}
            for reccord in [
                r for r in dbapi.db.slp2.find()
                if "metadata" in r
            ]:
                metadata.update(serde._unpack_meta(reccord["metadata"]))
            token["metadata"] = metadata
        return token
    else:
        return {"status": 400, "msg": "token %s not found" % reccord["id"]}


@srv.bind(
    "/api/tokensByOwner/<str:addr>", methods=["GET"], app=srv.uJsonHandler
)
def tokens_by_owner(addr, page=1, limit=50):
    page = int(page)
    limit = int(limit)
    # computes count and execute first filter
    total = dbapi.db.contracts.count_documents({'owner': addr})
    cursor = dbapi.db.contracts.find({'owner': addr})
    pages = int(math.ceil(total / float(limit)))
    # jump to asked page
    cursor = cursor.skip((page-1) * limit)

    data = functools.reduce(
        lambda a, b: a + b, [
            list(dbapi.token_details(contract["tokenId"]))
            for contract in cursor.limit(min(100, limit))
        ], []
    )
    return {
        "meta": {
            "count": len(data),
            "totalCount": total,
            "page": page,
            "pageCount": pages
        },
        "data": data
    }


@srv.bind("/api/addresses", methods=["GET"], app=srv.uJsonHandler)
def addresses(page=1, limit=100, **kw):
    page = int(page)
    limit = int(limit)
    # computes count and execute first filter
    aggregation = list(dbapi.wallets(tokenId=kw.get("tokenId", None)))
    total = len(aggregation)
    pages = int(math.ceil(total / float(limit)))
    # jump to asked page
    start = (page-1) * limit
    data = aggregation[start:start + min(100, limit)]
    return {
        "meta": {
            "count": len(data),
            "totalCount": total,
            "page": page,
            "pageCount": pages
        },
        "data": data
    }


@srv.bind(
    "/api/addresses/<str:address>", methods=["GET"], app=srv.uJsonHandler
)
def address(address, page=1, limit=100):
    page = int(page)
    limit = int(limit)
    # computes count and execute first filter
    aggregation = list(dbapi.wallets(address))
    total = len(aggregation)
    pages = int(math.ceil(total / float(limit)))
    # jump to asked page
    start = (page-1) * limit
    data = aggregation[start:start + min(100, limit)]
    return {
        "meta": {
            "count": len(data),
            "totalCount": total,
            "page": page,
            "pageCount": pages
        },
        "data": data
    }


@srv.bind(
    "/api/balance/<str:tokenId>/<str:address>",
    methods=["GET"], app=srv.uJsonHandler
)
def balance(address, tokenId, page=1, limit=100, **kw):
    aggregation = list(dbapi.wallets(address, tokenId))
    if len(aggregation):
        return {"balance": aggregation[0]["balance"]}
    else:
        return {"status": 400, "msg": "balance not found"}


# "/transactions"
# "/transaction/{txid}"
# "/transactions/{tokenid}"
# "/transactions/{tokenid}/{address}"

# "/metadata/{txid}"
# "/metadata/{tokenid}"
# "/metadata/{tokenid}/{address}"

####################
# SMARTBRIDGES API #
####################

@srv.bind(
    "/smartBridge/<str:slp_type>/<str:tp>",
    methods=["GET"], app=srv.uJsonHandler
)
def slp_smartbridge(slp_type, tp, **kw):
    try:
        fields = dict(
            [k, v] for k, v in kw.items()
            if k in slp.JSON.ask("slp fields")
        )
        for key in [k for k in ["pa", "mi"] if k in fields]:
            fields[key] = fields[key] in ["TRUE", "True", "true", "1", 1, True]
        return {
            "smartBridge": getattr(serde, "pack_%s" % slp_type)(
                tp.upper(), **fields
            )
        }
    except Exception as error:
        slp.LOG.error(
            "Error trying to compute : %s\n%s", kw, traceback.format_exc()
        )
        return {"status": 501, "msg": "Internal Error: %r" % error}


@srv.bind(
    "/vendorField/<str:slp_type>/<str:tp>",
    methods=["GET"], app=srv.uJsonHandler
)
def slp_vendorfield(slp_type, tp, **kw):
    try:
        fields = dict(
            [k, v] for k, v in kw.items()
            if k in slp.JSON.ask("slp fields")
        )
        for key in [k for k in ["pa", "mi"] if k in fields]:
            fields[key] = fields[key] in ["TRUE", "True", "true", "1", 1, True]
        return {
            "vendorField": serde.unpack_slp(
                getattr(serde, "pack_%s" % slp_type)(tp.upper(), **fields)
            )
        }
    except Exception as error:
        slp.LOG.error(
            "Error trying to compute : %s\n%s", kw, traceback.format_exc()
        )
        return {"status": 501, "msg": "Internal Error: %r" % error}
