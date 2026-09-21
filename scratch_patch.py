import os

file_path = 'api/mcp_actions_sage.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

def replace_function(lines, func_name, new_func_code):
    start_idx = -1
    for i, line in enumerate(lines):
        if line.startswith(f"def {func_name}("):
            start_idx = i
            break
            
    if start_idx == -1:
        print(f"Function {func_name} not found.")
        return lines
        
    end_idx = start_idx
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith("def ") or lines[i].startswith("# ──"):
            # Check if it's really the next function
            if lines[i].startswith("def ") and lines[i-1].strip() == "":
                end_idx = i
                break
            if lines[i].startswith("# ──") and lines[i+1].startswith("# "):
                end_idx = i
                break
    
    # ensure we got an end_idx, else to end of file
    if end_idx == start_idx:
        end_idx = len(lines)
        
    new_lines = new_func_code.split('\n')
    new_lines = [l + '\n' for l in new_lines]
    
    print(f"Replacing {func_name} from line {start_idx} to {end_idx}")
    return lines[:start_idx] + new_lines + lines[end_idx:]

with open('patch_workflow.py', 'w', encoding='utf-8') as f:
    f.write("print('ready')")
