"""
Order Book Analyzer — Fetches and analyzes Binance order book data.

Provides market microstructure signals:
- Bid/Ask imbalance
- Order book depth (liquidity)
- Spread analysis
- Whale order detection
"""
import requests
import numpy as np
from typing import Dict, Optional, List, Tuple


class OrderBookAnalyzer:
    """Fetches and analyzes order book data from Binance public API.

    Usage:
        ob = OrderBookAnalyzer(symbol='BTCUSDT', depth=20)
        if ob.fetch():
            imbalance = ob.get_imbalance()
            spread = ob.get_spread_pct()
            walls = ob.get_wall_detection()
    """

    def __init__(self, symbol: str = 'BTCUSDT', depth: int = 20, timeout: int = 10):
        """Initialize the order book analyzer.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT')
            depth: Number of price levels to fetch (max 5000)
            timeout: Request timeout in seconds
        """
        self.symbol = symbol.upper()
        self.depth = min(depth, 5000)
        self.timeout = timeout
        self.base_url = "https://api.binance.com/api/v3/depth"
        self.book: Dict = {'bids': [], 'asks': []}
        self._last_update_id: int = 0

    def fetch(self) -> Optional[Dict]:
        """Fetch current order book from Binance public API.

        Returns:
            dict with 'bids' and 'asks' keys, each a list of [price, qty] strings,
            or None if the request fails.
        """
        try:
            params = {
                'symbol': self.symbol,
                'limit': self.depth,
            }
            response = requests.get(self.base_url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if 'bids' in data and 'asks' in data:
                self.book = data
                self._last_update_id = data.get('lastUpdateId', 0)
                return self.book
            else:
                return None

        except requests.exceptions.RequestException:
            return None
        except ValueError:
            return None

    def get_imbalance(self) -> float:
        """Calculate bid/ask volume imbalance ratio.

        Returns:
            Float in range [-1, 1]:
            > 0 means buying pressure (more bids than asks)
            < 0 means selling pressure (more asks than bids)
            0 means balanced
            Returns 0.0 if order book is empty.
        """
        if not self.book['bids'] or not self.book['asks']:
            return 0.0

        total_bids = sum(float(b[1]) for b in self.book['bids'])
        total_asks = sum(float(a[1]) for a in self.book['asks'])

        total = total_bids + total_asks
        if total == 0:
            return 0.0

        return (total_bids - total_asks) / total

    def get_imbalance_at_price_levels(self, levels: int = 5) -> float:
        """Calculate imbalance using only the top N levels.

        Args:
            levels: Number of top levels to consider

        Returns:
            Imbalance ratio using top N levels.
        """
        if not self.book['bids'] or not self.book['asks']:
            return 0.0

        n_bids = min(len(self.book['bids']), levels)
        n_asks = min(len(self.book['asks']), levels)

        total_bids = sum(float(self.book['bids'][i][1]) for i in range(n_bids))
        total_asks = sum(float(self.book['asks'][i][1]) for i in range(n_asks))

        total = total_bids + total_asks
        if total == 0:
            return 0.0

        return (total_bids - total_asks) / total

    def get_spread_pct(self) -> float:
        """Calculate the best bid/ask spread as a percentage.

        Returns:
            Spread percentage relative to best bid.
            Returns 0.0 if order book is empty.
        """
        if not self.book['bids'] or not self.book['asks']:
            return 0.0

        best_bid = float(self.book['bids'][0][0])
        best_ask = float(self.book['asks'][0][0])

        if best_bid <= 0:
            return 0.0

        return (best_ask - best_bid) / best_bid * 100.0

    def get_spread_absolute(self) -> float:
        """Calculate the absolute spread (best ask - best bid).

        Returns:
            Absolute spread value. Returns 0.0 if order book is empty.
        """
        if not self.book['bids'] or not self.book['asks']:
            return 0.0

        best_bid = float(self.book['bids'][0][0])
        best_ask = float(self.book['asks'][0][0])

        return best_ask - best_bid

    def get_mid_price(self) -> float:
        """Calculate the mid price (average of best bid and best ask).

        Returns:
            Mid price. Returns 0.0 if order book is empty.
        """
        if not self.book['bids'] or not self.book['asks']:
            return 0.0

        best_bid = float(self.book['bids'][0][0])
        best_ask = float(self.book['asks'][0][0])

        return (best_bid + best_ask) / 2.0

    def get_wall_detection(self, multiplier: float = 5.0) -> Dict:
        """Detect large order walls (orders > multiplier * average size).

        An order wall is a single price level with significantly larger
        quantity than the average, indicating strong support/resistance.

        Args:
            multiplier: Threshold multiplier above average (default 5x)

        Returns:
            dict with:
                'bid_walls': list of (price, qty, ratio) for large bid walls
                'ask_walls': list of (price, qty, ratio) for large ask walls
                'has_bid_wall': bool
                'has_ask_wall': bool
        """
        result: Dict = {
            'bid_walls': [],
            'ask_walls': [],
            'has_bid_wall': False,
            'has_ask_wall': False,
        }

        if not self.book['bids'] or not self.book['asks']:
            return result

        # Calculate average sizes
        bid_quantities = [float(b[1]) for b in self.book['bids']]
        ask_quantities = [float(a[1]) for a in self.book['asks']]

        avg_bid_qty = np.mean(bid_quantities) if bid_quantities else 0
        avg_ask_qty = np.mean(ask_quantities) if ask_quantities else 0

        # Detect bid walls
        if avg_bid_qty > 0:
            for b in self.book['bids']:
                price = float(b[0])
                qty = float(b[1])
                ratio = qty / avg_bid_qty
                if ratio >= multiplier:
                    result['bid_walls'].append((price, qty, ratio))

        # Detect ask walls
        if avg_ask_qty > 0:
            for a in self.book['asks']:
                price = float(a[0])
                qty = float(a[1])
                ratio = qty / avg_ask_qty
                if ratio >= multiplier:
                    result['ask_walls'].append((price, qty, ratio))

        result['has_bid_wall'] = len(result['bid_walls']) > 0
        result['has_ask_wall'] = len(result['ask_walls']) > 0

        return result

    def get_liquidity_score(self) -> float:
        """Calculate a 0-1 score representing market liquidity.

        Factors considered:
        - Spread tightness (tighter = more liquid)
        - Order book depth (more depth = more liquid)
        - Balance between bid and ask sides

        Returns:
            Liquidity score between 0 (illiquid) and 1 (highly liquid).
        """
        if not self.book['bids'] or not self.book['asks']:
            return 0.0

        # Spread score: 0-1, higher for tighter spreads
        spread_pct = self.get_spread_pct()
        # Typical crypto spreads: 0.01% = excellent, 1% = poor
        spread_score = max(0.0, 1.0 - spread_pct / 0.5)

        # Depth score: total volume in the order book
        total_depth = sum(float(b[1]) for b in self.book['bids']) + \
                      sum(float(a[1]) for a in self.book['asks'])
        # Normalize: assume 1000 BTC depth is excellent
        depth_score = min(1.0, total_depth / 1000.0)

        # Balance score: 1 = perfectly balanced
        imbalance = abs(self.get_imbalance())
        balance_score = 1.0 - imbalance

        # Weighted combination
        score = 0.4 * spread_score + 0.4 * depth_score + 0.2 * balance_score
        return max(0.0, min(1.0, score))

    def get_depth_profile(self) -> Dict:
        """Get cumulative depth at various price distances from mid.

        Returns:
            dict with cumulative bid/ask volumes at 0.1%, 0.5%, 1%, 2%, 5% distance.
        """
        if not self.book['bids'] or not self.book['asks']:
            return {}

        mid = self.get_mid_price()
        if mid <= 0:
            return {}

        levels = [0.001, 0.005, 0.01, 0.02, 0.05]  # 0.1%, 0.5%, 1%, 2%, 5%
        profile: Dict[str, Dict] = {}

        for level_pct in levels:
            price_distance = mid * level_pct
            bid_threshold = mid - price_distance
            ask_threshold = mid + price_distance

            bid_volume = sum(
                float(b[1]) for b in self.book['bids']
                if float(b[0]) >= bid_threshold
            )
            ask_volume = sum(
                float(a[1]) for a in self.book['asks']
                if float(a[0]) <= ask_threshold
            )

            label = f"{level_pct*100:.1f}%"
            profile[label] = {
                'bid_volume': round(bid_volume, 4),
                'ask_volume': round(ask_volume, 4),
                'imbalance': round((bid_volume - ask_volume) / (bid_volume + ask_volume), 4)
                if (bid_volume + ask_volume) > 0 else 0.0,
            }

        return profile

    def get_slippage_estimate(self, order_size: float) -> Dict:
        """Estimate slippage for a market order of given size.

        Walks the order book to find the weighted average execution price.

        Args:
            order_size: Order size in base currency units

        Returns:
            dict with:
                'avg_price': weighted average execution price
                'slippage_pct': slippage from mid price as percentage
                'filled': whether the order can be fully filled
                'levels_consumed': number of price levels consumed
        """
        if not self.book['bids'] or not self.book['asks']:
            return {'avg_price': 0.0, 'slippage_pct': 0.0, 'filled': False, 'levels_consumed': 0}

        remaining = order_size
        total_cost = 0.0
        levels_consumed = 0

        for ask in self.book['asks']:
            price = float(ask[0])
            qty = float(ask[1])
            levels_consumed += 1

            if qty >= remaining:
                total_cost += remaining * price
                remaining = 0
                break
            else:
                total_cost += qty * price
                remaining -= qty

        filled = remaining == 0
        avg_price = total_cost / (order_size - remaining) if order_size > remaining else total_cost / order_size
        mid = self.get_mid_price()

        slippage_pct = (avg_price - mid) / mid * 100.0 if mid > 0 else 0.0

        return {
            'avg_price': round(avg_price, 8),
            'slippage_pct': round(slippage_pct, 4),
            'filled': filled,
            'levels_consumed': levels_consumed,
        }

    def get_summary(self) -> Dict:
        """Get a comprehensive summary of the current order book state.

        Returns:
            dict with all key metrics.
        """
        if not self.book['bids'] or not self.book['asks']:
            return {'error': 'Order book is empty — call fetch() first'}

        best_bid = float(self.book['bids'][0][0])
        best_ask = float(self.book['asks'][0][0])
        walls = self.get_wall_detection()

        return {
            'symbol': self.symbol,
            'best_bid': best_bid,
            'best_ask': best_ask,
            'mid_price': self.get_mid_price(),
            'spread_pct': round(self.get_spread_pct(), 4),
            'spread_absolute': round(self.get_spread_absolute(), 8),
            'imbalance': round(self.get_imbalance(), 4),
            'imbalance_top5': round(self.get_imbalance_at_price_levels(5), 4),
            'liquidity_score': round(self.get_liquidity_score(), 4),
            'has_bid_wall': walls['has_bid_wall'],
            'has_ask_wall': walls['has_ask_wall'],
            'bid_wall_count': len(walls['bid_walls']),
            'ask_wall_count': len(walls['ask_walls']),
        }
