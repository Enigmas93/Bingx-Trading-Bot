"""Cliente REST para os Perpétuos USDT-M da BingX (Swap V2)."""

import hashlib
import hmac
import math
import threading
import time
from urllib.parse import urlencode

import httpx

from . import settings

LIVE_URL = "https://open-api.bingx.com"
DEMO_URL = "https://open-api-vst.bingx.com"
RECV_WINDOW_MS = 10_000  # tolerância de atraso aceita pela corretora
TIME_SYNC_INTERVAL = 600  # ressincroniza o relógio a cada 10 min


class BingXError(Exception):
    def __init__(self, code, msg: str, path: str = ""):
        super().__init__(f"BingX {path} code={code}: {msg}")
        self.code = code
        self.msg = msg


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class BingXClient:
    def __init__(self, api_key: str, secret_key: str, demo: bool = True, retries: int = 2, read_only: bool = False):
        self.read_only = read_only  # só dados públicos: nunca assina requisição (sem acesso a conta/ordens)
        self.api_key = "" if read_only else api_key
        self.secret_key = "" if read_only else secret_key
        self.base_url = DEMO_URL if demo else LIVE_URL
        self.retries = retries
        self.http = httpx.Client(timeout=15.0)
        self._contracts: dict[str, dict] | None = None
        self._contracts_at = 0.0
        self._lock = threading.Lock()
        self._time_offset_ms = 0  # relógio da BingX - relógio local
        self._time_synced_at = 0.0
        self._hedge_mode: bool | None = None
        self._hedge_checked_at = 0.0

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.secret_key)

    # ------------------------------------------------------------------ base
    def _sign(self, query: str) -> str:
        return hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()

    def sync_time(self) -> int:
        """Mede a diferença entre o relógio local e o da BingX (o PC pode estar adiantado/atrasado)."""
        t0 = time.time()
        resp = self.http.get(f"{self.base_url}/openApi/swap/v2/server/time")
        t1 = time.time()
        server_ms = int(resp.json()["data"]["serverTime"])
        self._time_offset_ms = server_ms - int((t0 + t1) / 2 * 1000)
        self._time_synced_at = t1
        return self._time_offset_ms

    def _timestamp(self) -> int:
        if time.time() - self._time_synced_at > TIME_SYNC_INTERVAL:
            try:
                self.sync_time()
            except Exception:
                pass  # usa o último offset conhecido
        return int(time.time() * 1000) + self._time_offset_ms

    def _request(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if self.read_only:
                raise BingXError("READ_ONLY_CLIENT", "cliente de dados de mercado não acessa conta nem ordens", path)
            if not self.has_credentials:
                raise BingXError("NO_CREDENTIALS", "BINGX_API_KEY/BINGX_SECRET_KEY não configuradas", path)
            headers["X-BX-APIKEY"] = self.api_key

        last_exc: Exception | None = None
        time_resynced = False
        # Retry em erros de API: 1 tentativa + `retries` novas tentativas.
        attempt = 0
        while attempt <= self.retries:
            query_params = dict(params)
            if signed:
                query_params["timestamp"] = self._timestamp()
                query_params["recvWindow"] = RECV_WINDOW_MS
            query = urlencode(sorted(query_params.items()))
            if signed:
                query = f"{query}&signature={self._sign(query)}"
            url = f"{self.base_url}{path}?{query}"
            try:
                resp = self.http.request(method, url, headers=headers)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                payload = resp.json()
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # A requisição nem chegou na corretora: seguro repetir mesmo para ordens.
                last_exc = exc
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if method != "GET":
                    # POST/DELETE pode ter sido processado: não repetir para evitar ordem duplicada.
                    raise BingXError("NETWORK", str(exc), path) from exc
            else:
                code = payload.get("code", 0)
                if code in (0, "0"):
                    return payload.get("data")
                last_exc = BingXError(code, payload.get("msg", ""), path)
                if signed and not time_resynced and "timestamp" in str(last_exc.msg).lower():
                    # Relógio fora de sincronia: a corretora REJEITOU a requisição (nada foi executado),
                    # então é seguro ressincronizar e repetir, inclusive ordens.
                    time_resynced = True
                    try:
                        self.sync_time()
                    except Exception:
                        pass
                    continue
                # Erros de negócio em ordens/configurações não devem ser repetidos.
                if method != "GET":
                    raise last_exc
            if attempt < self.retries:
                time.sleep(0.5 * (attempt + 1))
            attempt += 1
        if isinstance(last_exc, BingXError):
            raise last_exc
        raise BingXError("NETWORK", str(last_exc), path)

    # --------------------------------------------------------------- mercado
    def get_contracts(self) -> dict[str, dict]:
        with self._lock:
            if self._contracts is None or time.time() - self._contracts_at > 3600:
                data = self._request("GET", "/openApi/swap/v2/quote/contracts") or []
                self._contracts = {c["symbol"]: c for c in data if str(c.get("symbol", "")).endswith("-USDT")}
                self._contracts_at = time.time()
            return self._contracts

    def contract(self, symbol: str) -> dict:
        info = self.get_contracts().get(symbol)
        if info is None:
            raise BingXError("SYMBOL", f"Contrato {symbol} não encontrado")
        return info

    def get_tickers(self) -> list[dict]:
        data = self._request("GET", "/openApi/swap/v2/quote/ticker")
        return data if isinstance(data, list) else [data]

    def get_book_top(self, symbol: str) -> tuple[float, float]:
        data = self._request("GET", "/openApi/swap/v2/quote/depth", {"symbol": symbol, "limit": 5})
        bids = [_f(b[0]) for b in data.get("bids", [])]
        asks = [_f(a[0]) for a in data.get("asks", [])]
        if not bids or not asks:
            raise BingXError("DEPTH", f"Livro vazio para {symbol}")
        return max(bids), min(asks)

    def get_funding_rate(self, symbol: str) -> float:
        data = self._request("GET", "/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        if isinstance(data, list):
            data = data[0] if data else {}
        return _f(data.get("lastFundingRate"))

    def get_klines(self, symbol: str, interval: str, limit: int = 200,
                   start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
        data = self._request(
            "GET", "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit, "startTime": start_ms, "endTime": end_ms},
        ) or []
        candles = []
        for k in data:
            if isinstance(k, dict):
                candles.append({
                    "time": int(k["time"]), "open": _f(k["open"]), "high": _f(k["high"]),
                    "low": _f(k["low"]), "close": _f(k["close"]), "volume": _f(k["volume"]),
                })
            else:  # formato em lista
                candles.append({
                    "time": int(k[0]), "open": _f(k[1]), "high": _f(k[2]),
                    "low": _f(k[3]), "close": _f(k[4]), "volume": _f(k[5]),
                })
        candles.sort(key=lambda c: c["time"])
        return candles

    def get_price(self, symbol: str) -> float:
        data = self._request("GET", "/openApi/swap/v2/quote/price", {"symbol": symbol})
        if isinstance(data, list):
            data = data[0] if data else {}
        price = _f(data.get("price"))
        if price <= 0:
            raise BingXError("PRICE", f"Preço inválido para {symbol}")
        return price

    # ----------------------------------------------------------------- conta
    def get_balance(self) -> dict:
        data = self._request("GET", "/openApi/swap/v2/user/balance", signed=True)
        bal = data.get("balance", data) if isinstance(data, dict) else (data[0] if data else {})
        if isinstance(bal, list):
            bal = next((b for b in bal if b.get("asset") == "USDT"), bal[0] if bal else {})
        return {
            "asset": bal.get("asset", "USDT"),
            "balance": _f(bal.get("balance")),
            "equity": _f(bal.get("equity")),
            "available": _f(bal.get("availableMargin")),
            "used_margin": _f(bal.get("usedMargin")),
            "unrealized_pnl": _f(bal.get("unrealizedProfit")),
        }

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        data = self._request("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol}, signed=True) or []
        positions = []
        for p in data:
            amt = _f(p.get("positionAmt"))
            if amt == 0:
                continue
            side = str(p.get("positionSide", "BOTH")).upper()
            direction = side if side in ("LONG", "SHORT") else ("LONG" if amt > 0 else "SHORT")
            positions.append({
                "symbol": p.get("symbol"),
                "direction": direction,
                "position_side": side,
                "quantity": abs(amt),
                "entry_price": _f(p.get("avgPrice")),
                "mark_price": _f(p.get("markPrice")) or None,
                "unrealized_pnl": _f(p.get("unrealizedProfit")),
                "realized_pnl": _f(p.get("realisedProfit")),
                "margin": _f(p.get("initialMargin") or p.get("margin")),
                "leverage": int(_f(p.get("leverage"), 0)) or None,
            })
        return positions

    def get_income(self, symbol: str, start_ms: int, end_ms: int | None = None) -> list[dict]:
        """Lançamentos realizados: REALIZED_PNL, TRADING_FEE, FUNDING_FEE..."""
        data = self._request("GET", "/openApi/swap/v2/user/income",
                             {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
                             signed=True) or []
        return data if isinstance(data, list) else []

    def is_hedge_mode(self) -> bool:
        """Modo de posição da conta (hedge ou one-way), em cache por 10 min.

        Nunca chuta: se a consulta falhar, usa o último valor conhecido; se nunca soube, propaga o erro.
        """
        if self._hedge_mode is not None and time.time() - self._hedge_checked_at < 600:
            return self._hedge_mode
        try:
            data = self._request("GET", "/openApi/swap/v1/positionSide/dual", signed=True) or {}
        except BingXError:
            if self._hedge_mode is not None:
                return self._hedge_mode
            raise
        self._hedge_mode = str(data.get("dualSidePosition", "")).lower() == "true"
        self._hedge_checked_at = time.time()
        return self._hedge_mode

    # ------------------------------------------------- trava de execução DEMO
    def _assert_execution_allowed(self, action: str) -> None:
        """Toda ação que altera posição/ordem só é permitida na conta DEMO.

        Esta versão do sistema é exclusivamente de validação: qualquer tentativa contra a conta real é recusada
        aqui, independentemente de quem chamou (motor novo, código legado ou pesquisa).
        """
        problems = []
        if self.read_only:
            problems.append("cliente somente-leitura de dados de mercado")
        if self.base_url != DEMO_URL:
            problems.append(f"cliente aponta para {self.base_url}")
        if not settings.DEMO_MODE:
            problems.append("DEMO_MODE != true")
        if settings.LIVE_TRADING:
            problems.append("LIVE_TRADING == true")
        if not settings.BINGX_DEMO:
            problems.append("BINGX_TESTNET/BINGX_DEMO != true")
        if problems:
            raise BingXError("LIVE_EXECUTION_BLOCKED", f"{action} recusado: {'; '.join(problems)}", action)

    def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> None:
        self._assert_execution_allowed("marginType")
        self._request("POST", "/openApi/swap/v2/trade/marginType",
                      {"symbol": symbol, "marginType": margin_type}, signed=True)

    def set_leverage(self, symbol: str, side: str, leverage: int) -> None:
        self._assert_execution_allowed("leverage")
        self._request("POST", "/openApi/swap/v2/trade/leverage",
                      {"symbol": symbol, "side": side, "leverage": leverage}, signed=True)

    # ---------------------------------------------------------------- ordens
    def place_order(self, **params) -> dict:
        self._assert_execution_allowed("order")
        data = self._request("POST", "/openApi/swap/v2/trade/order", params, signed=True) or {}
        return data.get("order", data)

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        data = self._request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": symbol}, signed=True) or {}
        orders = data.get("orders", data) if isinstance(data, dict) else data
        return orders or []

    def get_order(self, symbol: str, order_id: str) -> dict:
        data = self._request("GET", "/openApi/swap/v2/trade/order",
                             {"symbol": symbol, "orderId": order_id}, signed=True) or {}
        return data.get("order", data)

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self._assert_execution_allowed("cancel")
        self._request("DELETE", "/openApi/swap/v2/trade/order",
                      {"symbol": symbol, "orderId": order_id}, signed=True)

    # --------------------------------------------------------------- helpers
    def round_qty(self, symbol: str, qty: float) -> float:
        precision = int(_f(self.contract(symbol).get("quantityPrecision"), 4))
        factor = 10 ** precision
        return math.floor(qty * factor + 1e-9) / factor

    def round_price(self, symbol: str, price: float) -> float:
        precision = int(_f(self.contract(symbol).get("pricePrecision"), 4))
        return round(price, precision)

    def min_qty(self, symbol: str, price: float) -> float:
        info = self.contract(symbol)
        min_qty = _f(info.get("tradeMinQuantity"))
        min_usdt = _f(info.get("tradeMinUSDT"))
        if min_usdt and price > 0:
            min_qty = max(min_qty, min_usdt / price)
        return min_qty


_client: BingXClient | None = None
_live_market: BingXClient | None = None


def get_client() -> BingXClient:
    """Cliente da conta configurada (demo ou real): ordens, posições, saldo."""
    global _client
    if _client is None:
        _client = BingXClient(settings.BINGX_API_KEY, settings.BINGX_SECRET_KEY, settings.BINGX_DEMO)
    return _client


def get_market_client(use_live_data: bool = True) -> BingXClient:
    """Cliente para DADOS de mercado. Na demo, com use_live_data, usa os dados públicos da conta real
    (ranking, candles, spread, funding), para a análise ser idêntica à que rodará na conta real."""
    global _live_market
    if not settings.BINGX_DEMO or not use_live_data:
        return get_client()
    if _live_market is None:
        _live_market = BingXClient("", "", demo=False, read_only=True)
    return _live_market


_live_data: BingXClient | None = None


def get_live_data_client() -> BingXClient:
    """Dados de mercado SEMPRE da conta real (endpoint público open-api.bingx.com), sem chaves e somente leitura.
    Usado pelo motor quant para TODA a análise (universo, candles, regime, spread, funding, preço de referência),
    independentemente de a execução ser na demo. Assim, ao migrar para a conta real só a execução muda."""
    global _live_data
    if _live_data is None:
        _live_data = BingXClient("", "", demo=False, read_only=True)
    return _live_data
