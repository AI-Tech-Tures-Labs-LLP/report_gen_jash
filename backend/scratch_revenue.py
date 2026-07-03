import asyncio
import json
from ai.claude_multi_agent import ClaudeReportPipeline

async def main():
    pipeline = ClaudeReportPipeline()
    result = pipeline.generate("Give monthly revenue trends report", force_refresh=True)
    with open("revenue_report_claude.json", "w") as f:
        json.dump(result, f, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
