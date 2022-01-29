# -*- coding:utf-8 -*-

import os
import slp
import json
import hashlib
import decimal
import traceback

# mongo database to be initialized by slp app
db = None


def set_legit(filter, value=True):
    value = bool(value)
    db.journal.update_one(filter, {'$set': {"legit": value}})
    return value


def blockstamp_cmp(a, b):
    """
    Blockstamp comparison. Returns True if a higher than b.
    """
    height_a, index_a = [int(e) for e in a.split("#")]
    height_b, index_b = [int(e) for e in b.split("#")]
    if height_a > height_b:
        return True
    elif height_a == height_b:
        return index_a >= index_b
    else:
        return False


def compute_poh(name, last_poh=None, **data):
    collection = getattr(db, name)
    if last_poh is None:
        try:
            last_poh = collection.find().sort("_id", -1)[0].get("poh", "")
        except Exception:
            last_poh = ""
    if "hash" not in data:
        seed = json.dumps(data, sort_keys=True, separators=(',', ':'))
        seed = hashlib.md5(seed.encode("utf-8")).hexdigest()
    else:
        seed = data["hash"]
    seed = last_poh + seed
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def add_reccord(
    height, index, txid, slp_type, timestamp, emitter, receiver, cost, **kw
):
    """
    Add a reccord in the journal.

    Args:
        height (int): block height.
        index (int): transaction index in the block.
        txid (str): transaction id as hex.
        slp_type (str): see SLP contract types.
        timestamp (float): unix timestamp.
        emitter (str): sender id wallet.
        receiver (str): recipient id wallet.
        cost (int): amount of transaction.
        **kw (keyword args): contract field values.

    Returns:
        bool: `True` if success else `False`.
    """
    fields = dict(
        [k, v] for k, v in kw.items()
        if k in slp.JSON.ask("slp fields", height)
    )

    if kw.get("tp", "") == "GENESIS":
        fields.update(pa=kw.get("pa", False))
        if slp_type.endswith("1"):
            fields.update(mi=kw.get("mi", False))

    if not slp.validate(**fields):
        slp.LOG.error("field validation did not pass")
        slp.dumpJson(
            dict(
                slp.loadJson(f"unvalidated.{slp_type}", ".json"),
                **{f"{height}#{index}": fields}
            ), f"unvalidated.{slp_type}", os.path.join(slp.ROOT, ".json")
        )
        return False

    try:
        contract = dict(
            poh=compute_poh("journal", **fields),
            height=height, index=index, txid=txid, slp_type=slp_type,
            emitter=emitter, receiver=receiver, timestamp=timestamp,
            cost=cost, legit=None, **fields
        )
        db.journal.insert_one(contract)
    except Exception as error:
        slp.LOG.error("%r", error)
        slp.LOG.debug("traceback data:\n%s", traceback.format_exc())
        return False
    else:
        return contract


def find_reccord(**filter):
    return db.journal.find_one(filter)


def find_contract(**filter):
    return db.contracts.find_one(filter)


def find_slp1_wallet(**filter):
    return db.slp1.find_one(filter)


def find_slp2_wallet(**filter):
    return db.slp2.find_one(filter)


def update_contract(tokenId, values):
    try:
        query = {"tokenId": tokenId}
        update = {"$set": dict(
            [k, v] for k, v in values.items()
            if k in "tokenId,height,index,type,name,owner,"
                    "globalSupply,paused,minted,burned,crossed"
        )}
        db.contracts.update_one(query, update)
    except Exception as error:
        slp.LOG.error("%r", error)
        slp.LOG.debug("traceback data:\n%s", traceback.format_exc())
        return False
    return True


def update_slp_wallet(collection, address, tokenId, values):
    try:
        query = {"tokenId": tokenId, "address": address}
        update = {"$set": dict(
            [k, v] for k, v in values.items()
            if k in "address,tokenId,blockStamp,balance,owner,frozen,metadata"
        )}
        getattr(db, collection).update_one(query, update)
    except Exception as error:
        slp.LOG.error("%r", error)
        slp.LOG.debug("traceback data:\n%s", traceback.format_exc())
        return False
    return True


def update_slp1_wallet(address, tokenId, values):
    return update_slp_wallet("slp1", address, tokenId, values)


def update_slp2_wallet(address, tokenId, values):
    return update_slp_wallet("slp2", address, tokenId, values)


def exchange_slp1_token(tokenId, sender, receiver, qt):
    # find sender wallet from database
    _sender = find_slp1_wallet(address=sender, tokenId=tokenId)
    # get Decimal128 builder according to token id and convert qt to Decimal
    _decimal128 = slp.DECIMAL128[tokenId]
    qt = decimal.Decimal(qt)

    if _sender:
        # find receiver wallet from database
        _receiver = find_slp1_wallet(address=receiver, tokenId=tokenId)
        # create it with needed
        if _receiver is None:
            db.slp1.insert_one(
                dict(
                    address=receiver, tokenId=tokenId, blockStamp="0#0",
                    balance=_decimal128(0.), owner=False, frozen=False
                )
            )
            new_balance = qt
        else:
            new_balance = _receiver["balance"].to_decimal() + qt
        # first update receiver
        if update_slp1_wallet(
            receiver, tokenId, {"balance": _decimal128(new_balance)}
        ):
            # if reception is a success, update emitter
            if update_slp1_wallet(
                sender, tokenId, {
                    "balance":
                        _decimal128(_sender["balance"].to_decimal() - qt)
                }
            ):
                # and return True if success
                return True
            else:
                # if error with sender update get back received token
                update_slp1_wallet(
                    receiver, tokenId, {
                        "balance":
                            _decimal128(_receiver["balance"].to_decimal() - qt)
                    }
                )
                return False

    slp.LOG.error(
        "%s wallet does not exists with contract %s", sender, tokenId
    )
    return False


def get_unix_time(blockstamp, peer=None):
    height, index = [int(e) for e in blockstamp.split("#")]
    reccord = find_reccord(height=height, index=index)
    if reccord:
        return reccord.get("timestamp", None)
