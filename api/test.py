# api/test2.py
import asyncio
from mcp_pool import pool as mcp_pool

async def test():
    await mcp_pool.init()

    print("=== 1. Résolution avant création ===")
    raw1 = await mcp_pool.call("actions", "resoudre_tiers", {"code_ou_nom": "PROD-INT"})
    print(raw1)

    print("\n=== 2. Appel assurer_tiers_interne ===")
    raw2 = await mcp_pool.call("actions", "assurer_tiers_interne", {"code_client": "PROD-INT"})
    print(raw2)

    print("\n=== 3. Résolution après création ===")
    raw3 = await mcp_pool.call("actions", "resoudre_tiers", {"code_ou_nom": "PROD-INT"})
    print(raw3)

asyncio.run(test())