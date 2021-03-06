# -*- coding: utf-8 -*-
# Zilliqa Mining Proxy
# Copyright (C) 2019  Gully Chen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


from collections import defaultdict
from datetime import datetime, timedelta

import mongoengine as mg
from mongoengine import Q

from .basemodel import ModelMixin


"""
A Miner -> Many Workers
A Worker -> Time series of hashrate
"""


class Miner(ModelMixin, mg.Document):
    meta = {"collection": "zil_miners", "strict": False}

    wallet_address = mg.StringField(max_length=128, required=True, unique=True)
    rewards = mg.FloatField(default=0.0)
    paid = mg.FloatField(default=0.0)
    authorized = mg.BooleanField(default=True)

    nick_name = mg.StringField(max_length=64, default="")
    email = mg.StringField(max_length=128)
    email_verified = mg.BooleanField(default=False)
    join_date = mg.DateTimeField()

    workers_name = mg.ListField()
    # stats
    work_submitted = mg.IntField(default=0)
    work_failed = mg.IntField(default=0)
    work_finished = mg.IntField(default=0)
    work_verified = mg.IntField(default=0)

    def __str__(self):
        return f"[Miner: {self.wallet_address}, {self.authorized}]"

    @classmethod
    def get_or_create(cls, wallet_address: str, worker_name: str,
                      nick_name="", email="", authorized=True):
        worker = Worker.get_or_create(wallet_address, worker_name)
        if worker:
            miner = cls.objects(
                wallet_address=wallet_address
            ).modify(
                upsert=True, new=True,
                set__wallet_address=wallet_address,
                set__authorized=authorized,
                set__nick_name=nick_name,
                set__email=email
            )
            if miner.join_date is None:
                miner.join_date = datetime.utcnow()
            if worker_name not in miner.workers_name:
                miner.workers_name.append(worker_name)
            return miner.save()
        return None

    @property
    def workers(self):
        return Worker.get_all(wallet_address=self.wallet_address)

    def works_stats(self):
        return {
            "work_submitted": self.work_submitted,
            "work_failed": self.work_failed,
            "work_finished": self.work_finished,
            "work_verified": self.work_verified,
        }

    def update_stat(self, inc_submitted=0, inc_failed=0, inc_finished=0, inc_verified=0):
        update_kwargs = {
            "inc__work_submitted": inc_submitted,
            "inc__work_failed": inc_failed,
            "inc__work_finished": inc_finished,
            "inc__work_verified": inc_verified,
        }
        update_kwargs = {key: value for (key, value) in update_kwargs.items() if value > 0}
        return self.update(**update_kwargs)


class Worker(ModelMixin, mg.Document):
    meta = {"collection": "zil_mine_workers", "strict": False}

    wallet_address = mg.StringField(max_length=128, required=True)
    worker_name = mg.StringField(max_length=64, default="")

    work_submitted = mg.IntField(default=0)
    work_failed = mg.IntField(default=0)
    work_finished = mg.IntField(default=0)
    work_verified = mg.IntField(default=0)

    def __str__(self):
        return f"[Worker: {self.worker_name}.{self.wallet_address}]"

    @property
    def miner(self):
        return Miner.get_one(wallet_address=self.wallet_address)

    @classmethod
    def get_or_create(cls, wallet_address: str, worker_name: str):
        worker = cls.objects(
            wallet_address=wallet_address,
            worker_name=worker_name
        ).modify(
            upsert=True, new=True,
            set__wallet_address=wallet_address,
            set__worker_name=worker_name
        )
        return worker

    @classmethod
    def active_count(cls):
        three_hours = datetime.utcnow() - timedelta(hours=3)

        match = {
            "updated_time": {
                "$gte": three_hours,
            }
        }
        group = {
            "_id": {"wallet_address": "$wallet_address",
                    "worker_name": "$worker_name"},
        }

        return HashRate.aggregate_count(match, group)

    def update_stat(self, inc_submitted=0, inc_failed=0, inc_finished=0, inc_verified=0):
        update_kwargs = {
            "inc__work_submitted": inc_submitted,
            "inc__work_failed": inc_failed,
            "inc__work_finished": inc_finished,
            "inc__work_verified": inc_verified,
        }
        update_kwargs = {key: value for (key, value) in update_kwargs.items() if value > 0}
        if self.update(**update_kwargs):
            # update miner's stats
            self.miner.update_stat(
                inc_submitted=inc_submitted,
                inc_failed=inc_failed,
                inc_finished=inc_finished,
                inc_verified=inc_verified
            )

    def works_stats(self):
        return {
            "work_submitted": self.work_submitted,
            "work_failed": self.work_failed,
            "work_finished": self.work_finished,
            "work_verified": self.work_verified,
        }


class HashRate(ModelMixin, mg.Document):
    meta = {"collection": "zil_mine_hashrate", "strict": False}

    wallet_address = mg.StringField(max_length=128, required=True)
    worker_name = mg.StringField(max_length=64, default="")

    hashrate = mg.IntField(default=0.0, required=True)
    updated_time = mg.DateTimeField()

    @classmethod
    def log(cls, hashrate: int, wallet_address: str, worker_name: str):
        if hashrate < 0:
            return False
        _miner = Miner.get(wallet_address=wallet_address)
        if not _miner:
            return False
        _worker = Worker.get_or_create(wallet_address, worker_name)
        if not _worker:
            return False

        hr = cls(wallet_address=wallet_address, worker_name=worker_name,
                 hashrate=hashrate, updated_time=datetime.utcnow())
        return hr.save()

    @classmethod
    def epoch_hashrate(cls, block_num=None, wallet_address=None, worker_name=None):
        from .pow import PoWWindow

        pow_start, pow_end = PoWWindow.get_pow_window(block_num)
        if not pow_start or not pow_end:
            return 0

        match = {
            "updated_time": {
                "$gte": pow_start,
                "$lte": pow_end,
            }
        }

        if wallet_address is not None:
            match.update({
                "wallet_address": {
                    "$eq": wallet_address,
                }
            })

        if worker_name is not None:
            match.update({
                "worker_name": {
                    "$eq": worker_name,
                }
            })

        group = {
            "_id": {"wallet_address": "$wallet_address",
                    "worker_name": "$worker_name", },
            "hashrate": {"$max": "$hashrate"}
        }
        group_sum = {
            "_id": None,
            "hashrate": {"$sum": "$hashrate"}
        }

        pipeline = [
            {"$match": match},
            {"$group": group},
            {"$group": group_sum}
        ]

        res = list(cls.objects.aggregate(*pipeline))
        return res[0]["hashrate"] if res else 0
