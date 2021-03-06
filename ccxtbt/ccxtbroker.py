#!/usr/bin/env python
# -*- coding: utf-8; py-indent-offset:4 -*-
###############################################################################
#
# Copyright (C) 2015, 2016, 2017 Daniel Rodriguez
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
#
###############################################################################
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import collections
import json

from backtrader import BrokerBase, OrderBase, Order
from backtrader.position import Position
from backtrader.utils.py3 import queue, with_metaclass

from .ccxtstore import CCXTStore


class CCXTOrder(OrderBase):
    def __init__(self, owner, data, ccxt_order):
        self.owner = owner
        self.data = data
        self.ccxt_order = ccxt_order
        self.executed_fills = []
        self.ordtype = self.Buy if ccxt_order['side'] == 'buy' else self.Sell
        self.size = float(ccxt_order['amount'])

        super(CCXTOrder, self).__init__()


class MetaCCXTBroker(BrokerBase.__class__):
    def __init__(cls, name, bases, dct):
        '''Class has already been created ... register'''
        # Initialize the class
        super(MetaCCXTBroker, cls).__init__(name, bases, dct)
        CCXTStore.BrokerCls = cls


class CCXTBroker(with_metaclass(MetaCCXTBroker, BrokerBase)):
    '''Broker implementation for CCXT cryptocurrency trading library.
    This class maps the orders/positions from CCXT to the
    internal API of ``backtrader``.

    Broker mapping added as I noticed that there differences between the expected
    order_types and retuned status's from canceling an order

    Added a new mappings parameter to the script with defaults.

    Added a get_balance function. Manually check the account balance and update brokers
    self.cash and self.value. This helps alleviate rate limit issues.

    Added a new get_wallet_balance method. This will allow manual checking of the any coins
        The method will allow setting parameters. Useful for dealing with multiple assets

    Modified getcash() and getvalue():
        Backtrader will call getcash and getvalue before and after next, slowing things down
        with rest calls. As such, th

    The broker mapping should contain a new dict for order_types and mappings like below:

    broker_mapping = {
        'order_types': {
            bt.Order.Market: 'market',
            bt.Order.Limit: 'limit',
            bt.Order.Stop: 'stop-loss', #stop-loss for kraken, stop for bitmex
            bt.Order.StopLimit: 'stop limit'
        },
        'mappings':{
            'closed_order':{
                'key': 'status',
                'value':'closed'
                },
            'canceled_order':{
                'key': 'result',
                'value':1}
                }
        }

    Added new private_end_point method to allow using any private non-unified end point

    '''

    order_types = {Order.Market: 'market',
                   Order.Limit: 'limit',
                   Order.Stop: 'stop',  # stop-loss for kraken, stop for bitmex
                   Order.StopLimit: 'stop limit'}

    mappings = {
        'closed_order': {
            'key': 'status',
            'value': 'closed'
        },
        'canceled_order': {
            'key': 'status',
            'value': 'canceled'}
    }

    def __init__(self, broker_mapping=None, debug=True, **kwargs):
        super(CCXTBroker, self).__init__()

        if broker_mapping is not None:
            try:
                self.order_types = broker_mapping['order_types']
            except KeyError:  # Might not want to change the order types
                pass
            try:
                self.mappings = broker_mapping['mappings']
            except KeyError:  # might not want to change the mappings
                pass

        self.store = CCXTStore(**kwargs)

        self.currency = self.store.currency

        self.positions = collections.defaultdict(Position)

        self.debug = debug
        self.indent = 4  # For pretty printing dictionaries

        self.notifs = queue.Queue()  # holds orders which are notified

        self.open_orders = list()

        self.startingcash = self.store._cash
        self.startingvalue = self.store._value

    def get_balance(self):
        self.store.get_balance()
        self.cash = self.store._cash
        self.value = self.store._value
        return self.cash, self.value

    def get_wallet_balance(self, currency, params={}):
        balance = self.store.get_wallet_balance(currency, params=params)
        cash = balance['free'][currency] if balance['free'][currency] else 0
        value = balance['total'][currency] if balance['total'][currency] else 0
        return cash, value

    def getcash(self):
        # Get cash seems to always be called before get value
        # Therefore it makes sense to add getbalance here.
        # return self.store.getcash(self.currency)
        self.cash = self.store._cash
        return self.cash

    def getvalue(self, datas=None):
        # return self.store.getvalue(self.currency)
        self.value = self.store._value
        return self.value

    def get_notification(self):
        try:
            return self.notifs.get(False)
        except queue.Empty:
            return None

    def notify(self, order):
        self.notifs.put(order)

    def getposition(self, data, clone=True):
        # return self.o.getposition(data._dataname, clone=clone)
        pos = self.positions[data._dataname]
        if clone:
            pos = pos.clone()
        return pos

    def get_positions(self):
        return self.store.get_binance_positions()

    def next(self):
        if self.debug:
            print('Broker next() called')

        for o_order in list(self.open_orders):
            oID = o_order.ccxt_order['id']

            # Print debug before fetching so we know which order is giving an
            # issue if it crashes
            if self.debug:
                print('Fetching Order ID: {}'.format(oID))

            # Get the order
            ccxt_order = self.store.fetch_order(oID, o_order.data.p.dataname,
                                                params={'type': 'future'})  # ADDED PARAMS HERE
            # Check for new fills
            if self.debug:
                print(f'checking for trades in ccxt_order - ccxt_order is {ccxt_order}')

            if ccxt_order['trades'] is not None:  # added explicit check for None otherwise it was proceeding

                if self.debug:
                    print('reading trades in ccxt_order')

                for fill in ccxt_order['trades']:
                    if fill not in o_order.executed_fills:
                        o_order.execute(fill['datetime'], fill['amount'], fill['price'],
                                        0, 0.0, 0.0,
                                        0, 0.0, 0.0,
                                        0.0, 0.0,
                                        0, 0.0)
                        o_order.executed_fills.append(fill['id'])

            if self.debug:
                print(json.dumps(ccxt_order, indent=self.indent))

            # Check if the order is closed
            if ccxt_order[self.mappings['closed_order']['key']] == self.mappings['closed_order']['value']:
                # https://github.com/Dave-Vallance/bt-ccxt-store/compare/master...robobit:master
                # check if order is closed and add commission info
                if self.debug:
                    print('checking for closed status to notify of trade')
                pos = self.getposition(o_order.data, clone=False)
                pos.update(o_order.size, o_order.price)
                o_order.completed()
                self.notify(o_order)
                self.open_orders.remove(o_order)
                self.get_balance()

            # Manage case when an order is being Canceled from the Exchange
            #  from https://github.com/juancols/bt-ccxt-store/
            if ccxt_order[self.mappings['canceled_order']['key']] == self.mappings['canceled_order']['value']:
                if self.debug:
                    print('checking for canceled status to notify of trade')
                self.open_orders.remove(o_order)
                o_order.cancel()
                self.notify(o_order)

    def _submit(self, owner, data, exectype, side, amount, price, params):

        if amount == 0 or price == 0:  # malformed order case
            return None

        if self.debug:
            print(f'entering _submit method')

        order_type = self.order_types.get(exectype)  # if exectype else 'market'
        if order_type is None:
            order_type = 'market'
        created = int(data.datetime.datetime(0).timestamp() * 1000)
        # Extract CCXT specific params if passed to the order
        params = params['params'] if 'params' in params else params
        params['created'] = created  # Add timestamp of order creation for backtesting
        ret_ord = self.store.create_order(symbol=data.p.dataname, order_type=order_type, side=side,
                                          amount=amount, price=price, params=params)

        _order = self.store.fetch_order(ret_ord['id'], data.p.dataname, params=params)  # MODIFIED - ADDED PARAMS

        order = CCXTOrder(owner, data, _order)
        order.price = ret_ord['price']
        self.open_orders.append(order)

        self.notify(order)
        if self.debug:
            print(f'returning from _submit method')
        return order

    def buy(self, owner, data, size, price=None, plimit=None,
            exectype=None, valid=None, tradeid=0, oco=None,
            trailamount=None, trailpercent=None,
            **kwargs):
        # del kwargs['parent']
        # del kwargs['transmit']
        kwargs.__delitem__('parent') if 'parent' in kwargs else print('parent not there')
        kwargs.__delitem__('transmit') if 'transmit' in kwargs else print('transmit not there')

        if self.debug:
            print(f'buy order: {owner} | {data} | {exectype} | {size} @ {price} | {kwargs}')

        return self._submit(owner, data, exectype, 'buy', size, price, kwargs)

    def sell(self, owner, data, size, price=None, plimit=None,
             exectype=None, valid=None, tradeid=0, oco=None,
             trailamount=None, trailpercent=None,
             **kwargs):
        # del kwargs['parent']
        # del kwargs['transmit']
        kwargs.__delitem__('parent') if 'parent' in kwargs else print('parent not there')
        kwargs.__delitem__('transmit') if 'transmit' in kwargs else print('transmit not there')

        if self.debug:
            print(f'sell order: {owner} | {data} | {exectype} | {size} @ {price} | {kwargs}')

        return self._submit(owner, data, exectype, 'sell', size, price, kwargs)

    def close(self, owner, data, size, all=False, **kwargs):

        if self.debug:
            print(f'close trade: {owner} | {data} | {size} | close all? {all} | {kwargs}')

        open_positions = self.store.get_binance_positions()

        if all:
            print(f'we want to close all positions')
            poses = [{p['symbol']: p['positionAmt']} for p in open_positions]
            if self.debug:
                print(f'our positions {poses}')
            # todo finish this
            return None
        symbol = data.p.dataname.replace('/', '')
        print(f'data = {symbol}')
        try:
            possize = [p['positionAmt'] for p in open_positions if p['symbol'] == symbol][0]
        except IndexError:
            possize = None
            if self.debug:
                print(f'IndexError: list index out of range - symbol {symbol} not in current positions')
                print(f'no position to close')
            return None

        size = abs(size if size is not None else possize)

        if possize > 0:
            return self.sell(owner=owner, data=data, size=size, **kwargs)
        elif possize < 0:
            return self.buy(owner=owner, data=data, size=size, **kwargs)
        elif possize is None:
            if self.debug:
                print(f'no position to close')
            return None

        return None

    def cancel(self, order, params={}):

        if params is None:
            params = {}
        oID = order.ccxt_order['id']

        if self.debug:
            print('Broker cancel() called')
            print('Fetching Order ID: {}'.format(oID))

        # check first if the order has already been filled otherwise an error
        # might be raised if we try to cancel an order that is not open.
        ccxt_order = self.store.fetch_order(oID, order.data.p.dataname, params)

        if self.debug:
            print(json.dumps(ccxt_order, indent=self.indent))

        if ccxt_order[self.mappings['closed_order']['key']] == self.mappings['closed_order']['value']:
            return order

        ccxt_order = self.store.cancel_order(oID, order.data.p.dataname, params)

        if self.debug:
            print(json.dumps(ccxt_order, indent=self.indent))
            print('Value Received: {}'.format(ccxt_order[self.mappings['canceled_order']['key']]))
            print('Value Expected: {}'.format(self.mappings['canceled_order']['value']))

        if ccxt_order[self.mappings['canceled_order']['key']] == self.mappings['canceled_order']['value']:
            try:
                # todo fix for the cases when an order was cancelled via UI
                self.open_orders.remove(order)
                order.cancel()
                self.notify(order)
            except ValueError:
                print(f'self.open_orders\n{self.open_orders}')
                print(f'in cancel order, order is:\n{order}')

        return order

    def get_orders_open(self, safe=False, symbol=None):
        # todo account for multiple symbols passed to symbol
        ret = self.store.fetch_open_orders(symbol=symbol)
        if symbol:
            return [o for o in ret if o["symbol"] == symbol]
        return ret

    def update_open_orders_force(self, owner, data, simulated=False, filter_by_data_symbol=False):
        filter_symbol = data.symbol if filter_by_data_symbol else None
        orders = self.get_orders_open(symbol=filter_symbol)
        self.open_orders = list()
        for o in orders:
            _order = self.store.fetch_order(o['id'], o['symbol'])
            order = CCXTOrder(owner, data, _order, simulated=simulated)
            if _order[self.mappings['open_order']['key']] == self.mappings['open_order']['value']:
                order.accept(broker=self)
            else:
                pass

            self.open_orders.append(order)
            self.notify(order)

    def private_end_point(self, type, endpoint, params):
        '''
        Open method to allow calls to be made to any private end point.
        See here: https://github.com/ccxt/ccxt/wiki/Manual#implicit-api-methods

        - type: String, 'Get', 'Post','Put' or 'Delete'.
        - endpoint = String containing the endpoint address eg. 'order/{id}/cancel'
        - Params: Dict: An implicit method takes a dictionary of parameters, sends
          the request to the exchange and returns an exchange-specific JSON
          result from the API as is, unparsed.

        To get a list of all available methods with an exchange instance,
        including implicit methods and unified methods you can simply do the
        following:

        print(dir(ccxt.hitbtc()))
        '''
        endpoint_str = endpoint.replace('/', '_')
        endpoint_str = endpoint_str.replace('{', '')
        endpoint_str = endpoint_str.replace('}', '')

        method_str = 'private_' + type.lower() + endpoint_str.lower()

        return self.store.private_end_point(type=type, endpoint=method_str, params=params)
