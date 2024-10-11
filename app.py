import asyncio
import aiohttp
import json
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.keypair import Keypair
from solana.transaction import Transaction
from base58 import b58decode
import logging
import os
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# Constants
RPC_URL = "https://api.mainnet-beta.solana.com"  # Solana mainnet
PRICE_API_URL = "https://price.jup.ag/v6/price"
QUOTE_API_URL = "https://quote-api.jup.ag/v6/quote"
SWAP_API_URL = "https://quote-api.jup.ag/v6/swap"

# Get the private key from environment variable
PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")

if not PRIVATE_KEY:
    raise ValueError("SOLANA_PRIVATE_KEY environment variable is not set")

# Initialize wallet
try:
    wallet = Keypair.from_secret_key(b58decode(PRIVATE_KEY))
except ValueError as e:
    raise ValueError(f"Invalid private key: {e}")

# Initialize Solana client
client = AsyncClient(RPC_URL, commitment=Confirmed)

# Enable simulation mode
SIMULATION_MODE = True

# Transaction limits
MAX_TRANSACTIONS_PER_HOUR = 10
MAX_TRANSACTION_AMOUNT = 1_000_000_000  # 1 SOL in lamports

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Adjustable arbitrage threshold
ARBITRAGE_THRESHOLD = 1.005  # 0.5% profit threshold

# Transaction counter
transaction_count = 0
last_transaction_time = None

async def get_prices(tokens):
    try:
        async with aiohttp.ClientSession() as session:
            params = {"ids": ",".join(tokens)}
            async with session.get(PRICE_API_URL, params=params) as response:
                response.raise_for_status()
                return await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching prices: {e}")
        raise

async def get_quote(input_mint, output_mint, amount):
    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": 50
            }
            async with session.get(QUOTE_API_URL, params=params) as response:
                response.raise_for_status()
                return await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching quote: {e}")
        raise

async def execute_swap(quote_response):
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "quoteResponse": quote_response,
                "userPublicKey": str(wallet.public_key),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto"
            }
            async with session.post(SWAP_API_URL, json=payload) as response:
                response.raise_for_status()
                swap_response = await response.json()
                return swap_response["swapTransaction"]
    except aiohttp.ClientError as e:
        logger.error(f"Error executing swap: {e}")
        raise

async def send_transaction(swap_transaction):
    global transaction_count, last_transaction_time
    current_time = asyncio.get_event_loop().time()

    if last_transaction_time and current_time - last_transaction_time < 3600:
        if transaction_count >= MAX_TRANSACTIONS_PER_HOUR:
            logger.warning("Hourly transaction limit reached. Skipping transaction.")
            return None

    if SIMULATION_MODE:
        logger.info(f"Simulated transaction: {swap_transaction[:64]}...")
        return "simulated_signature"
    else:
        try:
            transaction = Transaction.deserialize(bytes.fromhex(swap_transaction))
            transaction.sign(wallet)
            signature = await client.send_transaction(transaction, wallet)
            transaction_count += 1
            last_transaction_time = current_time
            logger.info(f"Transaction sent. Signature: {signature}")
            return signature
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            raise

async def check_arbitrage_opportunity(token_a, token_b, token_c):
    try:
        prices = await get_prices([token_a, token_b, token_c])
        
        price_a = float(prices["data"][token_a]["price"])
        price_b = float(prices["data"][token_b]["price"])
        price_c = float(prices["data"][token_c]["price"])
        
        logger.info(f"Current prices: {token_a}: {price_a}, {token_b}: {price_b}, {token_c}: {price_c}")
        
        price_a_to_b = price_a / price_b
        price_b_to_c = price_b / price_c
        price_c_to_a = price_c / price_a
        
        logger.info(f"Exchange rates: {token_a}->{token_b}: {price_a_to_b}, {token_b}->{token_c}: {price_b_to_c}, {token_c}->{token_a}: {price_c_to_a}")
        
        arbitrage_ratio = price_a_to_b * price_b_to_c * price_c_to_a
        
        logger.info(f"Arbitrage ratio: {arbitrage_ratio}")
        
        if arbitrage_ratio > ARBITRAGE_THRESHOLD:
            logger.info(f"Arbitrage opportunity found: {arbitrage_ratio}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error checking arbitrage opportunity: {e}")
        raise

async def execute_arbitrage(token_a, token_b, token_c, amount):
    if amount > MAX_TRANSACTION_AMOUNT:
        logger.warning(f"Transaction amount {amount} exceeds maximum allowed {MAX_TRANSACTION_AMOUNT}. Reducing amount.")
        amount = MAX_TRANSACTION_AMOUNT

    logger.info(f"Executing arbitrage: {token_a} -> {token_b} -> {token_c} -> {token_a}")
    try:
        # A -> B
        quote_a_to_b = await get_quote(token_a, token_b, amount)
        swap_tx_a_to_b = await execute_swap(quote_a_to_b)
        await send_transaction(swap_tx_a_to_b)
        
        # B -> C
        amount_b = quote_a_to_b["outAmount"]
        quote_b_to_c = await get_quote(token_b, token_c, amount_b)
        swap_tx_b_to_c = await execute_swap(quote_b_to_c)
        await send_transaction(swap_tx_b_to_c)
        
        # C -> A
        amount_c = quote_b_to_c["outAmount"]
        quote_c_to_a = await get_quote(token_c, token_a, amount_c)
        swap_tx_c_to_a = await execute_swap(quote_c_to_a)
        await send_transaction(swap_tx_c_to_a)

        logger.info("Arbitrage execution complete")
    except Exception as e:
        logger.error(f"Error executing arbitrage: {e}")
        raise

async def main():
    token_a = "So11111111111111111111111111111111111111112"  # SOL
    token_b = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    token_c = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"  # USDT
    
    initial_amount = 1_000_000_000  # 1 SOL in lamports
    
    max_iterations = 20  # Increased to 20 iterations for more data
    iteration = 0
    while iteration < max_iterations:
        try:
            logger.info(f"Iteration {iteration + 1}/{max_iterations}")
            if await check_arbitrage_opportunity(token_a, token_b, token_c):
                await execute_arbitrage(token_a, token_b, token_c, initial_amount)
            await asyncio.sleep(1)  # Wait for 1 second before checking again
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            await asyncio.sleep(5)  # Wait for 5 seconds before retrying
        iteration += 1
    logger.info("Simulation complete")

if __name__ == "__main__":
    asyncio.run(main())
