import asyncio
import json
from ai.claude_multi_agent import ClaudeReportPipeline

async def main():
    pipeline = ClaudeReportPipeline()
    result = pipeline.generate("Give vendor and PO report", force_refresh=True)
    with open("vendor_report_claude.json", "w") as f:
        json.dump(result, f, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
