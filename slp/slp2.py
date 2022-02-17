# -*- coding:utf-8 -*-

"""
SLP2 contract execution module.

A wallet is stored into slp2 database if it owns the SLP2 contract or if it is
allowed to edit SLP2 metadata. If no one of the two condition are valid, wallet
is removed from slp2 database.

Each wallet have a metadata field where it stores its metadata. The token id
metadata is obtained by the aggregation of metadata from all token-associated
wallets.

A record in slp2 database is indexed by wallet address and token id.
"""

import slp
import sys
import json
import traceback

from slp import dbapi
from slp.serde import _pack_varia, _unpack_meta


def _pack_meta(**data):
    serial = b""
    metadata = sorted(data.items(), key=lambda i: len("%s%s" % i))
    for key, value in metadata:
        serial += _pack_varia(key, value)
    return serial


def _token_check(tokenId, paused=False):
    # get token from contracts database
    token = dbapi.find_contract(tokenId=tokenId)
    # token exists
    assert token is not None
    # token not paused|resumed by owner
    assert token.get("paused", False) is paused
    return token


def _emitter_ownership_check(address, tokenId, blockstamp):
    # get wallet from slp2 database
    emitter = dbapi.find_slp2_wallet(address=address, tokenId=tokenId)
    # emitter exists
    assert emitter is not None
    # emitter is realy the owner
    assert emitter.get("owner", False) is True
    # check if contract blockstamp higher than emitter one
    assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
    return emitter


def _emitter_check(address, tokenId, blockstamp):
    # get wallet from slp2 database
    emitter = dbapi.find_slp2_wallet(address=address, tokenId=tokenId)
    # emitter exists
    assert emitter is not None
    # check if contract blockstamp higher than emitter one
    assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
    return emitter


def _receiver_check(address, tokenId, must_exist=True):
    # get wallet from slp2 database
    receiver = dbapi.find_slp2_wallet(address=address, tokenId=tokenId)
    if must_exist:
        assert receiver is not None
    return receiver


def manage(contract, **options):
    """
    Dispatch the contract according to its type.
    """
    try:
        assert dbapi.db is not None
        assert contract.get("legit", False) is None
        result = getattr(
            sys.modules[__name__], "apply_%s" % contract["tp"].lower()
        )(contract, **options)
        if result is False:
            dbapi.db.rejected.insert_one(contract)
        return result
    except AssertionError:
        slp.LOG.error("Contract %s already applied", contract)
    except AttributeError:
        slp.LOG.error("Unknown contract type %s", contract["tp"])
    except Exception:
        slp.LOG.error("SLP2 exec - Error occured: %s", traceback.format_exc())


def apply_genesis(contract, **options):
    tokenId = contract["id"]
    try:
        comment = "blockchain transaction amount has to match GENESIS cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("GENESIS", 1)
        comment = "GENESIS contract has to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("assertion error with %s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        check = [
            # add new contract
            dbapi.db.contracts.insert_one(
                dict(
                    tokenId=tokenId, height=contract["height"],
                    index=contract["index"], type=slp.SLP2,
                    name=contract["na"], symbol=contract["sy"],
                    owner=contract["emitter"], document=contract["du"],
                    notes=contract.get("no", None), paused=False
                )
            ),
            # add new owner wallet
            dbapi.db.slp2.insert_one(
                dict(
                    address=contract["emitter"], tokenId=tokenId,
                    blockStamp=f"{contract['height']}#{contract['index']}",
                    owner=True, metadata=b""
                )
            )
        ]
        # set contract as legit if no errors (insert_one returns False if
        # element already exists in database)
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_newowner(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match NEWOWNER cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("NEWOWNER", 1)
        comment = f"token {tokenId} does not exist or paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not the owner"
        emitter = _emitter_ownership_check(
            contract["emitter"], tokenId, blockstamp
        )
        # RECEIVER check ---
        receiver = _receiver_check(
            contract["receiver"], tokenId, must_exist=False
        )
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        check = []
        if receiver is None:
            check.append(
                dbapi.db.slp2.insert_one(
                    dict(
                        address=contract["receiver"], tokenId=tokenId,
                        blockStamp=blockstamp, owner=True, metadata=b""
                    )
                )
            )
            receiver = dbapi.find_slp2_wallet(
                address=contract["receiver"], tokenId=tokenId
            )
        check += [
            dbapi.update_slp2_wallet(
                emitter["address"], tokenId,
                {"owner": False, "blockStamp": blockstamp}
            ),
            dbapi.update_slp2_wallet(
                receiver["address"], tokenId,
                {"owner": True, "blockStamp": blockstamp}
            ),
            dbapi.update_contract(
                tokenId, {
                    "height": contract["height"], "index": contract["index"],
                    "owner": receiver["address"]
                }
            )
        ]
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_pause(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # GENESIS check ---
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        comment = f"{tokenId} token is not pausable"
        assert reccord is not None and reccord["pa"] is True
        comment = "blockchain transaction amount has to match PAUSE cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("PAUSE", 1)
        comment = "PAUSE contract has to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        comment = f"token {tokenId} does not exist or already paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not the owner"
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.update_contract(
                tokenId, {
                    "height": contract["height"], "index": contract["index"],
                    "paused": True
                }
            )
        )


def apply_resume(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match RESUME cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("RESUME", 1)
        # RESUME contract has to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        comment = f"token {tokenId} does not exist or already resumed by owner"
        _token_check(tokenId, paused=True)
        comment = \
            f"wallet {contract['emitter']} does not exist or not the owner"
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.update_contract(
                tokenId, {
                    "height": contract["height"], "index": contract["index"],
                    "paused": False
                }
            )
        )


def apply_authmeta(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match AUTHMETA cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("AUTHMETA", 1)
        comment = f"token {tokenId} does not exist or paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not the owner"
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        comment = f"wallet {contract['receiver']} already authorized"
        assert dbapi.find_slp2_wallet(
            address=contract["receiver"], tokenId=tokenId
        ) is None
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.db.slp2.insert_one(
                dict(
                    address=contract["receiver"], tokenId=tokenId,
                    blockStamp=blockstamp, owner=False, metadata=b""
                )
            )
        )


def apply_addmeta(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match ADDMETA cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("ADDMETA", 1)
        comment = " ADDMETA contract has to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        comment = f"token {tokenId} does not exist or paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not allowed"
        emitter = _emitter_check(contract["emitter"], tokenId, blockstamp)
        # try to read metadata using key/value pair in na/dt or json string in
        # dt
        if contract.get("na", None) not in [None, "", False]:
            metadata = _pack_meta(**{contract["na"]: contract["dt"]})
        else:
            data = json.loads(contract["dt"])
            comment = "metadata should be a dictionary instance"
            assert isinstance(data, dict)
            metadata = _pack_meta(**data)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.update_slp2_wallet(
                emitter["address"], tokenId, dict(
                    blockStamp=blockstamp,
                    metadata=emitter["metadata"] + metadata
                )
            )
        )


def apply_voidmeta(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match VOIDMETA cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("VOIDMETA", 1)
        comment = "VOIDMETA contract has to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        comment = f"token {tokenId} does not exist or paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not allowed"
        emitter = _emitter_check(contract["emitter"], tokenId, blockstamp)
        comment = f"blockchain transaction {contract['tx']} not found"
        reccord = dbapi.find_reccord(txid=contract["tx"])
        assert reccord is not None
        if reccord.get("na", None) not in [None, "", False]:
            keys = ["na"]
        else:
            data = json.loads(reccord["dt"])
            comment = "metadata should be a dictionary instance"
            assert isinstance(data, dict)
            keys = list(data.keys())
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        metadata = _unpack_meta(emitter["metadata"])
        for key in keys:
            metadata.pop(key, False)
        return dbapi.set_legit(
            contract, dbapi.update_slp2_wallet(
                emitter["address"], tokenId, dict(
                    blockStamp=blockstamp, metadata=_pack_meta(**metadata)
                )
            )
        )


def apply_revokemeta(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match REVOKEMETA cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("REVOKEMETA", 1)
        comment = f"token {tokenId} does not exist or paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not the owner"
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        comment = f"wallet {contract['receiver']} already unauthorized"
        receiver = _receiver_check(contract["receiver"], tokenId)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.db.slp2.delete_one(receiver)
        )


def apply_clone(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount has to match CLONE cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2].get("CLONE", 1)
        comment = "CLONE contract has to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        reccord = dbapi.find_reccord(tp="GENESIS", id=contract["id"])
        assert reccord is not None
        comment = f"token {tokenId} does not exist or paused by owner"
        _token_check(tokenId)
        comment = \
            f"wallet {contract['emitter']} does not exist or not the owner"
        emitter = _emitter_ownership_check(
            contract["emitter"], tokenId, blockstamp
        )
        if options.get("assert_only", False):
            return True
    except Exception:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        # compute the new id of cloned token
        new_tokenId = slp.get_token_id(
            reccord["slp_type"], reccord["sy"], contract["height"],
            contract["txid"]
        )
        # get all metadata
        metadata = b""
        for document in dbapi.db.slp2.find({"tokenId": tokenId}):
            metadata += document["metadata"]

        check = [
            # add new contract
            dbapi.db.contracts.insert_one(
                dict(
                    tokenId=new_tokenId, height=contract["height"],
                    index=contract["index"], type=slp.SLP2,
                    name=reccord["na"], symbol=reccord["sy"],
                    owner=emitter["address"], document=reccord["du"],
                    notes=reccord.get("no", None), paused=False
                )
            ),
            # add new owner wallet with the whome metadata
            dbapi.db.slp2.insert_one(
                dict(
                    address=emitter["address"], tokenId=new_tokenId,
                    blockStamp=f"{contract['height']}#{contract['index']}",
                    owner=True, metadata=metadata
                )
            )
        ]
        # set contract as legit if no errors (insert_one returns False if
        # element already exists in database)
        return dbapi.set_legit(contract, check.count(False) == 0)
