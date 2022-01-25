# -*- coding:utf-8 -*-

import io
import os
import re
import json
import socket
import logging
import hashlib

DECIMAL128 = {}
INPUT_TYPES = {}
TYPES_INPUT = {}

PUBLIC_IP = "127.0.0.1"
PORT = 5000
LOG = logging.getLogger("slp")
ROOT = os.path.abspath(os.path.dirname(__file__))
BLOCKCHAIN_NODE = False
REGEXP = re.compile(".*")
VALIDATION = {
    "id": lambda value: re.match(r"^[0-9a-fA-F]{32}$", value) is not None,
    "qt": lambda value: isinstance(value, (int, float)),
    "de": lambda value: 0 <= value <= 8,
    "sy": lambda value: re.match(r"^[0-9a-zA-Z]{3,8}$", value) is not None,
    "na": lambda value: re.match(r"^.{3,24}$", value) is not None,
    "du": lambda value: (value == "") or (
        re.match(
            r"(https?|ipfs|ipns|dweb):\/\/[a-z0-9\/:%_+.,#?!@&=-]{3,180}",
            value
        )
    ) is not None,
    "no": lambda value: re.match(r"^.{0,180}$", value) is not None,
    "pa": lambda value: value in [True, False, 0, 1],
    "mi": lambda value: value in [True, False, 0, 1],
    "ch": lambda value: isinstance(value, int),
    "dt": lambda value: re.match(r"^.{0,256}$", value) is not None
}
HEADERS = {
    "API-Version": "3",
    "Content-Type": "application/json",
    "User-Agent": "Python/usrv - Side Ledger Protocol"
}


def validate(**fields):
    tests = dict(
        [k, VALIDATION[k](v)] for k, v in fields.items() if k in VALIDATION
    )
    LOG.debug("validation result: %s", tests)
    return list(tests.values()).count(False) == 0


def get_extern_ip():
    ip = '127.0.0.1'
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        pass
    finally:
        s.close()
    return ip


def loadJson(name, folder=None):
    filename = os.path.join(ROOT if not folder else folder, name)
    if os.path.exists(filename):
        with io.open(filename, "r", encoding="utf-8") as in_:
            data = json.load(in_)
    else:
        data = {}
    return data


def dumpJson(data, name, folder=None):
    filename = os.path.join(ROOT if not folder else folder, name)
    try:
        os.makedirs(os.path.dirname(filename))
    except OSError:
        pass
    with io.open(filename, "w", encoding="utf-8") as out:
        json.dump(data, out, indent=4)


def get_token_id(slp_type, symbol, blockheight, txid):
    """
    Generate token id.
    """
    raw = "%s.%s.%s.%s" % (slp_type.upper(), symbol, blockheight, txid)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def merge_milestone(a, b):
    merged = dict(a)
    for k, v in b.items():
        if k in merged:
            if type(merged[k]) != type(v):
                raise Exception("Unmergeable data")
            if isinstance(v, dict):
                merged[k].update(v)
            elif isinstance(v, list):
                merged[k].extend(v)
                merged[k] = list(set(merged[k]))
            else:
                merged[k] = v
        else:
            merged[k] = v
    return merged


class Config(dict):

    def load(self, name, **overrides):

        data = loadJson(f"{name}.json")
        if len(data) == 0:
            LOG.error("Missing JSON configuration file for %s", name)
            raise Exception("No configuration file found for %s" % name)

        previous_milestone = {}
        data["milestones"] = {1: previous_milestone}
        milestones = loadJson("milestones.json")

        for milestone in sorted(milestones, key=lambda m: m["height"]):
            height = milestone.pop("height")
            milestone = merge_milestone(
                previous_milestone, milestone
            )
            data["milestones"][height] = milestone
            previous_milestone = milestone

        self.clear()
        self.update(dict(data, **overrides))

    def ask(self, key, height=None):
        if key in self:
            return dict.__getitem__(self, key)
        else:
            if height is None:
                height = max(dict.__getitem__(self, "milestones").keys())
            else:
                try:
                    height = [
                        h for h in dict.__getitem__(self, "milestones", {})
                        if h <= height
                    ][-1]
                except Exception:
                    raise Exception("value not found in milestones")
            return dict.__getitem__(self, "milestones").get(height, {})[key]


JSON = Config()
