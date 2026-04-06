import asyncio
import aiohttp
import logging
from llm.constants import OLLAMA_HOST, MODEL_NAME

logger = logging.getLogger(__name__)

async def check_ollama_server():
    """
    Check if the Ollama server is running and accessible on localhost:11434.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{OLLAMA_HOST}/api/version", timeout=2.0) as response:
                if response.status == 200:
                    return True
                return False
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"Ollama server is not reachable: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error checking Ollama server: {e}")
        return False

async def _llm_chat_with_retry(client, msgs, tools=None, max_retries=3):
    """
    Robust LLM call with auto-retry on Ollama HTTP 500 / malformed XML.
    Qwen3.5:4b-q4_k_m occasionally generates malformed tool-call XML (e.g. <parameter>
    closed by 
    """
    for attempt in range(max_retries):
        try:
            return await client.chat(model=MODEL_NAME, messages=msgs, tools=tools)
        except Exception as e:
            if "500" in str(e) or "xml" in str(e).lower() or "syntax error" in str(e).lower():
                logger.warning(f"Ollama returned error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt == max_retries - 1:  # Last attempt
                    logger.error(f"Ollama failed after {max_retries} attempts: {e}")
                    raise
                # Wait before retrying
                await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
            else:
                # For non-500 errors, don't retry
                logger.error(f"Ollama returned non-retryable error: {e}")
                raise
    # This should never be reached due to the raise in the loop, but just in case
    raise Exception(f"Failed to get response from Ollama after {max_retries} attempts")