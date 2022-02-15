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
from slp.serde import _pack_varia


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
        # blockchain transaction amount have to match GENESIS cost
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP2]["GENESIS"]
        # GENESIS contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        slp.LOG.debug("assertion error with %s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
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
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
                receiver["address"], tokenId,
                {"owner": True, "blockStamp": blockstamp}
            ),
            dbapi.update_slp2_wallet(
                emitter["address"], tokenId,
                {"owner": False, "blockStamp": blockstamp}
            )
        ]
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_pause(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # GENESIS check ---
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        # token must be pausable
        assert reccord is not None and reccord["pa"] is True
        # PAUSE contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
        # RESUME contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        _token_check(tokenId, paused=True)
        # EMITTER check ---
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        # RECEIVER check ---
        # receiver should not exist in slp2 database
        assert dbapi.find_slp2_wallet(
            address=contract["receiver"], tokenId=tokenId
        ) is None
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
        # ADDMETA contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
        emitter = _emitter_check(contract["emitter"], tokenId, blockstamp)
        # try to read metadata using key/value pair in na/dt or json string in
        # dt
        if "na" in contract:
            metadata = _pack_meta(**{contract["na"]: contract["dt"]})
        else:
            metadata = _pack_meta(json.loads(contract["dt"]))
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
        # VOIDMETA contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
        emitter = _emitter_check(contract["emitter"], tokenId, blockstamp)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.update_slp2_wallet(
                emitter["address"], tokenId, dict(
                    blockStamp=blockstamp, metadata=b""
                )
            )
        )


def apply_revokemeta(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
        _emitter_ownership_check(contract["emitter"], tokenId, blockstamp)
        # RECEIVER check ---
        receiver = _receiver_check(contract["receiver"], tokenId)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.db.slp2.delete_one(receiver)
        )


def apply_clone(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # CLONE contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # get tokenId genesis reccord from journal
        reccord = dbapi.find_reccord(tp="GENESIS", id=contract["id"])
        assert reccord is not None
        # TOKEN check ---
        _token_check(tokenId)
        # EMITTER check ---
        emitter = _emitter_ownership_check(
            contract["emitter"], tokenId, blockstamp
        )
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
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
