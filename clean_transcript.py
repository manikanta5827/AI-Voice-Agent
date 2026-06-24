import re

def clean_telugu_transcript(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Remove line numbers and timestamps
        # Pattern: number: timestamp text
        # Timestamps like: 0:033 seconds, 1:201 minute, 20 seconds, 2:012 minutes, 1 second
        line = re.sub(r'^\d+:\d+\s*(?:seconds?|minutes?(?:,\s*\d+\s*seconds?)?)\s*', '', line)
        
        # Remove English characters and punctuation, keep only Telugu
        # Telugu Unicode range: \u0C00-\u0C7F
        # Also keep spaces and common punctuation that might be part of Telugu text
        cleaned = re.sub(r'[a-zA-Z0-9\[\]\(\)%$#@!&*+=/\\<>{}~`|^_]', ' ', line)
        
        # Remove multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # Skip empty lines or lines with only punctuation/spaces
        if cleaned and not re.match(r'^[\s\.\,\?\!\-\:]+$', cleaned):
            cleaned_lines.append(cleaned)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(cleaned_lines))
    
    print(f"Cleaned {len(cleaned_lines)} lines. Output saved to {output_file}")

if __name__ == "__main__":
    clean_telugu_transcript("temp2.txt", "cleaned_telugu2.txt")