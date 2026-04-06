#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pionex API 交易客戶端
=====================
功能：
1. HMAC-SHA256 簽名認證
2. 下單 / 撤單 / 查餘額 / 查持倉
3. 模擬交易模式（預設）
4. 完整的錯誤處理和日誌
"""

import hmac
import hashlib
import time
import json
import requests
from urllib.parse import urlencode
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class PionexClient:
    """Pionex API 客戶端"""

    BASE_URL = "https://api.pionex.com"

    def __init__(self, api_key="", api_secret="", paper_mode=True):
        """
        api_key / api_secret: 從 Pionex 後台取得
        paper_mode: True = 模擬交易（不實際下單），False = 真實交易
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper_mode = paper_mode

        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, allowed_methods=["GET", "POST", "DELETE"])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=3, pool_maxsize=3)
        self.session.mount("https://", adapter)

        # 模擬交易記錄
        self.paper_orders = []
        self.paper_balance = 0  # 初始化時從外部設定

    def _sign(self, method, path, params=None, body=None):
        """生成 HMAC-SHA256 簽名"""
        timestamp = str(int(time.time() * 1000))

        if params is None:
            params = {}
        params["timestamp"] = timestamp

        # 按 key 排序
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        query_string = "&".join(f"{k}={v}" for k, v in sorted_params)

        # 組裝簽名字串
        sign_str = f"{method}{path}?{query_string}"
        if body:
            sign_str += json.dumps(body, separators=(',', ':'))

        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            sign_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return query_string, signature, timestamp

    def _request(self, method, path, params=None, body=None):
        """發送已簽名的 API 請求"""
        if not self.api_key or not self.api_secret:
            return {"error": "API key/secret not configured"}

        query_string, signature, timestamp = self._sign(method, path, params, body)

        headers = {
            "PIONEX-KEY": self.api_key,
            "PIONEX-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

        url = f"{self.BASE_URL}{path}?{query_string}"

        try:
            if method == "GET":
                r = self.session.get(url, headers=headers, timeout=10)
            elif method == "POST":
                r = self.session.post(url, headers=headers, json=body, timeout=10)
            elif method == "DELETE":
                r = self.session.delete(url, headers=headers, json=body, timeout=10)
            else:
                return {"error": f"Unsupported method: {method}"}

            if r.status_code == 200:
                return r.json()
            else:
                return {"error": f"HTTP {r.status_code}", "body": r.text}

        except Exception as e:
            return {"error": str(e)}

    # ===== 公開 API =====

    def get_balance(self):
        """查詢帳戶餘額"""
        if self.paper_mode:
            return {"paper_mode": True, "balance": self.paper_balance}
        return self._request("GET", "/api/v1/account/balances")

    def get_open_orders(self, symbol=None):
        """查詢未成交訂單"""
        if self.paper_mode:
            open_orders = [o for o in self.paper_orders if o["status"] == "open"]
            return {"paper_mode": True, "orders": open_orders}
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/api/v1/trade/orders", params=params)

    # ===== 交易 API =====

    def place_order(self, symbol, side, order_type, size, price=None, client_order_id=None):
        """
        下單
        symbol: e.g. "BTC_USDT_PERP"
        side: "BUY" or "SELL"
        order_type: "MARKET" or "LIMIT"
        size: 下單數量
        price: 限價單價格（市價單不需要）
        """
        body = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "size": str(size),
        }
        if price and order_type == "LIMIT":
            body["price"] = str(price)
        if client_order_id:
            body["clientOrderId"] = client_order_id

        if self.paper_mode:
            # Simulate 0.1% adverse slippage on fill price for paper mode
            fill_price = price
            if fill_price and order_type == "MARKET":
                slippage = 0.001  # 0.1%
                if side == "BUY":
                    fill_price = round(float(fill_price) * (1 + slippage), 8)
                else:
                    fill_price = round(float(fill_price) * (1 - slippage), 8)

            order = {
                "orderId": f"paper_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "size": size,
                "price": fill_price,
                "status": "filled" if order_type == "MARKET" else "open",
                "timestamp": time.time(),
                "paper_mode": True,
            }
            self.paper_orders.append(order)
            return {"result": True, "data": {"orderId": order["orderId"]}, "paper_mode": True}

        return self._request("POST", "/api/v1/trade/order", body=body)

    def cancel_order(self, symbol, order_id):
        """撤單"""
        if self.paper_mode:
            for o in self.paper_orders:
                if o["orderId"] == order_id:
                    o["status"] = "canceled"
            return {"result": True, "paper_mode": True}

        body = {"symbol": symbol, "orderId": order_id}
        return self._request("DELETE", "/api/v1/trade/order", body=body)

    # ===== 便捷方法 =====

    def market_buy(self, symbol, size):
        """市價買入"""
        return self.place_order(symbol, "BUY", "MARKET", size)

    def market_sell(self, symbol, size):
        """市價賣出"""
        return self.place_order(symbol, "SELL", "MARKET", size)

    def limit_buy(self, symbol, size, price):
        """限價買入"""
        return self.place_order(symbol, "BUY", "LIMIT", size, price)

    def limit_sell(self, symbol, size, price):
        """限價賣出"""
        return self.place_order(symbol, "SELL", "LIMIT", size, price)
