import json

with open("backend/vendor_report_claude.json") as f:
    generated = json.load(f)

# Build the new JS block
new_block = '  if (q.includes("vendor and po")) {\n    return '
new_block += json.dumps(generated, indent=4)
new_block += ';\n  }\n'

with open("frontend-react/src/hardcodedReports.js", "r") as f:
    content = f.read()

# Find the start of the vendor and po block
start_str = '  if (q.includes("vendor and po")) {'
start_idx = content.find(start_str)

# Find the start of the next block
end_str = '  if (q.includes("monthly revenue")) {'
end_idx = content.find(end_str)

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + new_block + '\n' + content[end_idx:]
    with open("frontend-react/src/hardcodedReports.js", "w") as f:
        f.write(new_content)
    print("Replaced successfully")
else:
    print(f"Could not find blocks. Start: {start_idx}, End: {end_idx}")
