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
    except Exception:
        slp.LOG.error("SLP1 exec - Error occured: %s", traceback.format_exc())


def apply_genesis(contract, **options):
    tokenId = contract["id"]
    try:
        comment = "initial quantity should avoid decimal part"
        assert contract["qt"] % 1 == 0
        comment = "blockchain transaction amount have to match GENESIS cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("GENESIS", 1)
        comment = "GENESIS contract have to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
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
                    crossed=_decimal128(0.)
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
        comment = "burn quantity should avoid decimal part"
        assert contract["qt"] % 1 == 0
        comment = "blockchain transaction amount have to match BURN cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("BURN", 1)
        comment = "BURN contract have to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        # get contract and wallet
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} paused by owner"
        assert token.get("paused", False) is False
        wallet = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert wallet is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert wallet.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, wallet["blockStamp"])
        comment = "burn quantity greater than wallet balance"
        assert float(wallet["balance"].to_decimal()) >= contract["qt"]
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        # get Decimal128 builder associated to token id
        _decimal128 = slp.DECIMAL128[tokenId]
        check = [
            # remove quantity from owner wallet
            dbapi.update_slp1_wallet(
                contract["emitter"], tokenId, dict(
                    blockStamp=blockstamp, balance=_decimal128(
                        float(wallet["balance"].to_decimal()) - contract["qt"]
                    )
                )
            ),
            # update burned quantity on token contract
            dbapi.update_contract(
                tokenId, dict(
                    height=contract["height"], index=contract["index"],
                    burned=_decimal128(
                        float(token["burned"].to_decimal()) + contract["qt"]
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
        comment = f"{tokenId} token is not mintable"
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        assert reccord is not None and reccord["mi"] is True
        comment = "minted quantity should avoid decimal part"
        assert contract["qt"] % 1 == 0
        comment = "blockchain transaction amount have to match MINT cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("MINT", 1)
        comment = "MINT contract have to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} paused by owner"
        assert token.get("paused", False) is False
        wallet = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert wallet is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert wallet.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, wallet["blockStamp"])
        comment = "mint quantity overflows allowed supply"
        current_supply = (
            token["burned"].to_decimal() + token["minted"].to_decimal() +
            token["crossed"].to_decimal()
        )
        allowed_supply = token["globalSupply"].to_decimal()
        assert float(current_supply) + contract["qt"] <= float(allowed_supply)
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
    else:
        # get Decimal128 builder associated to token id
        _decimal128 = slp.DECIMAL128[tokenId]
        check = [
            # add quantity to owner wallet
            dbapi.update_slp1_wallet(
                contract["emitter"], tokenId, dict(
                    blockStamp=blockstamp, balance=slp.DECIMAL128[tokenId](
                        float(wallet["balance"].to_decimal()) + contract["qt"]
                    )
                )
            ),
            # update minted quantity on token contract
            dbapi.update_contract(
                tokenId, dict(
                    height=contract["height"], index=contract["index"],
                    minted=_decimal128(
                        float(token["minted"].to_decimal()) + contract["qt"]
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
        comment = "blockchain transaction amount have to match SEND cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("SEND", 1)
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} paused by owner"
        assert token.get("paused", False) is False
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert emitter is not None
        comment = f"wallet {contract['emitter']} frozen by owner"
        assert emitter.get("frozen", False) is False
        comment = f"wallet {contract['emitter']} balance is insufficient"
        assert emitter["balance"].to_decimal() > contract["qt"]
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # TODO: receiver is a valid address
        # chain.is_valid_address(contract["receiver"])
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
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
        comment = "blockchain transaction amount have to match NEWOWNER cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("NEWOWNER", 1)
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert emitter is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert emitter.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # RECEIVER check ---
        receiver = dbapi.find_slp1_wallet(
            address=contract["receiver"], tokenId=tokenId
        )
        # receiver is not already frozen
        if receiver is not None:
            comment = f"wallet {contract['receiver']} frozen by owner"
            assert receiver.get("frozen", False) is False
        # return True if assertion only asked (test if contract is valid)
        if options.get("assert_only", False):
            return True
    except AssertionError:
        contract["comment"] = comment
        slp.LOG.debug("!%s", contract)
        slp.LOG.error("invalid contract: %s", traceback.format_exc())
        return dbapi.set_legit({"_id": contract["_id"]}, False)
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
            ),
            dbapi.update_contract(
                tokenId, {
                    "height": contract["height"], "index": contract["index"],
                    "owner": receiver["address"]
                }
            )
        ]
        return dbapi.set_legit(contract, check.count(False) == 0)


def apply_freeze(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount have to match FREEZE cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("FREEZE", 1)
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} paused by owner"
        assert token.get("paused", False) is False
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert emitter is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert emitter.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # RECEIVER check ---
        receiver = dbapi.find_slp1_wallet(
            address=contract["receiver"], tokenId=tokenId
        )
        comment = f"wallet {contract['receiver']} does not exist"
        assert receiver is not None
        comment = f"wallet {contract['receiver']} already frozen by owner"
        assert receiver.get("frozen", False) is False
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
            contract, dbapi.update_slp1_wallet(
                receiver["address"], tokenId, {"frozen": True}
            )
        )


def apply_unfreeze(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        comment = "blockchain transaction amount have to match UNFREEZE cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("UNFREEZE", 1)
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} paused by owner"
        assert token.get("paused", False) is False
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert emitter is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert emitter.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
        # RECEIVER check ---
        receiver = dbapi.find_slp1_wallet(
            address=contract["receiver"], tokenId=tokenId
        )
        comment = f"wallet {contract['receiver']} does not exist"
        assert receiver is not None
        comment = f"wallet {contract['receiver']} not frozen by owner"
        assert receiver.get("frozen", False) is True
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
            contract, dbapi.update_slp1_wallet(
                receiver["address"], tokenId, {"frozen": False}
            )
        )


def apply_pause(contract, **options):
    tokenId = contract["id"]
    blockstamp = f"{contract['height']}#{contract['index']}"
    try:
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        comment = f"{tokenId} token is not pausable"
        assert reccord is not None and reccord["pa"] is True
        comment = "blockchain transaction amount have to match PAUSE cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("PAUSE", 1)
        comment = "PAUSE contract have to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} already paused by owner"
        assert token.get("paused", False) is False
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert emitter is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert emitter.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
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
        reccord = dbapi.find_reccord(id=tokenId, tp="GENESIS")
        comment = f"{tokenId} token is not pausable"
        assert reccord is not None and reccord["pa"] is True
        comment = "blockchain transaction amount have to match RESUME cost"
        assert contract["cost"] >= slp.JSON.ask(
            "cost", contract["height"]
        )[slp.SLP1].get("RESUME", 1)
        comment = "RESUME contract have to be sent to master address"
        assert contract["receiver"] == slp.JSON["master address"]
        # TOKEN check ---
        token = dbapi.find_contract(tokenId=tokenId)
        comment = f"token {tokenId} does not exist"
        assert token is not None
        comment = f"token {tokenId} already resumed by owner"
        assert token.get("paused", False) is True
        # EMITTER check ---
        emitter = dbapi.find_slp1_wallet(
            address=contract["emitter"], tokenId=tokenId
        )
        comment = f"wallet {contract['emitter']} does not exist"
        assert emitter is not None
        comment = f"wallet {contract['emitter']} is not the owner"
        assert emitter.get("owner", False) is True
        comment = f"invalid blockstamp {blockstamp} (too low)"
        assert dbapi.blockstamp_cmp(blockstamp, emitter["blockStamp"])
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
