import sys
import os
sys.path.insert(0, os.path.abspath('C:/Users/Aya/Documents/agent_sage'))
from api.mcp_nl2sql import verifier_stock_article
import asyncio
print(verifier_stock_article('CHORFA'))
