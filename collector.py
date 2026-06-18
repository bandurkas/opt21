import asyncio
import websockets
from curl_cffi.requests import AsyncSession
import aiosqlite
import json
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('collector')

DB_FILE = "data.sqlite"
bybit_cache = {}

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        with open('db_schema.sql', 'r') as f:
            schema = f.read()
        await db.executescript(schema)
        await db.commit()

# Removed Bybit WS Listener

async def fetch_derive_instruments(client):
    url = "https://api.lyra.finance/public/get_instruments"
    payload = {"currency": "ETH", "instrument_type": "option", "expired": False}
    try:
        response = await client.post(url, json=payload)
        data = response.json()
        return data.get('result', [])
    except Exception as e:
        logger.error(f"Failed to fetch derive instruments: {e}")
        return []

async def fetch_derive_ticker(client, instrument_name):
    url = "https://api.lyra.finance/public/get_ticker"
    payload = {"instrument_name": instrument_name}
    try:
        response = await client.post(url, json=payload)
        data = response.json()
        return data.get('result', {})
    except Exception as e:
        logger.error(f"Failed to fetch derive ticker for {instrument_name}: {e}")
        return None

async def fetch_aevo_markets(client):
    url = "https://api.aevo.xyz/markets?asset=ETH&instrument_type=OPTION"
    try:
        response = await client.get(url)
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch aevo markets: {e}")
        return []

async def fetch_aevo_orderbook(client, instrument_name):
    url = f"https://api.aevo.xyz/orderbook?instrument_name={instrument_name}"
    try:
        response = await client.get(url)
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch aevo orderbook for {instrument_name}: {e}")
        return None

async def save_to_db(records):
    if not records:
        return
    query = """
    INSERT INTO options_data (
        timestamp, exchange, symbol, underlying_price, strike, expiry, option_type,
        mark_price, iv, delta, gamma, vega, theta, volume, open_interest,
        bid_1, ask_1, bid_1_vol, ask_1_vol, orderbook_bids, orderbook_asks
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?
    )
    """
    
    tuples = []
    for r in records:
        tuples.append((
            r['timestamp'].isoformat(), r['exchange'], r['symbol'], r['underlying_price'], r['strike'], 
            r['expiry'].isoformat() if r['expiry'] else None, r['option_type'], r['mark_price'], r['iv'], r['delta'], 
            r['gamma'], r['vega'], r['theta'], r['volume'], r['open_interest'],
            r['bid_1'], r['ask_1'], r['bid_1_vol'], r['ask_1_vol'], 
            json.dumps(r.get('orderbook_bids', [])), json.dumps(r.get('orderbook_asks', []))
        ))
    
    async with aiosqlite.connect(DB_FILE) as db:
        await db.executemany(query, tuples)
        await db.commit()
    logger.info(f"Saved {len(records)} records to DB.")

async def collect_bybit(client):
    logger.info("Fetching Bybit instruments via REST...")
    url = "https://api.bytick.com/v5/market/tickers?category=option&baseCoin=ETH"
    try:
        response = await client.get(url)
        data = response.json()
        items = data.get('result', {}).get('list', [])
    except Exception as e:
        logger.error(f"Failed to fetch Bybit options: {e}")
        return []
        
    now = datetime.now(timezone.utc)
    records = []
    
    for item in items:
        symbol = item.get('symbol')
        parts = symbol.split('-')
        if len(parts) < 4:
            continue
        
        strike = float(parts[2])
        option_type = parts[3]
        
        def pf(val):
            try: return float(val)
            except: return None
        
        try:
            date_str = parts[1].title() # 24MAY24 -> 24May24
            expiry_date = datetime.strptime(date_str, '%d%b%y').replace(tzinfo=timezone.utc)
        except:
            expiry_date = None

        record = {
            'timestamp': now,
            'exchange': 'BYBIT',
            'symbol': symbol,
            'underlying_price': pf(item.get('underlyingPrice')),
            'strike': strike,
            'expiry': expiry_date,
            'option_type': option_type,
            'mark_price': pf(item.get('markPrice')),
            'iv': pf(item.get('markIv')),
            'delta': pf(item.get('delta')),
            'gamma': pf(item.get('gamma')),
            'vega': pf(item.get('vega')),
            'theta': pf(item.get('theta')),
            'volume': pf(item.get('volume24h')),
            'open_interest': pf(item.get('openInterest')),
            'bid_1': pf(item.get('bid1Price')),
            'ask_1': pf(item.get('ask1Price')),
            'bid_1_vol': pf(item.get('bid1Size')),
            'ask_1_vol': pf(item.get('ask1Size')),
            'orderbook_bids': [], # Top of book only
            'orderbook_asks': []
        }
        
        # Only add valid quotes
        if record['bid_1'] is not None or record['ask_1'] is not None:
            records.append(record)
            
    logger.info(f"Processed {len(records)} Bybit records from REST.")
    return records

async def collect_derive(client):
    logger.info("Fetching Derive instruments...")
    instruments = await fetch_derive_instruments(client)
    if not instruments:
        logger.warning("No Derive instruments found.")
        return []

    now = datetime.now(timezone.utc)
    logger.info(f"Processing {len(instruments)} Derive instruments...")

    semaphore = asyncio.Semaphore(5)

    async def process_instrument(inst):
        instrument_name = inst.get('instrument_name', '')
        if not instrument_name.startswith('ETH'):
            return None
            
        parts = instrument_name.split('-')
        if len(parts) != 4:
            return None
            
        strike = float(parts[2])
        option_type = parts[3]

        async with semaphore:
            ticker = await fetch_derive_ticker(client, instrument_name)
            await asyncio.sleep(0.1) # Rate limit

        if not ticker: return None
        
        def pf(val):
            try: return float(val)
            except: return None
            
        op = ticker.get('option_pricing', {})
        
        record = {
            'timestamp': now,
            'exchange': 'DERIVE',
            'symbol': instrument_name,
            'underlying_price': pf(ticker.get('index_price')),
            'strike': strike,
            'expiry': datetime.fromtimestamp(inst['option_details']['expiry'], tz=timezone.utc) if inst.get('option_details') else None,
            'option_type': option_type,
            'mark_price': pf(ticker.get('mark_price')),
            'iv': pf(op.get('iv')),
            'delta': pf(op.get('delta')),
            'gamma': pf(op.get('gamma')),
            'vega': pf(op.get('vega')),
            'theta': pf(op.get('theta')),
            'volume': None,
            'open_interest': None,
            'bid_1': pf(ticker.get('best_bid_price')),
            'ask_1': pf(ticker.get('best_ask_price')),
            'bid_1_vol': pf(ticker.get('best_bid_amount')),
            'ask_1_vol': pf(ticker.get('best_ask_amount')),
            'orderbook_bids': [],
            'orderbook_asks': []
        }
        return record

    tasks = [process_instrument(inst) for inst in instruments]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]

async def collect_aevo(client):
    logger.info("Fetching Aevo markets...")
    markets = await fetch_aevo_markets(client)
    if not markets:
        logger.warning("No Aevo markets found.")
        return []

    now = datetime.now(timezone.utc)
    logger.info(f"Processing {len(markets)} Aevo markets...")

    semaphore = asyncio.Semaphore(5)

    async def process_market(m):
        instrument_name = m.get('instrument_name', '')
        if not instrument_name.startswith('ETH'):
            return None
            
        parts = instrument_name.split('-')
        if len(parts) != 4:
            return None
            
        strike = float(parts[2])
        option_type = parts[3]

        async with semaphore:
            ob = await fetch_aevo_orderbook(client, instrument_name)
            await asyncio.sleep(0.1) # Rate limit

        if not ob: return None
        
        bids = ob.get('bids', [])
        asks = ob.get('asks', [])
        
        def pf(val):
            try: return float(val)
            except: return None
            
        greeks = m.get('greeks', {})
        
        record = {
            'timestamp': now,
            'exchange': 'AEVO',
            'symbol': instrument_name,
            'underlying_price': pf(m.get('index_price')),
            'strike': strike,
            'expiry': datetime.fromtimestamp(int(m.get('expiry', 0)) / 1e9, tz=timezone.utc) if m.get('expiry') else None,
            'option_type': option_type,
            'mark_price': pf(m.get('mark_price')),
            'iv': pf(greeks.get('iv')),
            'delta': pf(greeks.get('delta')),
            'gamma': pf(greeks.get('gamma')),
            'vega': pf(greeks.get('vega')),
            'theta': pf(greeks.get('theta')),
            'volume': None,
            'open_interest': None,
            'bid_1': pf(bids[0][0]) if bids else None,
            'ask_1': pf(asks[0][0]) if asks else None,
            'bid_1_vol': pf(bids[0][1]) if bids else None,
            'ask_1_vol': pf(asks[0][1]) if asks else None,
            'orderbook_bids': bids[:5],
            'orderbook_asks': asks[:5]
        }
        return record

    tasks = [process_market(m) for m in markets]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]


async def main():
    await init_db()
    logger.info("Database initialized")
    
    # (Removed Bybit websocket listener)

    async with AsyncSession(impersonate="chrome110", verify=False) as client:
        while True:
            logger.info("Starting collection cycle...")
            try:
                # Fetch Bybit via REST
                bybit_records = await collect_bybit(client)
                derive_records = await collect_derive(client)
                aevo_records = await collect_aevo(client)
                
                all_records = bybit_records + derive_records + aevo_records
                await save_to_db(all_records)
            except Exception as e:
                logger.error(f"Error in collection cycle: {e}")
            
            logger.info("Sleeping for 60 seconds...")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
