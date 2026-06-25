import re
import sys

def extract_messages(log_file_path, output_file_path):
    with open(log_file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    with open(output_file_path, 'w', encoding='utf-8') as out:
        for line in lines:
            # We look for "- User: " or "- Agent: " in the line
            match = re.search(r'-\s+(User:.*?)$', line)
            if match:
                out.write(match.group(1).strip() + '\n')
                continue
            
            match = re.search(r'-\s+(Agent:.*?)$', line)
            if match:
                out.write(match.group(1).strip() + '\n')
                continue

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python extract_messages.py <input_file> <output_file>")
        print("Example: python extract_messages.py temp.txt messages.txt")
        sys.exit(1)
        
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    extract_messages(input_file, output_file)
    print(f"Messages extracted successfully to {output_file}")
