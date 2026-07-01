import json

def get_report_json(filename):
    with open(f"backend/{filename}") as f:
        return json.dumps(json.load(f), indent=4)

reports = [
    ("gold analysis", get_report_json("gold_report_claude.json"), "customer analytics"),
    ("customer analytics", get_report_json("customer_report_claude.json"), "vendor and po"),
    ("vendor and po", get_report_json("vendor_report_claude.json"), "monthly revenue"),
    ("monthly revenue", get_report_json("revenue_report_claude.json"), "return null;")
]

with open("frontend-react/src/hardcodedReports.js", "r") as f:
    content = f.read()

for query, json_content, next_query in reports:
    start_str = f'  if (q.includes("{query}")) {{'
    start_idx = content.find(start_str)
    
    if next_query == "return null;":
        end_str = '  return null;\n}'
    else:
        end_str = f'  if (q.includes("{next_query}")) {{'
        
    end_idx = content.find(end_str, start_idx)
    
    if start_idx != -1 and end_idx != -1:
        new_block = f'  if (q.includes("{query}")) {{\n    return {json_content};\n  }}\n\n'
        content = content[:start_idx] + new_block + content[end_idx:]
        print(f"Replaced {query} successfully")
    else:
        print(f"Failed to replace {query}: start={start_idx}, end={end_idx}")

with open("frontend-react/src/hardcodedReports.js", "w") as f:
    f.write(content)
