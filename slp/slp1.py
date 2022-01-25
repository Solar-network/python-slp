# -*- coding:utf-8 -*-

"""
SLP1 contract execution module.
"""

import slp
import sys
import traceback

from slp import dbapi
from bson import Decimal128


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


def apply_genesis(contract, **options):
    tokenId = contract["id"]
    try:
        # initial quantity should avoid decimal part
        assert contract["qt"] % 1 == 0
        # blockchain transaction amount have to match GENESIS cost
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1]["GENESIS"]
        # GENESIS contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        # register Decimal128 for accounting precision
        slp.DECIMAL128[tokenId] = lambda v, de=contract.get('de', 0): \
            Decimal128(f"%.{de}f" % v)
        # get the token id associated decimal128 builder and convert qt value
        _decimal128 = slp.DECIMAL128[tokenId]
        # convert global sypply as decimal128 and compute minted supply. If
        # token is not mintable, mint global supply on contract creation and
        # credit with global supply on owner wallet creation
        globalSupply = _decimal128(contract["qt"])
        minted = _decimal128(0.) if contract.get("mi", False) else globalSupply
        # add new contract and new owner wallet into database
        check = [
            dbapi.db.contracts.insert_one(
                dict(
                    tokenId=tokenId, height=contract["height"],
                    index=contract["index"], type=slp.SLP1,
                    name=contract["na"], symbol=contract["sy"],
                    owner=contract["emitter"], globalSupply=globalSupply,
                    document=contract["du"], notes=contract.get("no", None),
                    paused=False, minted=minted, burned=_decimal128(0.),
                    exited=_decimal128(0.)
                )
            ),
            dbapi.db.slp1.insert_one(
                dict(
                    address=contract["emitter"], tokenId=tokenId,
                    blockStamp=f"{contract['height']}#{contract['index']}",
                    balance=minted, owner=True, frozen=False
                )
            )
        ]
        # set contract as legit if no errors (insert_one returns False if
        # element already exists in database)
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_burn(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # burned quantity should avoid decimal part
        assert contract["qt"].to_decimal() % 1 == 0
        # BURN contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # get contract and wallet
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is False
        wallet = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # wallet exists
        assert wallet is not None
        # wallet is realy the owner
        assert wallet.get("owner", False) is True
        # check if contract blockstamp higher than wallet one
        assert dbapi.blockstamp_cmp(blockstamp, wallet["blockStamp"])
        # owner may burn only from his balance
        assert wallet["balance"].to_decimal() >= contract["qt"]
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        # get Decimal128 builder associated to token id
        _decimal128 = slp.DECIMAL128[tokenId]
        check = [
            # remove quantity from owner wallet
            dbapi.update_slp1_wallet(
                contract["emitter"], tokenId, dict(
                    blockStamp=blockstamp, balance=_decimal128(
                        wallet["balance"].to_decimal() - contract["qt"]
                    )
                )
            ),
            # update burned quantity on token contract
            dbapi.update_contract(
                tokenId, dict(
                    height=contract["height"], index=contract["index"],
                    burned=_decimal128(
                        token["burned"].to_decimal() + contract["qt"]
                    )
                )
            )
        ]
        # set contract as legit if no errors (update_contract and
        # update_slp1_wallet return False if document not added to database)
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_mint(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # GENESIS check ---
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        assert reccord is not None and reccord["mintable"] is True
        # minted quantity should avoid decimal part
        assert contract["qt"].to_decimal() % 1 == 0
        # BURN contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is False
        wallet = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # wallet exists
        assert wallet is not None
        # wallet is realy the owner
        assert wallet.get("owner", False) is True
        # check if contract blockstamp higher than wallet one
        assert dbapi.blockstamp_cmp(blockstamp, wallet["blockStamp"])
        # owner may mint accourding to global supply limit
        current_supply = (
            token["burned"].to_decimal() + token["minted"].to_decimal() +
            token["exited"].to_decimal()
        )
        allowed_supply = token["globalSupply"].to_decimal()
        assert current_supply + contract["qt"] <= allowed_supply
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        # get Decimal128 builder associated to token id
        _decimal128 = slp.DECIMAL128[tokenId]
        check = [
            # add quantity to owner wallet
            dbapi.update_slp1_wallet(
                contract["emitter"], tokenId, dict(
                    blockStamp=blockstamp, balance=slp.DECIMAL128[tokenId](
                        wallet["balance"].to_decimal() + contract["qt"]
                    )
                )
            ),
            # update minted quantity on token contract
            dbapi.update_contract(
                tokenId, dict(
                    height=contract["height"], index=contract["index"],
                    minted=_decimal128(
                        token["minted"].to_decimal() + contract["qt"]
                    )
                )
            )
        ]
        # set contract as legit if no errors (update_contract and
        # update_slp1_wallet return False if document not added to database)
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_send(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is False
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # emitter exists
        assert emitter is not None
        # emitter not frozen by owner
        assert emitter.get("frozen", False) is False
        # emitter balance is okay
        assert emitter["balance"].to_decimal() > contract["qt"]
        # check if contract blockstamp higher than emitter one
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # TODO: receiver is a valid address
        # chain.is_valid_address(contract["receiver"])
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        check = [
            dbapi.exchange_slp1_token(
                tokenId, contract["emitter"], contract["receiver"],
                contract["qt"]
            )
        ]
        if check.count(False) == 0:
            check += [
                dbapi.update_slp1_wallet(
                    contract["emitter"], tokenId, {"blockStamp": blockstamp}
                ),
                dbapi.update_slp1_wallet(
                    contract["receiver"], tokenId, {"blockStamp": blockstamp}
                )
            ]
        # set contract as legit if no errors (exchange_slp1_token and
        # update_slp1_wallet return False if document not added to database)
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_newowner(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # emitter exists
        assert emitter is not None
        # emitter is realy the owner
        assert emitter.get("owner", False) is True
        # check if contract blockstamp higher than emitter one
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # RECEIVER check ---
        receiver = dbapi.find_slp1_wallet(
            address=contract["receiver"], tokenId=tokenId
        )
        # receiver is not already frozen
        if receiver is not None:
            assert receiver.get("frozen", False) is False
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        blockstamp = f"{contract['height']}#{contract['index']}"
        check = [
            dbapi.exchange_slp1_token(
                tokenId, contract["emitter"], contract["receiver"],
                emitter["balance"].to_decimal()
            ),
            dbapi.update_slp1_wallet(
                emitter["address"], tokenId,
                {"owner": False, "blockStamp": blockstamp}
            ),
            dbapi.update_slp1_wallet(
                receiver["address"], tokenId,
                {"owner": True, "blockStamp": blockstamp}
            )
        ]
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_freeze(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is False
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # emitter exists
        assert emitter is not None
        # emitter is realy the owner
        assert emitter.get("owner", False) is True
        # check if contract blockstamp higher than emitter one
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # RECEIVER check ---
        receiver = dbapi.find_slp1_wallet(
            address=contract["receiver"], tokenId=tokenId
        )
        # receiver exists
        assert receiver is not None
        # receiver is not already frozen
        assert receiver.get("frozen", False) is False
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.update_slp1_wallet(
                receiver["address"], tokenId, {"frozen": True}
            )
        )


def apply_unfreeze(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is False
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # emitter exists
        assert emitter is not None
        # emitter is realy the owner
        assert emitter.get("owner", False) is True
        # check if contract blockstamp higher than emitter one
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # RECEIVER check ---
        receiver = dbapi.find_slp1_wallet(
            address=contract["receiver"], tokenId=tokenId
        )
        # receiver exists
        assert receiver is not None
        # receiver is not already frozen
        assert receiver.get("frozen", False) is True
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except Exception:
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit(contract, False)
    else:
        return dbapi.set_legit(
            contract, dbapi.update_slp1_wallet(
                receiver["address"], tokenId, {"frozen": False}
            )
        )


def apply_pause(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        # GENESIS check ---
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        assert reccord is not None and reccord["pa"] is True
        # PAUSE contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is False
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # emitter exists
        assert emitter is not None
        # emitter is realy the owner
        assert emitter.get("owner", False) is True
        # check if contract blockstamp higher than emitter one
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
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
        # GENESIS check ---
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        assert reccord is not None and reccord["pa"] is True
        # RESUME contract have to be sent to master address
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        # token exists
        assert token is not None
        # token not paused by owner
        assert token.get("paused", False) is True
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        # emitter exists
        assert emitter is not None
        # emitter is realy the owner
        assert emitter.get("owner", False) is True
        # check if contract blockstamp higher than emitter one
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
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
